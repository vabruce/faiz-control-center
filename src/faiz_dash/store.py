"""Central read-only snapshot store with atomic cache persistence."""

from __future__ import annotations

from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Optional

from .models import (
    ColabGcpSnapshot,
    GceInstanceInfo,
    MailboxItem,
    MailboxSnapshot,
    ModelBucketQuota,
    ProcessChannelInfo,
    QuotaSnapshot,
    TokenBurnStats,
    sanitize_for_json,
)

DEFAULT_CACHE_DIR = Path.home() / ".agents" / "cache"
MAX_CACHE_BYTES = 240_000
WINDOW_5H_SECONDS = 5 * 3600
WINDOW_WEEKLY_SECONDS = 7 * 86400
MIN_FORECAST_ELAPSED_SEC = 600


def summarize_probe_error(err_text: Optional[str], cooldown_sec: int = 0) -> str:
    """Distill noisy rclone/probe tracebacks into crisp single-line diagnostics."""
    raw = (err_text or "").strip()
    cd_tag = ""
    if cooldown_sec > 0:
        mins, secs = divmod(cooldown_sec, 60)
        cd_tag = f" (cooldown {mins}m{secs:02d}s)" if mins > 0 else f" (cooldown {secs}s)"
    if not raw:
        return f"Drive backoff active{cd_tag}" if cd_tag else ""
    upper = raw.upper()
    if "RATE_LIMIT" in upper or "RATELIMIT" in upper or "403" in upper:
        return f"Drive 403 RATE_LIMIT{cd_tag}"
    if "TIMED OUT" in upper or "TIMEOUT" in upper:
        return f"rclone timeout{cd_tag}"
    lines = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and "NOTICE: gdrive:" not in ln and "shared Google Drive client_id" not in ln
    ]
    core = lines[-1] if lines else raw.splitlines()[0].strip()
    core = re.sub(r"^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+(?:CRITICAL|ERROR|NOTICE):\s*", "", core)
    if len(core) > 56:
        core = core[:53] + "..."
    return f"{core}{cd_tag}"


def read_backoff_remaining(backoff_path: Optional[Path] = None) -> int:
    """Return remaining Drive cooldown seconds from cloud_backoff.json, or 0."""
    target = backoff_path or (DEFAULT_CACHE_DIR / "cloud_backoff.json")
    if not target.exists():
        return 0
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        next_ep = float(data.get("next_allowed_epoch") or 0.0)
        return max(0, int(next_ep - time.time()))
    except Exception:
        return 0


def compute_quota_gate(quota: QuotaSnapshot) -> str:
    """Compute advisory quota gate signal: GREEN, THROTTLE_ARGON, or WARN_PRO."""
    pro_low = False
    argon_low = False
    for bucket in quota.buckets:
        name = bucket.group_name.upper()
        if "GEMINI MODELS" in name:
            for pct in (bucket.five_hour_pct, bucket.weekly_pct):
                if pct is not None and pct < 20.0:
                    pro_low = True
        elif "ARGON" in name:
            for pct in (bucket.weekly_pct, bucket.five_hour_pct):
                if pct is not None and pct < 20.0:
                    argon_low = True
    if pro_low:
        return "WARN_PRO"
    if argon_low:
        return "THROTTLE_ARGON"
    return "GREEN"


def atomic_write_json(path: Path, payload: dict[str, Any], max_bytes: int = MAX_CACHE_BYTES) -> None:
    """Write sanitized JSON atomically via NamedTemporaryFile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_payload = sanitize_for_json(payload)
    encoded = json.dumps(clean_payload, ensure_ascii=False, indent=2)
    if len(encoded.encode("utf-8")) > max_bytes:
        encoded = json.dumps(clean_payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"Cache payload for {path.name} exceeds {max_bytes} bytes")

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(encoded)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, str(path))
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


_REFRESH_DUR_RE = re.compile(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", re.IGNORECASE)


def parse_refresh_seconds(refresh_text: str) -> Optional[int]:
    """Parse `/usage` strings like 'Refreshes in 6d 23h 31m', '3h 32m', or '45m' to remaining seconds."""
    text = (refresh_text or "").strip()
    if not text or "available" in text.lower():
        return None
    tail = text.rsplit("in", 1)[-1] if " in " in text.lower() else text
    for m in _REFRESH_DUR_RE.finditer(tail):
        d_s, h_s, m_s = m.group(1), m.group(2), m.group(3)
        if d_s is not None or h_s is not None or m_s is not None:
            days = int(d_s or 0)
            hours = int(h_s or 0)
            mins = int(m_s or 0)
            return days * 86400 + hours * 3600 + mins * 60
    return None


def remaining_reset_seconds(
    refresh_text: str,
    anchor_epoch: int,
    now_epoch: Optional[float] = None,
) -> Optional[int]:
    """Resolve remaining seconds until quota reset, adjusted by anchor timestamp when available."""
    secs = parse_refresh_seconds(refresh_text)
    if secs is None:
        return None
    if anchor_epoch > 0:
        now_ts = time.time() if now_epoch is None else float(now_epoch)
        return max(0, (anchor_epoch + secs) - int(now_ts))
    return secs


def compute_quota_pace(
    remaining_pct: Optional[float],
    reset_seconds: Optional[int],
    window_seconds: int,
) -> Optional[dict[str, Any]]:
    """Compute burn pace vs elapsed window timeline and run-out forecast."""
    if (
        remaining_pct is None
        or reset_seconds is None
        or window_seconds <= 0
        or reset_seconds < 0
        or reset_seconds > window_seconds
    ):
        return None
    rem = max(0.0, min(100.0, float(remaining_pct)))
    used_pct = 100.0 - rem
    elapsed_sec = window_seconds - int(reset_seconds)
    elapsed_pct = (elapsed_sec / window_seconds) * 100.0
    pace_delta_pct = int(round(used_pct - elapsed_pct))
    if pace_delta_pct >= 5:
        status = "over"
    elif pace_delta_pct <= -5:
        status = "under"
    else:
        status = "on_pace"

    runs_out_seconds: Optional[int] = None
    if elapsed_sec >= MIN_FORECAST_ELAPSED_SEC and 0.0 < rem < 100.0 and used_pct > elapsed_pct:
        est = int((rem * elapsed_sec) / used_pct)
        if 0 < est < reset_seconds:
            runs_out_seconds = est

    return {
        "elapsed_pct": round(elapsed_pct, 1),
        "used_pct": round(used_pct, 1),
        "pace_delta_pct": pace_delta_pct,
        "status": status,
        "runs_out_seconds": runs_out_seconds,
    }


def _bucket_pcts(quota: QuotaSnapshot) -> dict[str, Any]:
    pro_5h: Optional[float] = None
    pro_wk: Optional[float] = None
    pro_5h_ref = ""
    pro_wk_ref = ""
    claude_5h: Optional[float] = None
    claude_wk: Optional[float] = None
    argon_wk: Optional[float] = None
    for b in quota.buckets:
        upper = b.group_name.upper()
        if "GEMINI MODELS" in upper:
            pro_5h = b.five_hour_pct
            pro_wk = b.weekly_pct
            pro_5h_ref = b.five_hour_refresh
            pro_wk_ref = b.weekly_refresh
        elif "CLAUDE" in upper:
            claude_5h = b.five_hour_pct
            claude_wk = b.weekly_pct
        elif "ARGON" in upper:
            argon_wk = b.weekly_pct

    anchor = quota.pty_fetched_epoch if quota.pty_fetched_epoch > 0 else int(time.time())
    pro_5h_reset_at: Optional[int] = None
    if "available" in pro_5h_ref.lower():
        pro_5h_reset_at = 0
    else:
        sec_5h = parse_refresh_seconds(pro_5h_ref)
        if sec_5h is not None:
            pro_5h_reset_at = anchor + sec_5h

    pro_wk_reset_at: Optional[int] = None
    if "available" in pro_wk_ref.lower():
        pro_wk_reset_at = 0
    else:
        sec_wk = parse_refresh_seconds(pro_wk_ref)
        if sec_wk is not None:
            pro_wk_reset_at = anchor + sec_wk

    return {
        "pro_5h_pct": pro_5h,
        "pro_wk_pct": pro_wk,
        "claude_5h_pct": claude_5h,
        "claude_wk_pct": claude_wk,
        "argon_wk_pct": argon_wk,
        "pro_5h_refresh_text": pro_5h_ref,
        "pro_5h_reset_at_epoch": pro_5h_reset_at,
        "pro_wk_reset_at_epoch": pro_wk_reset_at,
    }


Subscriber = Callable[[str], Any]


class StateStore:
    """Holds latest snapshots, persists local cache files, and notifies subscribers."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.usage_cache_path = self.cache_dir / "usage_snapshot.json"
        self.cloud_cache_path = self.cache_dir / "cloud_snapshot.json"
        self.quota: Optional[QuotaSnapshot] = None
        self.cloud: Optional[ColabGcpSnapshot] = None
        self.mailbox: Optional[MailboxSnapshot] = None
        self._seed_cloud_payload: dict[str, Any] = {}
        self._load_existing_cloud_seed()
        self.hydrate_from_cache()
        self._subscribers: list[Subscriber] = []

    def _load_existing_cloud_seed(self) -> None:
        if not self.cloud_cache_path.exists():
            return
        try:
            data = json.loads(self.cloud_cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._seed_cloud_payload = data
        except Exception:
            self._seed_cloud_payload = {}

    @staticmethod
    def _decode_mailbox_item(raw: dict[str, Any]) -> MailboxItem:
        return MailboxItem(
            name=str(raw.get("name", "")),
            queue=str(raw.get("queue", "")),
            size=int(raw.get("size") or 0),
            modified_at=str(raw.get("modified_at", "")),
            is_tts=bool(raw.get("is_tts", False)),
            verified=raw.get("verified"),
            verify_detail=str(raw.get("verify_detail", "")),
            log_tail=str(raw.get("log_tail", "")),
        )

    def hydrate_from_cache(self) -> None:
        """Populate in-memory snapshots from on-disk cache files for 0ms TUI cold start."""
        if self.usage_cache_path.exists():
            try:
                u = json.loads(self.usage_cache_path.read_text(encoding="utf-8"))
                if isinstance(u, dict) and u.get("fetched_at"):
                    buckets = tuple(
                        ModelBucketQuota(
                            group_name=str(b.get("group_name", "")),
                            models=tuple(str(m) for m in (b.get("models") or ())),
                            weekly_pct=b.get("weekly_pct"),
                            weekly_refresh=str(b.get("weekly_refresh", "")),
                            five_hour_pct=b.get("five_hour_pct"),
                            five_hour_refresh=str(b.get("five_hour_refresh", "")),
                        )
                        for b in (u.get("buckets") or ())
                        if isinstance(b, dict)
                    )
                    ch = u.get("channels") or {}
                    br = u.get("burn") or {}
                    self.quota = QuotaSnapshot(
                        fetched_at=str(u.get("fetched_at", "")),
                        account=str(u.get("account", "")),
                        buckets=buckets,
                        channels=ProcessChannelInfo(
                            pro_count=int(ch.get("pro_count") or u.get("pro_count") or 0),
                            gcp_count=int(ch.get("gcp_count") or u.get("gcp_count") or 0),
                            quota_project=str(ch.get("quota_project", "")),
                            active_auth_method=str(ch.get("active_auth_method", "")),
                        ),
                        burn=TokenBurnStats(
                            tokens_1h=int(br.get("tokens_1h") or u.get("tokens_1h") or 0),
                            tokens_24h=int(br.get("tokens_24h") or u.get("tokens_24h") or 0),
                            cache_hit_rate=float(br.get("cache_hit_rate") or u.get("cache_hit_rate") or 0.0),
                            active_sessions=int(br.get("active_sessions") or 0),
                            by_model_24h={
                                str(k): int(v) for k, v in (br.get("by_model_24h") or {}).items()
                            },
                        ),
                        quota_gate=str(u.get("quota_gate", "GREEN")),
                        source="cache",
                        pty_fetched_epoch=int(u.get("pty_fetched_epoch") or 0),
                        error=u.get("error"),
                        stale=bool(u.get("stale", False)),
                    )
            except Exception:
                pass

        c = self._seed_cloud_payload
        if c and c.get("fetched_at"):
            try:
                gces = tuple(
                    GceInstanceInfo(
                        name=str(g.get("name", "")),
                        zone=str(g.get("zone", "")),
                        status=str(g.get("status", "")),
                        machine_type=str(g.get("machine_type", "")),
                        created_at=str(g.get("created_at", "")),
                    )
                    for g in (c.get("gce_instances") or ())
                    if isinstance(g, dict)
                )
                self.cloud = ColabGcpSnapshot(
                    fetched_at=str(c.get("fetched_at", "")),
                    colab_state=str(c.get("colab_state", "UNKNOWN")),
                    raw_status=str(c.get("raw_status", "")),
                    project=str(c.get("project", "")),
                    mode=str(c.get("mode", "")),
                    reason=str(c.get("reason", "")),
                    updated_at=str(c.get("updated_at", "")),
                    age_seconds=c.get("age_seconds"),
                    idle_remaining_seconds=c.get("idle_remaining_seconds"),
                    webui_url=str(c.get("webui_url", "")),
                    gce_instances=gces,
                    drive_used_bytes=c.get("drive_used_bytes"),
                    drive_total_bytes=c.get("drive_total_bytes"),
                    drive_free_bytes=c.get("drive_free_bytes"),
                    rclone_readonly=bool(c.get("rclone_readonly", False)),
                    rclone_shared_client_id=bool(c.get("rclone_shared_client_id", False)),
                    drive_backend=str(c.get("drive_backend", "rclone")),
                    colab_sessions_count=c.get("colab_sessions_count"),
                    colab_gpu=str(c.get("colab_gpu", "")),
                    colab_source=str(c.get("colab_source", "drive")),
                    error=c.get("error"),
                    stale=bool(c.get("stale", False)),
                )
            except Exception:
                pass

            mbox = c.get("mailbox")
            if isinstance(mbox, dict) and mbox.get("fetched_at"):
                try:
                    self.mailbox = MailboxSnapshot(
                        fetched_at=str(mbox.get("fetched_at", "")),
                        tasks_count=int(mbox.get("tasks_count") or 0),
                        done_count=int(mbox.get("done_count") or 0),
                        failed_count=int(mbox.get("failed_count") or 0),
                        tasks=tuple(self._decode_mailbox_item(x) for x in (mbox.get("tasks") or ()) if isinstance(x, dict)),
                        done_items=tuple(self._decode_mailbox_item(x) for x in (mbox.get("done_items") or ()) if isinstance(x, dict)),
                        failed_items=tuple(self._decode_mailbox_item(x) for x in (mbox.get("failed_items") or ()) if isinstance(x, dict)),
                        error=mbox.get("error"),
                        stale=bool(mbox.get("stale", False)),
                    )
                except Exception:
                    pass

    def subscribe(self, callback: Subscriber) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify(self, topic: str) -> None:
        for cb in list(self._subscribers):
            res = cb(topic)
            if inspect.isawaitable(res):
                await res

    def _build_usage_payload(self, quota: QuotaSnapshot) -> dict[str, Any]:
        data = sanitize_for_json(quota)
        data.update(_bucket_pcts(quota))
        data["pro_count"] = quota.channels.pro_count
        data["gcp_count"] = quota.channels.gcp_count
        data["tokens_1h"] = quota.burn.tokens_1h
        data["tokens_24h"] = quota.burn.tokens_24h
        data["cache_hit_rate"] = quota.burn.cache_hit_rate
        return data

    def _build_cloud_payload(self) -> dict[str, Any]:
        cloud = self.cloud
        mailbox = self.mailbox
        payload: dict[str, Any] = dict(self._seed_cloud_payload) if self._seed_cloud_payload else {}
        payload["fetched_at"] = cloud.fetched_at if cloud else (mailbox.fetched_at if mailbox else payload.get("fetched_at", ""))
        if cloud is not None:
            cloud_dict = sanitize_for_json(cloud)
            if cloud.stale and payload:
                for key in (
                    "raw_status",
                    "project",
                    "mode",
                    "reason",
                    "updated_at",
                    "webui_url",
                ):
                    if not cloud_dict.get(key) and payload.get(key):
                        cloud_dict[key] = payload[key]
                for key in (
                    "age_seconds",
                    "idle_remaining_seconds",
                    "drive_used_bytes",
                    "drive_total_bytes",
                    "drive_free_bytes",
                ):
                    if cloud_dict.get(key) is None and payload.get(key) is not None:
                        cloud_dict[key] = payload[key]
                if not cloud_dict.get("gce_instances") and payload.get("gce_instances"):
                    cloud_dict["gce_instances"] = payload["gce_instances"]
            cloud_dict["gce_running_count"] = sum(
                1 for inst in (cloud_dict.get("gce_instances") or [])
                if str(inst.get("status", "")).upper() in {"RUNNING", "STAGING"}
            )
            err_upper = (cloud.error or "").upper()
            ctrl_confirmed = cloud.colab_source in {"colab_cli", "hybrid"} and cloud.colab_state in {"ONLINE", "STOPPED"}
            cloud_ok = (ctrl_confirmed or not cloud.stale) and cloud.colab_state != "DEGRADED"
            cloud_error_kind = (
                "rate_limit"
                if ("RATE_LIMIT" in err_upper or "RATELIMIT" in err_upper)
                else ("timeout" if "TIMED OUT" in err_upper or "TIMEOUT" in err_upper else ("error" if cloud.error else ""))
            )
            payload.update(cloud_dict)
            payload["colab_gcp"] = cloud_dict
            payload["cloud_ok"] = cloud_ok
            payload["cloud_error_kind"] = cloud_error_kind
        if mailbox is not None:
            prev_mbox = payload.get("mailbox") if isinstance(payload.get("mailbox"), dict) else {}
            has_prev_mbox = bool(
                prev_mbox
                and (
                    not prev_mbox.get("stale", False)
                    or prev_mbox.get("tasks_count", 0) > 0
                    or prev_mbox.get("done_count", 0) > 0
                    or prev_mbox.get("failed_count", 0) > 0
                    or prev_mbox.get("done_items")
                    or prev_mbox.get("failed_items")
                )
            )
            if mailbox.stale and has_prev_mbox:
                mbox_dict = {
                    **prev_mbox,
                    "fetched_at": mailbox.fetched_at,
                    "error": sanitize_for_json(mailbox.error),
                    "stale": True,
                }
                payload["mailbox"] = mbox_dict
                payload["mailbox_tasks_count"] = prev_mbox.get("tasks_count", 0)
                payload["mailbox_done_count"] = prev_mbox.get("done_count", 0)
                payload["mailbox_failed_count"] = prev_mbox.get("failed_count", 0)
                payload["mailbox_unverified_count"] = prev_mbox.get("unverified_count", 0)
            else:
                unverified = sum(1 for item in mailbox.done_items if item.verified is False)
                mbox_dict = {
                    "fetched_at": mailbox.fetched_at,
                    "tasks_count": mailbox.tasks_count,
                    "done_count": mailbox.done_count,
                    "failed_count": mailbox.failed_count,
                    "unverified_count": unverified,
                    "tasks": sanitize_for_json(mailbox.tasks[:10]),
                    "done_items": sanitize_for_json(mailbox.done_items[:10]),
                    "failed_items": sanitize_for_json(mailbox.failed_items[:5]),
                    "error": sanitize_for_json(mailbox.error),
                    "stale": mailbox.stale,
                }
                payload["mailbox"] = mbox_dict
                payload["mailbox_tasks_count"] = mailbox.tasks_count
                payload["mailbox_done_count"] = mailbox.done_count
                payload["mailbox_failed_count"] = mailbox.failed_count
                payload["mailbox_unverified_count"] = unverified
        self._seed_cloud_payload = payload
        return payload

    async def publish_quota(self, quota: QuotaSnapshot) -> QuotaSnapshot:
        gate = compute_quota_gate(quota)
        pty_ep = quota.pty_fetched_epoch
        fetched_at = quota.fetched_at
        if pty_ep <= 0 and quota.source == "cache" and self.usage_cache_path.exists():
            try:
                old = json.loads(self.usage_cache_path.read_text(encoding="utf-8"))
                pty_ep = int(old.get("pty_fetched_epoch") or self.usage_cache_path.stat().st_mtime)
                if old.get("fetched_at"):
                    fetched_at = str(old["fetched_at"])
            except Exception:
                pass
        normalized = replace(quota, quota_gate=gate, pty_fetched_epoch=pty_ep, fetched_at=fetched_at)
        self.quota = normalized
        atomic_write_json(self.usage_cache_path, self._build_usage_payload(normalized))
        await self._notify("quota")
        return normalized

    async def publish_cloud_and_mailbox(
        self, cloud: ColabGcpSnapshot, mailbox: MailboxSnapshot
    ) -> tuple[ColabGcpSnapshot, MailboxSnapshot]:
        """Publish cloud and mailbox snapshots together in a single atomic cache write."""
        self.cloud = cloud
        self.mailbox = mailbox
        atomic_write_json(self.cloud_cache_path, self._build_cloud_payload())
        await self._notify("cloud")
        await self._notify("mailbox")
        return cloud, mailbox

    async def publish_cloud(self, cloud: ColabGcpSnapshot) -> ColabGcpSnapshot:
        self.cloud = cloud
        atomic_write_json(self.cloud_cache_path, self._build_cloud_payload())
        await self._notify("cloud")
        return cloud

    async def publish_mailbox(self, mailbox: MailboxSnapshot) -> MailboxSnapshot:
        self.mailbox = mailbox
        atomic_write_json(self.cloud_cache_path, self._build_cloud_payload())
        await self._notify("mailbox")
        return mailbox

    def to_combined_dict(self) -> dict[str, Any]:
        return {
            "quota": self._build_usage_payload(self.quota) if self.quota else None,
            "cloud": self._build_cloud_payload() if (self.cloud or self.mailbox) else None,
        }

"""Unit tests for StateStore atomic cache persistence, quota_gate, and LayoutManager."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from faiz_dash.models import (
    ColabGcpSnapshot,
    MailboxSnapshot,
    ModelBucketQuota,
    QuotaSnapshot,
)
from faiz_dash.statusline_config import (
    get_cloud_settings,
    load_statusline_config,
    read_auto_dock_enabled,
    save_statusline_config,
    write_auto_dock_enabled,
)
from faiz_dash.store import StateStore, compute_quota_gate, parse_refresh_seconds, summarize_probe_error
from faiz_dash.ui.layout import LayoutManager


class TestLayoutAndStore(unittest.IsolatedAsyncioTestCase):
    async def test_store_quota_gate_and_atomic_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(cache_dir=Path(tmp))

            q_throttle = QuotaSnapshot(
                fetched_at="2026-11-18T10:00:00Z",
                account="user@example.com",
                buckets=(
                    ModelBucketQuota("GEMINI MODELS", weekly_pct=90.0, five_hour_pct=65.0),
                    ModelBucketQuota("CLAUDE AND GPT MODELS", weekly_pct=85.17, five_hour_pct=70.35),
                    ModelBucketQuota("GEMINI 4 ARGON", weekly_pct=13.7),
                ),
            )
            published = await store.publish_quota(q_throttle)
            self.assertEqual(published.quota_gate, "THROTTLE_ARGON")
            u_json = json.loads(store.usage_cache_path.read_text(encoding="utf-8"))
            self.assertAlmostEqual(u_json["claude_5h_pct"], 70.35)
            self.assertAlmostEqual(u_json["claude_wk_pct"], 85.17)

            q_warn = QuotaSnapshot(
                fetched_at="2026-11-18T10:00:00Z",
                buckets=(
                    ModelBucketQuota("GEMINI MODELS", weekly_pct=80.0, five_hour_pct=12.0),
                    ModelBucketQuota("GEMINI 4 ARGON", weekly_pct=10.0),
                ),
            )
            self.assertEqual(compute_quota_gate(q_warn), "WARN_PRO")

            q_green = QuotaSnapshot(
                fetched_at="2026-11-18T10:00:00Z",
                buckets=(
                    ModelBucketQuota("GEMINI MODELS", weekly_pct=80.0, five_hour_pct=55.0),
                    ModelBucketQuota("GEMINI 4 ARGON", weekly_pct=45.0),
                ),
            )
            self.assertEqual(compute_quota_gate(q_green), "GREEN")

            await store.publish_cloud(
                ColabGcpSnapshot(
                    fetched_at="2026-11-18T10:00:00Z",
                    colab_state="ONLINE",
                    error="token sk-secret123456789",
                )
            )
            await store.publish_mailbox(
                MailboxSnapshot(fetched_at="2026-11-18T10:00:00Z", tasks_count=1, done_count=2, failed_count=0)
            )

            self.assertTrue(store.usage_cache_path.exists())
            self.assertTrue(store.cloud_cache_path.exists())
            total_size = store.usage_cache_path.stat().st_size + store.cloud_cache_path.stat().st_size
            self.assertLess(total_size, 256 * 1024)

            cloud_raw = store.cloud_cache_path.read_text(encoding="utf-8")
            self.assertNotIn("sk-secret123456789", cloud_raw)
            cloud_json = json.loads(cloud_raw)
            self.assertEqual(cloud_json["colab_state"], "ONLINE")
            self.assertEqual(cloud_json["mailbox_tasks_count"], 1)

    def test_layout_manager_operations_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout_file = Path(tmp) / "tui_layout.json"
            lm = LayoutManager(layout_path=layout_file)
            self.assertEqual(lm.effective_preset(140), 1)
            self.assertEqual(lm.effective_preset(46), 2)

            lm.set_preset(2)
            lm.toggle_zoom("colab")
            lm.adjust_fr("quota", 0.3)
            lm.toggle_visible("mailbox")
            lm.swap_with_next("quota")

            lm2 = LayoutManager(layout_path=layout_file)
            lm2.load()
            self.assertEqual(lm2.preset, 2)
            self.assertEqual(lm2.zoomed_panel, "colab")
            self.assertAlmostEqual(lm2.panels["quota"].fr, 1.3)
            self.assertFalse(lm2.panels["mailbox"].visible)
            ordered = [c.panel_id for c in lm2.ordered_panels(visible_only=True)]
            self.assertEqual(ordered, ["colab", "quota"])

    async def test_store_preserves_seed_mailbox_when_publishing_cloud_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            store1 = StateStore(cache_dir=cache_dir)
            await store1.publish_cloud_and_mailbox(
                ColabGcpSnapshot(fetched_at="2026-11-18T10:00:00Z", colab_state="ONLINE"),
                MailboxSnapshot(fetched_at="2026-11-18T10:00:01Z", tasks_count=1, done_count=3, failed_count=1),
            )

            # New StateStore simulating `--once-json` reload before mailbox finishes
            store2 = StateStore(cache_dir=cache_dir)
            await store2.publish_cloud(
                ColabGcpSnapshot(
                    fetched_at="2026-11-18T10:05:00Z",
                    colab_state="DEGRADED",
                    error="403 RATE_LIMIT_EXCEEDED",
                    stale=True,
                )
            )
            data = json.loads((cache_dir / "cloud_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(data["colab_state"], "DEGRADED")
            self.assertFalse(data["cloud_ok"])
            self.assertEqual(data["cloud_error_kind"], "rate_limit")
            self.assertEqual(data["mailbox_tasks_count"], 1)
            self.assertEqual(data["mailbox_failed_count"], 1)

    async def test_store_preserves_pty_fetched_epoch_on_cache_republish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            store1 = StateStore(cache_dir=cache_dir)
            await store1.publish_quota(
                QuotaSnapshot(
                    fetched_at="2026-11-18T10:00:00Z",
                    source="pty",
                    pty_fetched_epoch=1795000000,
                    buckets=(
                        ModelBucketQuota("GEMINI MODELS", weekly_pct=72.1, five_hour_pct=90.5),
                        ModelBucketQuota("GEMINI 4 ARGON", weekly_pct=13.8),
                    ),
                )
            )
            # Subsequent `--no-pty` publish uses source="cache" and pty_fetched_epoch=0;
            # store must NOT overwrite the real PTY age or fetched_at timestamp.
            store2 = StateStore(cache_dir=cache_dir)
            await store2.publish_quota(
                QuotaSnapshot(
                    fetched_at="2026-11-18T10:05:00Z",
                    source="cache",
                    pty_fetched_epoch=0,
                    buckets=(
                        ModelBucketQuota("GEMINI MODELS", weekly_pct=72.1, five_hour_pct=90.5),
                        ModelBucketQuota("GEMINI 4 ARGON", weekly_pct=13.8),
                    ),
                )
            )
            data = json.loads((cache_dir / "usage_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(data["pty_fetched_epoch"], 1795000000)
            self.assertEqual(data["fetched_at"], "2026-11-18T10:00:00Z")

    async def test_parse_refresh_seconds_and_reset_epoch_export(self) -> None:
        self.assertEqual(parse_refresh_seconds("Refreshes in 6d 23h 31m"), 6 * 86400 + 23 * 3600 + 31 * 60)
        self.assertEqual(parse_refresh_seconds("Refreshes in 3h 32m"), 3 * 3600 + 32 * 60)
        self.assertEqual(parse_refresh_seconds("Refreshes in 45m"), 45 * 60)
        self.assertIsNone(parse_refresh_seconds("Quota available"))
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            store = StateStore(cache_dir=cache_dir)
            await store.publish_quota(
                QuotaSnapshot(
                    fetched_at="2026-11-18T10:00:00Z",
                    source="pty",
                    pty_fetched_epoch=1795000000,
                    buckets=(
                        ModelBucketQuota("GEMINI MODELS", weekly_pct=62.8, five_hour_pct=60.5, five_hour_refresh="Refreshes in 3h 32m"),
                    ),
                )
            )
            data = json.loads((cache_dir / "usage_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(data["pro_5h_reset_at_epoch"], 1795000000 + 3 * 3600 + 32 * 60)
            self.assertEqual(data["pro_5h_refresh_text"], "Refreshes in 3h 32m")

    def test_statusline_config_load_save_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "statusline_config.json"
            cfg = load_statusline_config(cfg_path)
            self.assertEqual(cfg["language"], "en")
            self.assertEqual(cfg["style"], "capsule")
            self.assertEqual(cfg["indicator"], "ring")
            self.assertEqual(cfg["density"], "auto")
            self.assertEqual(cfg["secondary_metric"], "burn")
            self.assertEqual(cfg["theme"], "google")
            self.assertEqual(cfg["color_mode"], "row")
            self.assertEqual(cfg["row_colors"], ["blue", "blue", "blue", "blue"])
            self.assertEqual(cfg["capsule_colors"]["gemini"], "auto")
            self.assertEqual(cfg["capsule_colors"]["mailbox"], "auto")
            self.assertTrue(cfg["top_gap"])
            self.assertTrue(cfg["line_gap"])
            self.assertTrue(cfg["auto_wrap"])
            self.assertTrue(cfg["modules"]["cache"])
            self.assertTrue(cfg["modules"]["speed"])
            self.assertTrue(cfg["modules"]["mailbox"])
            self.assertEqual(cfg["control_center"], {"side_ratio": 28, "dock_ratio": 22})

            cfg["language"] = "zh"
            cfg["style"] = "pill"
            cfg["indicator"] = "bar"
            cfg["density"] = "compact"
            cfg["secondary_metric"] = "reset"
            cfg["theme"] = "catppuccin"
            cfg["color_mode"] = "semantic"
            cfg["row_colors"] = ["red", "blue"]
            cfg["top_gap"] = False
            cfg["line_gap"] = False
            cfg["modules"]["cache"] = False
            cfg["modules"]["sessions"] = False
            cfg["colors"]["context"] = "magenta"
            cfg["capsule_colors"]["gemini"] = "green"
            cfg["capsule_colors"]["mailbox"] = "red"
            cfg["control_center"] = {"side_ratio": 32, "dock_ratio": 26}
            save_statusline_config(cfg, cfg_path)

            reloaded = load_statusline_config(cfg_path)
            self.assertEqual(reloaded["control_center"], {"side_ratio": 32, "dock_ratio": 26})

            # Out-of-bounds or boolean ratios must fall back to safe defaults
            cfg["control_center"] = {"side_ratio": 99, "dock_ratio": True}
            bounded = save_statusline_config(cfg, cfg_path)
            self.assertEqual(bounded["control_center"], {"side_ratio": 28, "dock_ratio": 22})

            flag_path = Path(tmp) / "cache" / "dash_disabled"
            self.assertTrue(read_auto_dock_enabled(flag_path))
            write_auto_dock_enabled(False, flag_path)
            self.assertFalse(read_auto_dock_enabled(flag_path))
            self.assertTrue(flag_path.exists())
            write_auto_dock_enabled(True, flag_path)
            self.assertTrue(read_auto_dock_enabled(flag_path))
            self.assertFalse(flag_path.exists())
            self.assertEqual(reloaded["language"], "zh")
            self.assertEqual(reloaded["style"], "pill")
            self.assertEqual(reloaded["indicator"], "bar")
            self.assertEqual(reloaded["density"], "compact")
            self.assertEqual(reloaded["secondary_metric"], "reset")
            self.assertEqual(reloaded["theme"], "catppuccin")
            self.assertEqual(reloaded["color_mode"], "semantic")
            self.assertEqual(reloaded["row_colors"], ["red", "blue"])
            self.assertFalse(reloaded["top_gap"])
            self.assertFalse(reloaded["line_gap"])
            self.assertFalse(reloaded["modules"]["cache"])
            self.assertFalse(reloaded["modules"]["sessions"])
            self.assertEqual(reloaded["colors"]["context"], "magenta")
            self.assertEqual(reloaded["capsule_colors"]["gemini"], "green")
            self.assertEqual(reloaded["capsule_colors"]["mailbox"], "red")

            import os

            old_proj = os.environ.get("FAIZ_GCP_PROJECT")
            os.environ["FAIZ_GCP_PROJECT"] = "custom-oss-proj"
            try:
                cloud_cfg = get_cloud_settings(cfg_path)
                self.assertEqual(cloud_cfg["gcp_project"], "custom-oss-proj")
            finally:
                if old_proj is None:
                    os.environ.pop("FAIZ_GCP_PROJECT", None)
                else:
                    os.environ["FAIZ_GCP_PROJECT"] = old_proj

    async def test_store_preserves_cloud_and_mailbox_across_consecutive_stale_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            store1 = StateStore(cache_dir=cache_dir)
            await store1.publish_cloud_and_mailbox(
                ColabGcpSnapshot(
                    fetched_at="2026-11-18T10:00:00Z",
                    colab_state="ONLINE",
                    raw_status="ONLINE",
                    project="demo_swarm",
                    mode="tts_only",
                    idle_remaining_seconds=2200,
                ),
                MailboxSnapshot(
                    fetched_at="2026-11-18T10:00:01Z",
                    tasks_count=1,
                    done_count=17,
                    failed_count=2,
                ),
            )

            # Consecutive stale publish #1 and #2 from fresh short-lived StateStore instances
            for ts in ("2026-11-18T10:05:00Z", "2026-11-18T10:10:00Z"):
                st = StateStore(cache_dir=cache_dir)
                await st.publish_cloud_and_mailbox(
                    ColabGcpSnapshot(
                        fetched_at=ts,
                        colab_state="DEGRADED",
                        error="403 RATE_LIMIT_EXCEEDED",
                        stale=True,
                    ),
                    MailboxSnapshot(fetched_at=ts, error="403 RATE_LIMIT_EXCEEDED", stale=True),
                )

            data = json.loads((cache_dir / "cloud_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(data["colab_state"], "DEGRADED")
            self.assertEqual(data["project"], "demo_swarm")
            self.assertEqual(data["raw_status"], "ONLINE")
            self.assertEqual(data["idle_remaining_seconds"], 2200)
            self.assertEqual(data["mailbox_tasks_count"], 1)
            self.assertEqual(data["mailbox_done_count"], 17)
            self.assertEqual(data["mailbox_failed_count"], 2)

    async def test_summarize_probe_error_and_hydrate_from_cache(self) -> None:
        verbose_rclone = (
            "Colab: 2026/11/18 22:13:06 NOTICE: gdrive: This remote uses rclone's shared client_id\n"
            "2026/11/18 22:13:07 CRITICAL: googleapi: Error 403: Quota exceeded, RATE_LIMIT_EXCEEDED"
        )
        self.assertEqual(
            summarize_probe_error(verbose_rclone, cooldown_sec=135),
            "Drive 403 RATE_LIMIT (cooldown 2m15s)",
        )
        self.assertEqual(summarize_probe_error("rclone timed out after 12.0s"), "rclone timeout")

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            s1 = StateStore(cache_dir=cache_dir)
            await s1.publish_quota(
                QuotaSnapshot(
                    fetched_at="2026-11-18T10:00:00Z",
                    account="dev@example.com",
                    buckets=(ModelBucketQuota("GEMINI MODELS", weekly_pct=70.0, five_hour_pct=60.0),),
                )
            )
            await s1.publish_cloud_and_mailbox(
                ColabGcpSnapshot(fetched_at="2026-11-18T10:00:01Z", colab_state="ONLINE", project="ProjX"),
                MailboxSnapshot(fetched_at="2026-11-18T10:00:02Z", tasks_count=1, done_count=4, failed_count=2),
            )

            # New StateStore immediately hydrates all 3 snapshots from cache files at 0ms init
            s2 = StateStore(cache_dir=cache_dir)
            self.assertIsNotNone(s2.quota)
            self.assertEqual(s2.quota.account, "dev@example.com")  # type: ignore[union-attr]
            self.assertIsNotNone(s2.cloud)
            self.assertEqual(s2.cloud.project, "ProjX")  # type: ignore[union-attr]
            self.assertIsNotNone(s2.mailbox)
            self.assertEqual(s2.mailbox.failed_count, 2)  # type: ignore[union-attr]

    async def test_store_control_plane_cloud_ok_and_gdrive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            store = StateStore(cache_dir=cache_dir)
            await store.publish_cloud(
                ColabGcpSnapshot(
                    fetched_at="2026-11-23T08:30:00Z",
                    colab_state="STOPPED",
                    drive_backend="gdrive",
                    colab_sessions_count=0,
                    colab_gpu="",
                    colab_source="colab_cli",
                    error="Drive: 403 RATE_LIMIT_EXCEEDED",
                    stale=False,
                )
            )
            data = json.loads((cache_dir / "cloud_snapshot.json").read_text(encoding="utf-8"))
            self.assertTrue(data["cloud_ok"])
            self.assertEqual(data["drive_backend"], "gdrive")
            self.assertEqual(data["colab_source"], "colab_cli")
            self.assertEqual(data["colab_sessions_count"], 0)
            self.assertFalse(data["rclone_shared_client_id"])

            reloaded = StateStore(cache_dir=cache_dir)
            self.assertIsNotNone(reloaded.cloud)
            self.assertEqual(reloaded.cloud.drive_backend, "gdrive")  # type: ignore[union-attr]
            self.assertEqual(reloaded.cloud.colab_source, "colab_cli")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()

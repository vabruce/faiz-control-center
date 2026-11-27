"""StatusLine and cloud topology configuration schema, persistence, and env helpers."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Optional

from .store import atomic_write_json

DEFAULT_STATUSLINE_CONFIG_PATH = Path.home() / ".agents" / "statusline_config.json"

DEFAULT_SIDE_RATIO = 28
DEFAULT_DOCK_RATIO = 22
SIDE_RATIO_BOUNDS = (18, 45)
DOCK_RATIO_BOUNDS = (12, 40)
VALID_SIDE_RATIOS = (22, 25, 28, 32, 35)
VALID_DOCK_RATIOS = (18, 22, 26, 30)
VALID_MAX_WIDTHS = (0, 80, 90, 100, 120)

MODULE_KEYS = ("context", "cache", "speed", "quota", "sessions", "colab", "mailbox", "freshness")
CAPSULE_COLOR_KEYS = (
    "context",
    "cache",
    "speed",
    "gemini",
    "argon",
    "reset",
    "sessions",
    "model",
    "workspace",
    "burn",
    "colab",
    "mailbox",
    "freshness",
)
VALID_CAPSULE_COLOR_TOKENS = ("auto", "blue", "green", "yellow", "red", "slate", "muted")
COLOR_SLOTS = ("context", "ok", "warn", "alert", "muted")
VALID_LANGUAGES = ("en", "zh")
VALID_STYLES = ("capsule", "ghost", "powerline", "pill", "minimal")
VALID_INDICATORS = ("ring", "bar")
VALID_DENSITIES = ("auto", "compact", "expanded")
VALID_SECONDARY_METRICS = ("burn", "reset", "sessions")
VALID_THEMES = ("google", "nord", "tokyo_night", "catppuccin", "custom")
VALID_COLOR_MODES = ("row", "semantic")
VALID_QUOTA_PRECISIONS = ("exact", "1dp", "int")
VALID_ROW_COLOR_TOKENS = ("blue", "green", "yellow", "red", "slate", "muted")
DEFAULT_ROW_COLORS = ["blue", "blue", "blue", "blue"]
VALID_COLORS = (
    "cyan",
    "green",
    "yellow",
    "red",
    "blue",
    "magenta",
    "white",
    "gray",
    "bright_cyan",
    "bright_green",
    "bright_yellow",
    "bright_red",
    "bright_blue",
    "bright_magenta",
)

DEFAULT_CLOUD_PATHS: dict[str, str] = {
    "gcp_project": "your-gcp-project",
    "rclone_remote": "gdrive",
    "drive_backend": "auto",
    "colab_status_path": "Colab_Notebooks/status.json",
    "mailbox_root": "Colab_Notebooks/mailbox",
    "projects_root": "Projects",
}

DEFAULT_STATUSLINE_CONFIG: dict[str, Any] = {
    "version": 1,
    "language": "en",
    "style": "capsule",
    "indicator": "ring",
    "density": "auto",
    "secondary_metric": "burn",
    "theme": "google",
    "color_mode": "row",
    "quota_precision": "exact",
    "row_colors": list(DEFAULT_ROW_COLORS),
    "top_gap": True,
    "line_gap": True,
    "auto_wrap": True,
    "max_width": 0,
    "modules": {
        "context": True,
        "cache": True,
        "speed": True,
        "quota": True,
        "sessions": True,
        "colab": True,
        "mailbox": True,
        "freshness": True,
    },
    "colors": {
        "context": "cyan",
        "ok": "green",
        "warn": "yellow",
        "alert": "red",
        "muted": "gray",
    },
    "capsule_colors": {key: "auto" for key in CAPSULE_COLOR_KEYS},
    "control_center": {
        "side_ratio": DEFAULT_SIDE_RATIO,
        "dock_ratio": DEFAULT_DOCK_RATIO,
    },
    "cloud": deepcopy(DEFAULT_CLOUD_PATHS),
}


def _bounded_int(raw_val: Any, default: int, bounds: tuple[int, int]) -> int:
    if isinstance(raw_val, bool):
        return default
    try:
        val = int(raw_val)
    except (TypeError, ValueError):
        return default
    lo, hi = bounds
    return val if lo <= val <= hi else default


def _normalize_config(raw: dict[str, Any], base_cloud: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Validate and merge raw statusline config fields with defaults."""
    cfg = deepcopy(DEFAULT_STATUSLINE_CONFIG)
    if base_cloud:
        cfg["cloud"].update(base_cloud)

    for key, valid in (
        ("language", VALID_LANGUAGES),
        ("style", VALID_STYLES),
        ("indicator", VALID_INDICATORS),
        ("density", VALID_DENSITIES),
        ("secondary_metric", VALID_SECONDARY_METRICS),
        ("theme", VALID_THEMES),
        ("color_mode", VALID_COLOR_MODES),
        ("quota_precision", VALID_QUOTA_PRECISIONS),
    ):
        val = str(raw.get(key, "")).lower()
        if val in valid:
            cfg[key] = val

    for bool_key in ("auto_wrap", "top_gap", "line_gap"):
        if isinstance(raw.get(bool_key), bool):
            cfg[bool_key] = raw[bool_key]

    if isinstance(raw.get("max_width"), int) and not isinstance(raw.get("max_width"), bool) and raw["max_width"] >= 0:
        cfg["max_width"] = raw["max_width"]

    cc_raw = raw.get("control_center")
    if isinstance(cc_raw, dict):
        cfg["control_center"]["side_ratio"] = _bounded_int(
            cc_raw.get("side_ratio"), DEFAULT_SIDE_RATIO, SIDE_RATIO_BOUNDS
        )
        cfg["control_center"]["dock_ratio"] = _bounded_int(
            cc_raw.get("dock_ratio"), DEFAULT_DOCK_RATIO, DOCK_RATIO_BOUNDS
        )

    rc = raw.get("row_colors")
    if isinstance(rc, list):
        clean_rc = [str(x).strip().lower() for x in rc if str(x).strip().lower() in VALID_ROW_COLOR_TOKENS]
        if clean_rc:
            cfg["row_colors"] = clean_rc

    mods = raw.get("modules")
    if isinstance(mods, dict):
        for key in MODULE_KEYS:
            if isinstance(mods.get(key), bool):
                cfg["modules"][key] = mods[key]

    cols = raw.get("colors")
    if isinstance(cols, dict):
        for slot in COLOR_SLOTS:
            val = str(cols.get(slot, "")).strip().lower()
            if val in VALID_COLORS:
                cfg["colors"][slot] = val

    cap_cols = raw.get("capsule_colors")
    if isinstance(cap_cols, dict):
        for key in CAPSULE_COLOR_KEYS:
            val = str(cap_cols.get(key, "")).strip().lower()
            if val in VALID_CAPSULE_COLOR_TOKENS:
                cfg["capsule_colors"][key] = val

    cloud_raw = raw.get("cloud")
    if isinstance(cloud_raw, dict):
        for key in DEFAULT_CLOUD_PATHS:
            val = str(cloud_raw.get(key, "")).strip()
            if val:
                cfg["cloud"][key] = val

    return cfg


def load_statusline_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load statusline_config.json merged safely with English-first defaults."""
    target = path or DEFAULT_STATUSLINE_CONFIG_PATH
    if not target.exists():
        return deepcopy(DEFAULT_STATUSLINE_CONFIG)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(DEFAULT_STATUSLINE_CONFIG)
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_STATUSLINE_CONFIG)
    return _normalize_config(raw)


def get_cloud_settings(path: Optional[Path] = None) -> dict[str, str]:
    """Return cloud topology settings with FAIZ_* environment variable overrides."""
    cfg = load_statusline_config(path)
    cloud = dict(cfg.get("cloud", DEFAULT_CLOUD_PATHS))
    env_map = {
        "gcp_project": "FAIZ_GCP_PROJECT",
        "rclone_remote": "FAIZ_RCLONE_REMOTE",
        "drive_backend": "FAIZ_DRIVE_BACKEND",
        "colab_status_path": "FAIZ_COLAB_STATUS_PATH",
        "mailbox_root": "FAIZ_MAILBOX_ROOT",
        "projects_root": "FAIZ_PROJECTS_ROOT",
    }
    for key, env_name in env_map.items():
        val = os.environ.get(env_name, "").strip()
        if val:
            cloud[key] = val
    return cloud


def default_dash_disabled_path() -> Path:
    """Return path to the `dash_disabled` marker file controlling startup auto-dock."""
    return Path.home() / ".agents" / "cache" / "dash_disabled"


def read_auto_dock_enabled(disabled_flag: Optional[Path] = None) -> bool:
    """Return True when startup auto-dock is enabled (`dash_disabled` absent)."""
    target = disabled_flag or default_dash_disabled_path()
    return not target.exists()


def write_auto_dock_enabled(enabled: bool, disabled_flag: Optional[Path] = None) -> bool:
    """Synchronize `dash_disabled` marker file with the requested auto-dock boolean."""
    target = disabled_flag or default_dash_disabled_path()
    if enabled:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    return enabled


def save_statusline_config(cfg: dict[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    """Normalize and atomically persist statusline_config.json."""
    target = path or DEFAULT_STATUSLINE_CONFIG_PATH
    existing = load_statusline_config(target)
    normalized = _normalize_config(cfg, base_cloud=existing.get("cloud"))
    atomic_write_json(target, normalized)
    return normalized

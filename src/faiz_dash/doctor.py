"""Environment diagnostics & installation doctor checks for Faiz Control Center."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import tomllib
from typing import Any, Callable, Mapping, Optional

from .models import sanitize_for_json

GLYPH_SAMPLE = "\ue0b6 ● \ue0b4"
EXPECTED_STATUSLINE_CMD = "~/.agents/scripts/statusline.sh"


def _detect_truecolor(env: Mapping[str, str]) -> tuple[bool, str]:
    ct = env.get("COLORTERM", "")
    tm = env.get("TERM", "")
    tp = env.get("TERM_PROGRAM", "")
    if "truecolor" in ct or "24bit" in ct:
        return True, f"COLORTERM={ct}"
    if any(k in tm for k in ("kitty", "ghostty", "direct")):
        return True, f"TERM={tm}"
    if tp in {"rex", "ghostty", "iTerm.app", "WezTerm", "kitty"}:
        return True, f"TERM_PROGRAM={tp}"
    if tp == "Apple_Terminal":
        return False, "TERM_PROGRAM=Apple_Terminal (forces minimal fallback)"
    if "256color" in tm:
        return True, f"TERM={tm}"
    return False, f"COLORTERM={ct or 'unset'}, TERM={tm or 'unset'}"


def _mask_home(text: str, home: Path) -> str:
    if not text:
        return text
    home_str = str(home)
    resolved_str = str(home.resolve())
    out = text.replace(resolved_str, "~").replace(home_str, "~")
    return out


def _mask_obj(obj: Any, home: Path) -> Any:
    if isinstance(obj, str):
        return _mask_home(obj, home)
    if isinstance(obj, list):
        return [_mask_obj(item, home) for item in obj]
    if isinstance(obj, dict):
        return {k: _mask_obj(v, home) for k, v in obj.items()}
    return obj


def _age_label(path: Path, now_ts: float) -> str:
    if not path.exists():
        return "missing"
    age = max(0, int(now_ts - path.stat().st_mtime))
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    return f"{age // 3600}h{(age % 3600) // 60:02d}m ago"


def run_doctor_checks(
    *,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    settings_path: Optional[Path] = None,
    statusline_path: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    which_fn: Optional[Callable[[str], Optional[str]]] = None,
    version_info: Optional[tuple[int, int, int]] = None,
) -> dict[str, Any]:
    """Run read-only environment, font, settings, and cache checks."""
    active_home = home or Path.home()
    active_env = env if env is not None else os.environ
    active_settings = settings_path or (active_home / ".gemini" / "antigravity-cli" / "settings.json")
    active_statusline = statusline_path or (active_home / ".agents" / "scripts" / "statusline.sh")
    active_cache = cache_dir or (active_home / ".agents" / "cache")
    resolver = which_fn or shutil.which
    py_ver = version_info or (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    now_ts = time.time()

    checks: list[dict[str, str]] = []

    # 1. Python version >= 3.12
    py_str = f"{py_ver[0]}.{py_ver[1]}.{py_ver[2]}"
    if py_ver[:2] >= (3, 12):
        checks.append({"id": "python_version", "status": "pass", "title": "Python Runtime", "detail": f"Python {py_str} (>= 3.12)"})
    else:
        checks.append({"id": "python_version", "status": "fail", "title": "Python Runtime", "detail": f"Python {py_str} (< 3.12 required)", "fix": "Install Python 3.12+"})

    # 2. Python dependencies (textual, rich)
    missing_mods = [mod for mod in ("textual", "rich") if importlib.util.find_spec(mod) is None]
    if not missing_mods:
        checks.append({"id": "python_deps", "status": "pass", "title": "Python Packages", "detail": "textual, rich available"})
    else:
        checks.append({"id": "python_deps", "status": "fail", "title": "Python Packages", "detail": f"Missing: {', '.join(missing_mods)}", "fix": "pip install -e ."})

    # 3. Shell deps (bash, jq)
    bash_bin = resolver("bash")
    jq_bin = resolver("jq") or ("/usr/bin/jq" if Path("/usr/bin/jq").exists() else None)
    missing_bins = [name for name, found in (("bash", bash_bin), ("jq", jq_bin)) if not found]
    if not missing_bins:
        checks.append({"id": "shell_deps", "status": "pass", "title": "Shell Tools", "detail": f"bash & jq ready ({jq_bin})"})
    else:
        checks.append({"id": "shell_deps", "status": "fail", "title": "Shell Tools", "detail": f"Missing: {', '.join(missing_bins)}", "fix": "brew install jq"})

    # 4. Terminal TrueColor
    tc_ok, tc_detail = _detect_truecolor(active_env)
    checks.append({
        "id": "terminal_truecolor",
        "status": "pass" if tc_ok else "warn",
        "title": "Terminal TrueColor",
        "detail": tc_detail,
        **({} if tc_ok else {"fix": "Use Rex, Ghostty, iTerm2, Kitty, or set COLORTERM=truecolor"}),
    })

    # 5. Nerd Font capsule glyphs & UTF-8 locale
    locale_val = active_env.get("LC_ALL") or active_env.get("LC_CTYPE") or active_env.get("LANG") or ""
    utf8_ok = ("utf-8" in locale_val.lower()) or ("utf8" in locale_val.lower()) or (locale_val == "")
    checks.append({
        "id": "nerd_font_glyphs",
        "status": "pass" if utf8_ok else "warn",
        "title": "Nerd Font & UTF-8",
        "detail": f"sample: {GLYPH_SAMPLE} (locale={locale_val or 'default'})",
        **({} if utf8_ok else {"fix": "Set LANG=en_US.UTF-8 and use a Nerd Font in your terminal"}),
    })

    # 6. statusline.sh existence & executability
    if active_statusline.exists() and os.access(active_statusline, os.X_OK):
        target_note = f" -> {active_statusline.resolve()}" if active_statusline.is_symlink() else ""
        checks.append({"id": "statusline_script", "status": "pass", "title": "StatusLine Script", "detail": f"{active_statusline}{target_note}"})
    elif active_statusline.exists():
        checks.append({"id": "statusline_script", "status": "fail", "title": "StatusLine Script", "detail": f"{active_statusline} is not executable", "fix": f"chmod +x {active_statusline}"})
    else:
        checks.append({"id": "statusline_script", "status": "fail", "title": "StatusLine Script", "detail": f"Missing {active_statusline}", "fix": "./install.sh --link"})

    # 7. agy settings.json statusLine wiring
    if not active_settings.exists():
        checks.append({"id": "agy_settings", "status": "warn", "title": "agy settings.json", "detail": f"Missing {active_settings}", "fix": "./install.sh --wire-settings"})
    else:
        try:
            raw = json.loads(active_settings.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Root JSON value must be an object")
            sl = raw.get("statusLine")
            if not isinstance(sl, dict):
                checks.append({"id": "agy_settings", "status": "warn", "title": "agy settings.json", "detail": "statusLine block missing", "fix": "./install.sh --wire-settings"})
            elif sl.get("enabled") is False:
                checks.append({"id": "agy_settings", "status": "warn", "title": "agy settings.json", "detail": "statusLine.enabled is false", "fix": "Remove enabled:false or set enabled:true in settings.json"})
            elif str(sl.get("command", "")).endswith("statusline.sh"):
                stack = sl.get("stack_with_default")
                stack_note = f", stack_with_default={stack}" if stack is not None else ""
                checks.append({"id": "agy_settings", "status": "pass", "title": "agy settings.json", "detail": f"Wired to {sl.get('command')}{stack_note}"})
            else:
                checks.append({"id": "agy_settings", "status": "warn", "title": "agy settings.json", "detail": f"statusLine.command={sl.get('command')!r} (expected {EXPECTED_STATUSLINE_CMD})", "fix": "./install.sh --wire-settings"})
        except Exception as exc:
            checks.append({"id": "agy_settings", "status": "fail", "title": "agy settings.json", "detail": f"Invalid JSON in {active_settings}: {exc}", "fix": f"Fix JSON syntax in {active_settings} before wiring"})

    # 8. Cache health
    usage_f = active_cache / "usage_snapshot.json"
    cloud_f = active_cache / "cloud_snapshot.json"
    usage_age = _age_label(usage_f, now_ts)
    cloud_age = _age_label(cloud_f, now_ts)
    cache_ok = usage_f.exists() or cloud_f.exists()
    checks.append({
        "id": "cache_health",
        "status": "pass" if cache_ok else "warn",
        "title": "Snapshot Cache",
        "detail": f"usage: {usage_age}, cloud: {cloud_age}",
        **({} if cache_ok else {"fix": "Run `faiz-dash inspect --live` to populate initial caches"}),
    })

    # 9. Optional CLIs
    opt_names = ("agy", "grok", "rex", "colab", "gdrive", "rclone", "gcloud")
    found_opts = [name for name in opt_names if resolver(name)]
    missing_opts = [name for name in opt_names if name not in found_opts]
    opt_detail = f"found: {', '.join(found_opts) or 'none'}; optional missing: {', '.join(missing_opts) or 'none'}"
    checks.append({
        "id": "optional_clis",
        "status": "pass" if found_opts else "info",
        "title": "Optional CLIs",
        "detail": opt_detail,
    })

    # 10. Grok CLI statusLine check (when grok CLI or ~/.grok/config.toml is present)
    grok_cfg = active_home / ".grok" / "config.toml"
    grok_sl = active_home / ".agents" / "scripts" / "grok-statusline.sh"
    if resolver("grok") or grok_cfg.exists():
        if not grok_sl.exists() or not os.access(grok_sl, os.X_OK):
            checks.append({
                "id": "grok_statusline",
                "status": "warn",
                "title": "Grok StatusLine",
                "detail": f"Missing or non-executable {grok_sl}",
                "fix": "./install.sh --link",
            })
        elif not grok_cfg.exists():
            checks.append({
                "id": "grok_statusline",
                "status": "warn",
                "title": "Grok StatusLine",
                "detail": f"Missing {grok_cfg}",
                "fix": 'Add [ui.status_line] type="command" command="~/.grok/statusline.sh" in ~/.grok/config.toml',
            })
        else:
            try:
                g_data = tomllib.loads(grok_cfg.read_text(encoding="utf-8"))
                g_sl = ((g_data.get("ui") or {}).get("status_line") or {}) if isinstance(g_data, dict) else {}
                g_type = str(g_sl.get("type", "")).strip()
                g_cmd = str(g_sl.get("command", "")).strip()
                if g_type == "command" and (g_cmd.endswith("statusline.sh") or g_cmd.endswith("grok-statusline.sh")):
                    checks.append({
                        "id": "grok_statusline",
                        "status": "pass",
                        "title": "Grok StatusLine",
                        "detail": f"Wired to {g_cmd} (target: {grok_sl})",
                    })
                else:
                    checks.append({
                        "id": "grok_statusline",
                        "status": "warn",
                        "title": "Grok StatusLine",
                        "detail": f"ui.status_line type={g_type!r} command={g_cmd!r}",
                        "fix": 'Set [ui.status_line] type="command" command="~/.grok/statusline.sh" in ~/.grok/config.toml',
                    })
            except Exception as exc:
                checks.append({
                    "id": "grok_statusline",
                    "status": "fail",
                    "title": "Grok StatusLine",
                    "detail": f"Invalid TOML in {grok_cfg}: {exc}",
                    "fix": f"Fix TOML syntax in {grok_cfg}",
                })

    summary = {
        "pass": sum(1 for c in checks if c["status"] == "pass"),
        "warn": sum(1 for c in checks if c["status"] == "warn"),
        "fail": sum(1 for c in checks if c["status"] == "fail"),
        "info": sum(1 for c in checks if c["status"] == "info"),
    }
    return {
        "ok": summary["fail"] == 0,
        "summary": summary,
        "glyph_sample": GLYPH_SAMPLE,
        "checks": checks,
    }


def format_doctor_output(
    report: dict[str, Any],
    *,
    as_json: bool = False,
    as_markdown: bool = False,
    home: Optional[Path] = None,
) -> str:
    """Format doctor report as redacted JSON, Markdown, or terminal summary."""
    active_home = home or Path.home()
    clean = sanitize_for_json(_mask_obj(report, active_home))
    if as_json:
        return json.dumps(clean, ensure_ascii=False, indent=2)

    s = clean["summary"]
    if as_markdown:
        lines = [
            f"## Faiz Control Center Doctor (`{'OK' if clean['ok'] else 'FAILED'}` | pass={s['pass']} warn={s['warn']} fail={s['fail']})",
            "",
            "| Check | Status | Detail |",
            "| :--- | :---: | :--- |",
        ]
        for chk in clean["checks"]:
            fix = f" (Fix: `{chk['fix']}`)" if chk.get("fix") else ""
            lines.append(f"| {chk['title']} | `{chk['status'].upper()}` | {chk['detail']}{fix} |")
        return "\n".join(lines)

    badge_map = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "INFO"}
    out = [f"Faiz Control Center Doctor — {'PASS' if clean['ok'] else 'FAIL'} (pass={s['pass']} warn={s['warn']} fail={s['fail']})"]
    for chk in clean["checks"]:
        badge = badge_map.get(chk["status"], chk["status"].upper())
        out.append(f"  [{badge}] {chk['title']}: {chk['detail']}")
        if chk.get("fix"):
            out.append(f"         fix: {chk['fix']}")
    return "\n".join(out)

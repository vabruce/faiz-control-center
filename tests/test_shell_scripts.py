"""Integration tests for statusline.sh rendering/latency and _faiz_dash_hook.sh gating."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_SH = REPO_ROOT / "scripts" / "statusline.sh"
HOOK_SH = REPO_ROOT / "bin" / "_faiz_dash_hook.sh"
FAIZ_DASH_SH = REPO_ROOT / "bin" / "faiz-dash"


class TestShellScripts(unittest.TestCase):
    def test_statusline_renders_cached_capsules_and_latency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 57.42,
                        "pro_wk_pct": 87.55,
                        "argon_wk_pct": 13.78,
                        "quota_gate": "THROTTLE_ARGON",
                        "pro_count": 2,
                        "gcp_count": 1,
                        "tokens_1h": 1766049,
                        "pro_5h_reset_at_epoch": int(time.time() + 12750),
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps(
                    {
                        "colab_state": "ONLINE",
                        "idle_remaining_seconds": 2685,
                        "gce_running_count": 0,
                        "mailbox_tasks_count": 2,
                        "mailbox_failed_count": 1,
                        "mailbox_unverified_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            t_file = fake_home / "transcript.jsonl"
            now_ep = time.time()
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 10))
            t_e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 5))
            t_file.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 3506, "cache_read_tokens": 232915, "output_tokens": 900}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_e}),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = str(fake_home)
            env["FAIZ_STATUSLINE_NO_BG"] = "1"
            env["FAIZ_TRANSCRIPT_PATH"] = str(t_file)
            env["FAIZ_TERM_WIDTH"] = "190"
            env["TERM"] = "xterm-kitty"
            env["COLORTERM"] = "truecolor"
            payload = json.dumps(
                {
                    "model": {"display_name": "Gemini 4 Hydrogen (High)"},
                    "context_window": {"used_percentage": 32.4},
                }
            ).encode("utf-8")

            # Warm run timing on wide terminal (W1-A default auto: unified 2x4 bento matrix + equalized column expansion)
            subprocess.run([str(STATUSLINE_SH)], input=payload, env=env, capture_output=True, check=True)
            timings: list[float] = []
            for _ in range(3):
                t0 = time.perf_counter()
                res = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env, capture_output=True, check=True)
                timings.append((time.perf_counter() - t0) * 1000.0)
            elapsed_ms = min(timings)

            raw_out = res.stdout.decode("utf-8", errors="replace")
            out = raw_out.strip()
            ansi_lines = out.splitlines()
            self.assertEqual(len(ansi_lines), 4)
            self.assertIn("─", ansi_lines[0])
            self.assertIn("╌", ansi_lines[2])
            plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
            plain_lines = plain.splitlines()
            self.assertEqual(len({len(ln) for ln in plain_lines}), 1)
            self.assertEqual(len(plain_lines[1]), 188)
            self.assertEqual(plain.count("Context "), 1)
            self.assertIn("\ue0b6", out)
            self.assertIn("\ue0b4", out)
            self.assertIn("35;45;64", out)       # Unified deep slate-blue dashboard glass
            self.assertIn("138;180;248", out)     # Row 1 accent
            self.assertIn("156;172;198", out)     # Row 1 harmonized label tint
            self.assertIn("238;243;252", out)     # Row 1 crisp value
            self.assertNotIn("27;52;38", out)     # No Row 2 frosted green in unified 2x4 matrix
            self.assertIn("\x1b[0m", out)
            self.assertIn("◔ Context 32% ■■□□□□", plain_lines[1])
            self.assertIn("● Cache 98.5% · Live", plain_lines[1])
            self.assertIn("▸ Speed 180 tok/s", plain_lines[1])
            self.assertIn("◑ Gemini 5h:57.42%(↻", plain_lines[1])
            self.assertIn(") · Wk:87.55%", plain_lines[1])
            self.assertIn("▲ Argon 13.78% Low", plain_lines[3])
            self.assertIn("◆ Session 4.4K · 1h:1.8M", plain_lines[3])
            self.assertIn("● Colab ONLINE · 44m left", plain_lines[3])
            self.assertIn("▲ Queue 2 Run · 1 Fail", plain_lines[3])
            self.assertLess(elapsed_ms, 120.0)

            # 91-col split with density="auto": 4-col aligned frosted matrix + faint top/mid hairline dividers
            env_91 = {**env, "FAIZ_TERM_WIDTH": "91"}
            res_91 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_91, capture_output=True, check=True)
            out_91 = res_91.stdout.decode("utf-8", errors="replace")
            ansi_lines_91 = out_91.splitlines()
            self.assertEqual(len(ansi_lines_91), 4)
            self.assertIn("─", ansi_lines_91[0])
            self.assertIn("35;45;64", ansi_lines_91[1])
            self.assertIn("╌", ansi_lines_91[2])
            self.assertIn("35;45;64", ansi_lines_91[3])
            self.assertNotIn("27;52;38", out_91)  # Unified slate-blue matrix surface on both rows
            plain_91 = re.sub(r"\x1b\[[0-9;]*m", "", out_91)
            plain_lines_91 = plain_91.splitlines()
            self.assertEqual(len({len(ln) for ln in plain_lines_91}), 1)
            self.assertLessEqual(len(plain_lines_91[1]), 89)
            self.assertIn("Cache 99%", plain_lines_91[1])
            self.assertIn("Speed 180t/s", plain_lines_91[1])
            self.assertIn("Gemini 57.42%·87.55%", plain_lines_91[1])
            self.assertIn("Argon 13.78%!", plain_lines_91[3])
            self.assertIn("◆ Burn 4.4K", plain_lines_91[3])
            self.assertIn("Colab ON(44m)", plain_lines_91[3])
            self.assertIn("Queue 2R · 1F", plain_lines_91[3])
            self.assertNotIn("live", plain_91)

            # Cold cache (+5 chars) + Arg·Cla at 91 cols stays 4-line 2x4 slate-blue glass matrix via compact rescue
            old_ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 360))
            t_cold = fake_home / "transcript_cold.jsonl"
            t_cold.write_text(
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": old_ts_iso, "input_tokens": 2500, "cache_read_tokens": 90000, "output_tokens": 900}) + "\n",
                encoding="utf-8",
            )
            os.utime(t_cold, (now_ep - 300, now_ep - 300))
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps({"pro_5h_pct": 100.0, "pro_wk_pct": 3.86, "argon_wk_pct": 1.63, "claude_5h_pct": 100.0, "claude_wk_pct": 77.82, "quota_gate": "WARN_PRO", "pro_count": 3, "gcp_count": 0}),
                encoding="utf-8",
            )
            env_91_cold = {**env_91, "FAIZ_TRANSCRIPT_PATH": str(t_cold)}
            res_91_cold = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_91_cold, capture_output=True, check=True)
            out_91_cold = res_91_cold.stdout.decode("utf-8", errors="replace")
            lines_91_cold = out_91_cold.splitlines()
            self.assertEqual(len(lines_91_cold), 4)
            self.assertIn("35;45;64", lines_91_cold[1])
            self.assertNotIn("84;108;150", out_91_cold)
            plain_91_cold = re.sub(r"\x1b\[[0-9;]*m", "", out_91_cold).splitlines()
            self.assertEqual(len({len(ln) for ln in plain_91_cold}), 1)
            self.assertLessEqual(len(plain_91_cold[1]), 89)
            self.assertIn("Cache 97% cold", plain_91_cold[1])
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 57.42,
                        "pro_wk_pct": 87.55,
                        "argon_wk_pct": 13.78,
                        "quota_gate": "THROTTLE_ARGON",
                        "pro_count": 2,
                        "gcp_count": 1,
                        "tokens_1h": 1766049,
                        "pro_5h_reset_at_epoch": int(time.time() + 12750),
                    }
                ),
                encoding="utf-8",
            )

            # Switch secondary_metric="reset" & idle queue -> displays Reset countdown and Queue Idle (suppresses UNV noise)
            (fake_home / ".agents" / "statusline_config.json").write_text(
                json.dumps({"secondary_metric": "reset"}), encoding="utf-8"
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "ONLINE", "idle_remaining_seconds": 2685, "mailbox_tasks_count": 0, "mailbox_failed_count": 0, "mailbox_unverified_count": 3}),
                encoding="utf-8",
            )
            res_rst = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_91, capture_output=True, check=True)
            plain_rst = re.sub(r"\x1b\[[0-9;]*m", "", res_rst.stdout.decode("utf-8", errors="replace"))
            self.assertIn("↻ Reset 3h3", plain_rst)
            self.assertIn("Queue Idle", plain_rst)
            self.assertNotIn("UNV", plain_rst)

            # Restore mailbox fixture for subsequent expanded check
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "ONLINE", "idle_remaining_seconds": 2685, "gce_running_count": 0, "mailbox_tasks_count": 2, "mailbox_failed_count": 1, "mailbox_unverified_count": 0}),
                encoding="utf-8",
            )

            # Explicit density="compact" on 91-col split packs into 1 single compact row (+ leading top_gap)
            (fake_home / ".agents" / "statusline_config.json").write_text(
                json.dumps({"density": "compact"}), encoding="utf-8"
            )
            res_compact = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_91, capture_output=True, check=True)
            raw_compact = res_compact.stdout.decode("utf-8", errors="replace")
            self.assertTrue(raw_compact.startswith("\n"))
            plain_compact = re.sub(r"\x1b\[[0-9;]*m", "", raw_compact.strip())
            self.assertNotIn("\n", plain_compact)
            self.assertIn("Ctx 32%·C99%·180t/s", plain_compact)

            # Reset config to default density="auto" for narrow 4x2 checks
            (fake_home / ".agents" / "statusline_config.json").write_text("{}", encoding="utf-8")

            # Explicit 58-col narrow split (FAIZ_TERM_WIDTH=60) with density="auto": tight 5-line 4x2 slate-blue outline grid
            env_60 = {**env, "FAIZ_TERM_WIDTH": "60"}
            res_60 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_60, capture_output=True, check=True)
            out_60 = res_60.stdout.decode("utf-8", errors="replace")
            ansi_lines_60 = out_60.splitlines()
            self.assertEqual(len(ansi_lines_60), 5)
            self.assertIn("╌", ansi_lines_60[2])
            self.assertIn("84;108;150", out_60)
            self.assertNotIn("35;45;64", out_60)
            self.assertNotIn("78;132;98", out_60)
            self.assertNotIn("162;82;84", out_60)
            plain_60 = re.sub(r"\x1b\[[0-9;]*m", "", out_60).splitlines()
            self.assertEqual(len({len(ln) for ln in plain_60}), 1)
            self.assertEqual(len(plain_60[0]), 58)
            self.assertIn("]  [", plain_60[0])
            self.assertIn("[ ▸ 180t/s", plain_60[1])
            self.assertIn("[ ◑ Gemini 57%·88%", plain_60[1])

            # 46-col narrow Rex sidebar (FAIZ_TERM_WIDTH=46): stays strictly 5-line 4x2 aligned slate-blue outline
            env_46 = {**env, "FAIZ_TERM_WIDTH": "46"}
            res_46 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_46, capture_output=True, check=True)
            out_46 = res_46.stdout.decode("utf-8", errors="replace")
            ansi_lines_46 = out_46.splitlines()
            self.assertEqual(len(ansi_lines_46), 5)
            self.assertIn("╌", ansi_lines_46[2])
            self.assertNotIn("78;132;98", out_46)
            plain_46 = re.sub(r"\x1b\[[0-9;]*m", "", out_46).splitlines()
            self.assertEqual(len({len(ln) for ln in plain_46}), 1)
            self.assertLessEqual(len(plain_46[0]), 44)
            self.assertIn("[ ◑ Gem 57%·88%", plain_46[1])

            # Per-capsule independent color override via capsule_colors (and CLI config command)
            cli_env = {**env, "PYTHONPATH": str(REPO_ROOT / "src")}
            subprocess.run(
                [sys.executable, "-m", "faiz_dash", "config", "--capsule-color", "gemini=green", "--capsule-color", "mailbox=red"],
                env=cli_env,
                capture_output=True,
                check=True,
            )
            res_cap = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_60, capture_output=True, check=True)
            out_cap = res_cap.stdout.decode("utf-8", errors="replace")
            cap_lines = out_cap.splitlines()
            self.assertIn("84;108;150", cap_lines[0])
            self.assertNotIn("78;132;98", cap_lines[0])
            self.assertIn("78;132;98", cap_lines[1])   # Gemini overridden to green border
            self.assertIn("162;82;84", cap_lines[4])   # Queue overridden to red border

            # Explicit density="expanded" with custom row_colors on 95-col split wraps cleanly onto 2 frosted rows (Blue / Green)
            (fake_home / ".agents" / "statusline_config.json").write_text(
                json.dumps({"density": "expanded", "line_gap": False, "row_colors": ["blue", "green"]}), encoding="utf-8"
            )
            env_95 = {**env, "FAIZ_TERM_WIDTH": "95"}
            res_95 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_95, capture_output=True, check=True)
            out_95 = res_95.stdout.decode("utf-8", errors="replace").strip()
            self.assertNotIn("\n\n", out_95)
            parts = out_95.splitlines()
            self.assertEqual(len(parts), 2)
            self.assertIn("35;45;64", parts[0])
            self.assertNotIn("27;52;38", parts[0])
            self.assertIn("27;52;38", parts[1])
            self.assertNotIn("35;45;64", parts[1])
            self.assertIn("2Q·1F", parts[1])

            # Explicit density="expanded" on 190-col wide renders single-row expanded capsules with freshness
            res_exp_wide = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env, capture_output=True, check=True)
            raw_exp_wide = res_exp_wide.stdout.decode("utf-8", errors="replace")
            self.assertTrue(raw_exp_wide.startswith("\n"))
            plain_exp_wide = re.sub(r"\x1b\[[0-9;]*m", "", raw_exp_wide.strip())
            self.assertNotIn("\n", plain_exp_wide)
            self.assertIn("▸ 180t/s", plain_exp_wide)
            self.assertIn("▲ Argon 13.78% (Low)", plain_exp_wide)
            self.assertIn("▲ Queue 2Q·1F", plain_exp_wide)
            self.assertIn("live", plain_exp_wide)

    def test_statusline_custom_config_zh_and_degraded_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps({"pro_5h_pct": 52.0, "pro_wk_pct": 86.0, "argon_wk_pct": 13.8, "pro_count": 3, "gcp_count": 0}),
                encoding="utf-8",
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps(
                    {
                        "colab_state": "DEGRADED",
                        "cloud_ok": False,
                        "cloud_error_kind": "rate_limit",
                        "gce_running_count": 0,
                        "mailbox_tasks_count": 0,
                        "mailbox_failed_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            t_file = fake_home / "transcript.jsonl"
            now_ep = time.time()
            # 250s stale step -> triggers cache cold alert ("冷却") & low speed <10 t/s
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 260))
            t_e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 250))
            t_file.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 3506, "cache_read_tokens": 232915, "output_tokens": 60}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_e}),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            cfg_file = fake_home / ".agents" / "statusline_config.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "language": "zh",
                        "style": "pill",
                        "indicator": "bar",
                        "density": "expanded",
                        "theme": "custom",
                        "color_mode": "semantic",
                        "top_gap": True,
                        "line_gap": True,
                        "auto_wrap": True,
                        "modules": {"context": True, "cache": True, "speed": True, "quota": True, "sessions": False, "colab": True, "mailbox": True, "freshness": True},
                        "colors": {"context": "magenta", "warn": "bright_yellow"},
                    }
                ),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_STATUSLINE_CONFIG": str(cfg_file),
                "FAIZ_TRANSCRIPT_PATH": str(t_file),
                "FAIZ_TERM_WIDTH": "180",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            res = subprocess.run([str(STATUSLINE_SH)], input=b'{"context_window":{"used_percentage":10}}', env=env, capture_output=True, check=True)
            out = res.stdout.decode("utf-8", errors="replace")
            self.assertTrue(out.startswith("\n"))
            self.assertIn("▎", out)
            self.assertIn("上下文 10% ■□□□□□", out)
            self.assertIn("缓存 98.5% 冷却", out)
            self.assertIn("6t/s", out)
            self.assertIn("180;142;173", out)
            self.assertIn("Wk:86.00%", out)
            self.assertNotIn("会话 ", out)
            self.assertIn("▲ Colab 限流", out)
            self.assertIn("队列 0待·1败", out)
            self.assertIn("rc限流", out)
            self.assertNotIn("实时", out)

    def test_statusline_styles_themes_and_apple_terminal_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cfg_file = fake_home / ".agents" / "statusline_config.json"
            cfg_file.parent.mkdir(parents=True)
            base_env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_STATUSLINE_CONFIG": str(cfg_file),
                "FAIZ_TRANSCRIPT_PATH": "",
                "FAIZ_TERM_WIDTH": "160",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }

            # Ghost style emits transparent brackets without background sequences
            cfg_file.write_text(json.dumps({"style": "ghost", "theme": "google"}), encoding="utf-8")
            res_gh = subprocess.run([str(STATUSLINE_SH)], input=b'{"context_window":{"used_percentage":40}}', env=base_env, capture_output=True, check=True)
            out_gh = res_gh.stdout.decode("utf-8", errors="replace")
            plain_gh = re.sub(r"\x1b\[[0-9;]*m", "", out_gh)
            self.assertNotIn("\x1b[48;2;", out_gh)
            self.assertIn("107;114;128", out_gh)
            self.assertRegex(plain_gh, r"\[◑ Context 40% ■■□□□□\s*\]")

            # Powerline style with Google theme uses Material 800 deep shades + crisp white fg
            cfg_file.write_text(json.dumps({"style": "powerline", "theme": "google", "indicator": "bar"}), encoding="utf-8")
            res_pw = subprocess.run([str(STATUSLINE_SH)], input=b'{"context_window":{"used_percentage":40}}', env=base_env, capture_output=True, check=True)
            out_pw = res_pw.stdout.decode("utf-8", errors="replace")
            self.assertIn("\ue0b6", out_pw)
            self.assertIn("\ue0b4", out_pw)
            self.assertIn("23;78;166", out_pw)
            self.assertIn("248;249;250", out_pw)

            # Pill style emits crisp left accent bar + elevated Material 800 Google palette
            cfg_file.write_text(json.dumps({"style": "pill", "theme": "google"}), encoding="utf-8")
            res_pl = subprocess.run([str(STATUSLINE_SH)], input=b'{"context_window":{"used_percentage":40}}', env=base_env, capture_output=True, check=True)
            out_pl = res_pl.stdout.decode("utf-8", errors="replace")
            self.assertIn("▎", out_pl)
            self.assertIn("23;78;166", out_pl)

            # Apple_Terminal forces minimal fallback even when style=ghost and TERM=xterm-256color
            cfg_file.write_text(json.dumps({"style": "ghost", "theme": "google", "indicator": "bar"}), encoding="utf-8")
            apple_env = {**base_env, "TERM_PROGRAM": "Apple_Terminal", "TERM": "xterm-256color", "COLORTERM": ""}
            res_ap = subprocess.run([str(STATUSLINE_SH)], input=b'{"context_window":{"used_percentage":40}}', env=apple_env, capture_output=True, check=True)
            out_ap = res_ap.stdout.decode("utf-8", errors="replace")
            self.assertNotIn("\x1b[48;2;", out_ap)
            self.assertNotIn("[◔", out_ap)
            self.assertIn(" | ", out_ap)
            self.assertIn("Context 40% ■■□□□□", out_ap)

    def test_statusline_broken_stdin_path_falls_back_to_cli_brain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            t_dir = fake_home / ".gemini" / "antigravity-cli" / "brain" / "conv-live" / ".system_generated" / "logs"
            t_dir.mkdir(parents=True)
            t_file = t_dir / "transcript.jsonl"
            now_ep = time.time()
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 8))
            t_e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ep - 4))
            t_file.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 500, "cache_read_tokens": 9500, "output_tokens": 400}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_e}),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            env = {**os.environ, "HOME": str(fake_home), "FAIZ_STATUSLINE_NO_BG": "1", "TERM": "xterm-kitty", "COLORTERM": "truecolor", "FAIZ_TERM_WIDTH": "160"}
            env.pop("FAIZ_TRANSCRIPT_PATH", None)
            broken_payload = json.dumps({
                "context_window": {"used_percentage": 22},
                "transcript_path": str(fake_home / ".gemini" / "antigravity" / "brain" / ".system_generated" / "logs" / "transcript.jsonl"),
            }).encode("utf-8")
            res = subprocess.run([str(STATUSLINE_SH)], input=broken_payload, env=env, capture_output=True, check=True)
            plain = re.sub(r"\x1b\[[0-9;]*m", "", res.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Cache 95.0%", plain)
            self.assertIn("100 tok/s", plain)

    def test_statusline_graceful_without_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["HOME"] = tmp
            env["FAIZ_STATUSLINE_NO_BG"] = "1"
            env["FAIZ_TRANSCRIPT_PATH"] = ""
            env["FAIZ_TERM_WIDTH"] = "160"
            env["TERM"] = "xterm-kitty"
            env["COLORTERM"] = "truecolor"
            res = subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            out = res.stdout.decode("utf-8", errors="replace")
            plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
            self.assertIn("Faiz", plain)
            self.assertIn("○ Quota syncing", plain)

    def _run_hook_in_pty(self, args: list[str], env_overrides: dict[str, str], fake_bin: Path) -> None:
        master, slave = pty.openpty()
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        env.pop("FAIZ_NO_DASH", None)
        env.pop("REX_BLOCK", None)
        env.pop("REX_BLOCK_ID", None)
        env.update(env_overrides)

        cmd_args = " ".join(repr(a) for a in args)
        script = f"source {str(HOOK_SH)!r}; _maybe_launch_faiz_dash {cmd_args}; wait"
        proc = subprocess.Popen(
            ["bash", "-c", script],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            close_fds=True,
        )
        os.close(slave)
        try:
            proc.wait(timeout=5.0)
            while select.select([master], [], [], 0.05)[0]:
                try:
                    if not os.read(master, 4096):
                        break
                except OSError:
                    break
        finally:
            os.close(master)

    def test_hook_anti_recursion_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            (fake_home / ".agents" / "cache").mkdir(parents=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            rex_log = root / "rex_calls.log"
            fake_rex = fake_bin / "rex"
            fake_rex.write_text(
                f"""#!/usr/bin/env bash
echo "$@" >> {str(rex_log)!r}
case "$1" in
  whoami) echo '{{"window_id":"win-1"}}' ;;
  block) echo '{{"blocks":[]}}' ;;
esac
""",
                encoding="utf-8",
            )
            fake_rex.chmod(0o755)

            base_env = {"HOME": str(fake_home), "REX_BLOCK_ID": "block:123"}

            # 1. All blocked modes must NEVER invoke rex
            blocked_cases = [
                (["-p", "hi"], base_env),
                (["--print", "hi"], base_env),
                (["acp"], base_env),
                (["models"], base_env),
                (["help"], base_env),
                (["update"], base_env),
                (["plugin", "list"], base_env),
                ([], {**base_env, "FAIZ_NO_DASH": "1"}),
                ([], {"HOME": str(fake_home)}),  # missing REX_BLOCK_ID / REX_BLOCK
            ]
            for args, env_ov in blocked_cases:
                self._run_hook_in_pty(args, env_ov, fake_bin)
            self.assertFalse(rex_log.exists(), f"Blocked case triggered rex: {rex_log.read_text() if rex_log.exists() else ''}")

            # 2. dash_disabled must block rex
            disabled_flag = fake_home / ".agents" / "cache" / "dash_disabled"
            disabled_flag.write_text("", encoding="utf-8")
            self._run_hook_in_pty([], base_env, fake_bin)
            self.assertFalse(rex_log.exists())
            disabled_flag.unlink()

            # 3. Valid interactive TTY + REX_BLOCK_ID must trigger rex split
            self._run_hook_in_pty([], base_env, fake_bin)
            self.assertTrue(rex_log.exists())
            calls = rex_log.read_text(encoding="utf-8")
            self.assertIn("split -b block:123 --split=below --ratio 22 --focus=false --label faiz-dash", calls)

            # 4. Existing faiz-dash block in same window_id must NOT split again
            rex_log.unlink()
            fake_rex.write_text(
                f"""#!/usr/bin/env bash
echo "$@" >> {str(rex_log)!r}
case "$1" in
  whoami) echo '{{"window_id":"win-1"}}' ;;
  block) echo '{{"blocks":[{{"block_id":"b9","window_id":"win-1","label":"faiz-dash"}}]}}' ;;
esac
""",
                encoding="utf-8",
            )
            fake_rex.chmod(0o755)
            self._run_hook_in_pty([], base_env, fake_bin)
            calls2 = rex_log.read_text(encoding="utf-8")
            self.assertNotIn("split ", calls2)

    def test_statusline_cloud_backoff_skips_bg_and_detaches_pipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps({"pro_5h_pct": 80.0, "pro_wk_pct": 70.0, "pty_fetched_epoch": int(time.time())}),
                encoding="utf-8",
            )
            (cache_dir / ".bg_pty_ts").write_text("", encoding="utf-8")
            # Force cloud_snapshot.json mtime stale (>90s) while cloud_backoff.json is active in future
            old_ts = time.time() - 300
            cloud_f = cache_dir / "cloud_snapshot.json"
            cloud_f.write_text(json.dumps({"colab_state": "DEGRADED"}), encoding="utf-8")
            os.utime(cloud_f, (old_ts, old_ts))
            (cache_dir / "cloud_backoff.json").write_text(
                json.dumps({"next_allowed_epoch": time.time() + 240, "error_streak": 2}),
                encoding="utf-8",
            )

            marker = root / "dash_invoked.log"
            fake_dash = root / "fake-dash"
            fake_dash.write_text(
                f"#!/usr/bin/env bash\necho called >> {str(marker)!r}\nsleep 1\n",
                encoding="utf-8",
            )
            fake_dash.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_DASH_BIN": str(fake_dash),
                "FAIZ_TERM_WIDTH": "160",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            env.pop("FAIZ_STATUSLINE_NO_BG", None)

            t0 = time.perf_counter()
            res = subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertLess(elapsed_ms, 120.0)
            self.assertFalse(marker.exists())
            self.assertIn("Gemini", res.stdout.decode("utf-8", errors="replace"))

    def test_faiz_dash_side_toggle_and_non_rex_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            (fake_home / ".agents" / "cache").mkdir(parents=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            rex_log = root / "rex_calls.log"
            fake_rex = fake_bin / "rex"
            fake_rex.write_text(
                f"""#!/usr/bin/env bash
echo "$@" >> {str(rex_log)!r}
case "$1" in
  whoami) echo '{{"window_id":"win-1"}}' ;;
  block)
    if [ "${{2:-}}" = "list" ]; then
      echo '{{"blocks":[]}}'
    fi
    ;;
esac
""",
                encoding="utf-8",
            )
            fake_rex.chmod(0o755)

            # 1. Non-Rex fallback prints guidance and skips rex
            non_rex_env = {**os.environ, "HOME": str(fake_home), "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
            non_rex_env.pop("REX_BLOCK_ID", None)
            non_rex_env.pop("REX_BLOCK", None)
            res_nr = subprocess.run([str(FAIZ_DASH_SH), "side"], env=non_rex_env, capture_output=True, text=True, check=True)
            self.assertIn("not inside a Rex pane", res_nr.stdout)
            self.assertFalse(rex_log.exists())

            # 2. First `faiz-dash side` inside Rex opens right sidebar with preset 2
            rex_env = {**non_rex_env, "REX_BLOCK_ID": "block:123"}
            res_open = subprocess.run([str(FAIZ_DASH_SH), "side"], env=rex_env, capture_output=True, text=True, check=True)
            self.assertIn("opened faiz-dash-side pane", res_open.stdout)
            open_calls = rex_log.read_text(encoding="utf-8")
            self.assertIn("split -b block:123 --split=right --ratio 28 --focus=false --label faiz-dash-side --shell=none", open_calls)
            self.assertIn("--preset 2", open_calls)

            # 3. Second `faiz-dash side` when faiz-dash-side exists closes it
            rex_log.unlink()
            fake_rex.write_text(
                f"""#!/usr/bin/env bash
echo "$@" >> {str(rex_log)!r}
case "$1" in
  whoami) echo '{{"window_id":"win-1"}}' ;;
  block)
    if [ "${{2:-}}" = "list" ]; then
      echo '{{"blocks":[{{"block_id":"b-side-1","window_id":"win-1","label":"faiz-dash-side"}}]}}'
    fi
    ;;
esac
""",
                encoding="utf-8",
            )
            fake_rex.chmod(0o755)
            res_close = subprocess.run([str(FAIZ_DASH_SH), "side"], env=rex_env, capture_output=True, text=True, check=True)
            self.assertIn("closed faiz-dash-side pane", res_close.stdout)
            self.assertIn("block close b-side-1", rex_log.read_text(encoding="utf-8"))

            # 4. `faiz-dash off` closes existing pane and creates dash_disabled
            rex_log.unlink()
            res_off = subprocess.run([str(FAIZ_DASH_SH), "off"], env=rex_env, capture_output=True, text=True, check=True)
            self.assertIn("auto-launch off", res_off.stdout)
            self.assertTrue((fake_home / ".agents" / "cache" / "dash_disabled").exists())
            self.assertIn("block close b-side-1", rex_log.read_text(encoding="utf-8"))

            # 5. Custom `control_center.side_ratio` in statusline_config.json overrides default 28
            rex_log.unlink()
            fake_rex.write_text(
                f"""#!/usr/bin/env bash
echo "$@" >> {str(rex_log)!r}
case "$1" in
  whoami) echo '{{"window_id":"win-1"}}' ;;
  block)
    if [ "${{2:-}}" = "list" ]; then
      echo '{{"blocks":[]}}'
    fi
    ;;
esac
""",
                encoding="utf-8",
            )
            fake_rex.chmod(0o755)
            (fake_home / ".agents" / "statusline_config.json").write_text(
                json.dumps({"control_center": {"side_ratio": 32, "dock_ratio": 26}}),
                encoding="utf-8",
            )
            res_custom = subprocess.run([str(FAIZ_DASH_SH), "side"], env=rex_env, capture_output=True, text=True, check=True)
            self.assertIn("opened faiz-dash-side pane (right, 32%)", res_custom.stdout)
            self.assertIn("--split=right --ratio 32", rex_log.read_text(encoding="utf-8"))

    def test_statusline_active_transcript_triggers_fast_45s_quota_only_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)

            now = time.time()
            u_file = cache_dir / "usage_snapshot.json"
            u_file.write_text(
                json.dumps({"pro_5h_pct": 21.36, "pro_wk_pct": 51.66, "pty_fetched_epoch": int(now - 55)}),
                encoding="utf-8",
            )
            os.utime(u_file, (now - 55, now - 55))
            p_stamp = cache_dir / ".bg_pty_ts"
            p_stamp.write_text("", encoding="utf-8")
            os.utime(p_stamp, (now - 55, now - 55))

            c_file = cache_dir / "cloud_snapshot.json"
            c_file.write_text(json.dumps({"colab_state": "DEGRADED"}), encoding="utf-8")
            os.utime(c_file, (now - 10, now - 10))
            (cache_dir / "cloud_backoff.json").write_text(
                json.dumps({"next_allowed_epoch": now + 180}),
                encoding="utf-8",
            )

            t_file = root / "transcript.jsonl"
            t_file.write_text('{"step_index":1}\n', encoding="utf-8")
            os.utime(t_file, (now - 120, now - 120))  # Older than usage_snapshot -> idle -> skip

            marker = root / "dash_args.log"
            fake_dash = root / "fake-dash"
            fake_dash.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {str(marker)!r}\n", encoding="utf-8")
            fake_dash.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_DASH_BIN": str(fake_dash),
                "FAIZ_TRANSCRIPT_PATH": str(t_file),
                "FAIZ_TERM_WIDTH": "91",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            env.pop("FAIZ_STATUSLINE_NO_BG", None)

            subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            time.sleep(0.08)
            self.assertFalse(marker.exists())

            # Now simulate an active AI turn newer than usage_snapshot -> triggers 45s fast quota-only refresh
            os.utime(t_file, (now - 2, now - 2))
            subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            deadline = time.monotonic() + 1.5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            self.assertIn("--once-json --quota-only", marker.read_text(encoding="utf-8"))

    def test_statusline_not_configured_cloud_hides_noise_and_shows_claude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 80.25,
                        "pro_wk_pct": 91.5,
                        "claude_wk_pct": 88.25,
                        "argon_wk_pct": 64.0,
                        "quota_gate": "GREEN",
                        "pty_fetched_epoch": int(time.time()),
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "NOT_CONFIGURED", "mailbox_tasks_count": 0}),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_TRANSCRIPT_PATH": "",
                "FAIZ_TERM_WIDTH": "180",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            res = subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            plain = re.sub(r"\x1b\[[0-9;]*m", "", res.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Gemini 5h:80.25% · Wk:91.50%", plain)
            self.assertIn("Argon 64.00%", plain)
            self.assertNotIn("Claude", plain)
            self.assertNotIn("Colab", plain)
            self.assertNotIn("Queue", plain)

            (fake_home / ".agents" / "statusline_config.json").write_text(
                json.dumps({"density": "expanded"}), encoding="utf-8"
            )
            res_exp = subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            plain_exp = re.sub(r"\x1b\[[0-9;]*m", "", res_exp.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Argon 64.00%", plain_exp)
            self.assertNotIn("Claude", plain_exp)

    def test_statusline_merges_claude_5h_wk_and_tracks_session_burn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()
            u_file = cache_dir / "usage_snapshot.json"
            u_file.write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 0.0,
                        "pro_wk_pct": 17.11,
                        "claude_5h_pct": 70.35,
                        "claude_wk_pct": 85.17,
                        "argon_wk_pct": 9.43,
                        "quota_gate": "WARN_PRO",
                        "tokens_1h": 1200000,
                        "pty_fetched_epoch": int(now - 2),
                    }
                ),
                encoding="utf-8",
            )
            os.utime(u_file, (now - 2, now - 2))
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps(
                    {
                        "colab_state": "ONLINE",
                        "idle_remaining_seconds": 420,
                        "mailbox_tasks_count": 0,
                        "mailbox_failed_count": 2,
                    }
                ),
                encoding="utf-8",
            )
            t_file = root / "transcript.jsonl"
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10))
            t_file.write_text(
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 3500, "cache_read_tokens": 90000, "output_tokens": 900}) + "\n",
                encoding="utf-8",
            )
            os.utime(t_file, (now - 5, now - 5))

            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_TRANSCRIPT_PATH": str(t_file),
                "FAIZ_TERM_WIDTH": "91",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            payload = b'{"context_window":{"used_percentage":17},"terminal_width":90}'
            env_90 = {k: v for k, v in env.items() if k != "FAIZ_TERM_WIDTH"}
            res_90 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_90, capture_output=True, check=True)
            lines_90 = res_90.stdout.decode("utf-8", errors="replace").splitlines()
            self.assertEqual(len(lines_90), 4)
            plain_90 = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines_90)).splitlines()
            self.assertIn("─", plain_90[0])
            self.assertIn("╌", plain_90[2])
            self.assertEqual(len({len(ln) for ln in plain_90}), 1)
            self.assertLessEqual(len(plain_90[1]), 88)
            self.assertIn("Gemini 0.00%·17.11%", plain_90[1])
            self.assertIn("Argon 9.43%!", plain_90[3])
            self.assertNotIn("Claude", plain_90[3])
            self.assertIn("Burn 4.4K", plain_90[3])

            # At >=96 columns (A2 adaptive expansion), Argon displays full 2 decimals and stays aligned 2x4 matrix
            env_104 = {**env, "FAIZ_TERM_WIDTH": "104"}
            res_104 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_104, capture_output=True, check=True)
            lines_104 = res_104.stdout.decode("utf-8", errors="replace").splitlines()
            self.assertEqual(len(lines_104), 4)
            plain_104_lines = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines_104)).splitlines()
            self.assertIn("─", plain_104_lines[0])
            self.assertIn("╌", plain_104_lines[2])
            self.assertEqual(len({len(ln) for ln in plain_104_lines}), 1)
            self.assertIn("Argon 9.43%!", plain_104_lines[3])
            self.assertNotIn("Claude", plain_104_lines[3])

            # At >=155 columns (W1-A wide 2x4 bento matrix), full labels spread evenly to eff_cols
            env_180 = {**env, "FAIZ_TERM_WIDTH": "180"}
            res_180 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_180, capture_output=True, check=True)
            lines_180 = res_180.stdout.decode("utf-8", errors="replace").splitlines()
            self.assertEqual(len(lines_180), 4)
            plain_180_lines = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines_180)).splitlines()
            self.assertIn("─", plain_180_lines[0])
            self.assertIn("╌", plain_180_lines[2])
            self.assertEqual(len({len(ln) for ln in plain_180_lines}), 1)
            self.assertEqual(len(plain_180_lines[1]), 178)
            self.assertIn("Gemini 5h:0.00% · Wk:17.11%", plain_180_lines[1])
            self.assertIn("Argon 9.43% Pro!", plain_180_lines[3])
            self.assertNotIn("Claude", plain_180_lines[3])
            self.assertIn("Queue 2 Fail", plain_180_lines[3])

            # At 60 columns (5-line 4x2 narrow outline matrix), renders concise Gemini + Argon without Claude or rainbow fills
            env_60 = {**env, "FAIZ_TERM_WIDTH": "60"}
            res_60 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_60, capture_output=True, check=True)
            out_60 = res_60.stdout.decode("utf-8", errors="replace")
            lines_60 = out_60.splitlines()
            self.assertEqual(len(lines_60), 5)
            self.assertNotIn("27;52;38", out_60)
            self.assertNotIn("64;30;31", out_60)
            self.assertIn("84;108;150", out_60)
            plain_60_lines = re.sub(r"\x1b\[[0-9;]*m", "", out_60).splitlines()
            self.assertEqual(len({len(ln) for ln in plain_60_lines}), 1)
            self.assertEqual(len(plain_60_lines[0]), 58)
            self.assertIn("[ ○ Gemini 0%·17%", plain_60_lines[1])
            self.assertIn("[ ▲ Argon 9.43%!", plain_60_lines[3])
            self.assertNotIn("Cla", plain_60_lines[3])

            # Appending turns incrementally updates single-session Burn to 2-decimal M
            with t_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 1_100_000, "output_tokens": 5_600}) + "\n")
            res_inc = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_104, capture_output=True, check=True)
            plain_inc = re.sub(r"\x1b\[[0-9;]*m", "", res_inc.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Burn 1.11M", plain_inc)

            # Even when usage_snapshot mtime (now-2) > transcript mtime (now-5), active <120s transcript keeps 20s cooldown
            p_stamp = cache_dir / ".bg_pty_ts"
            p_stamp.write_text("", encoding="utf-8")
            os.utime(p_stamp, (now - 25, now - 25))
            os.utime(cache_dir / "cloud_snapshot.json", (now - 5, now - 5))
            (cache_dir / "cloud_backoff.json").write_text(json.dumps({"next_allowed_epoch": now + 180}), encoding="utf-8")
            marker = root / "dash_fast.log"
            fake_dash = root / "fake-dash"
            fake_dash.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {str(marker)!r}\n", encoding="utf-8")
            fake_dash.chmod(0o755)
            env_bg = {**env_104, "FAIZ_DASH_BIN": str(fake_dash)}
            env_bg.pop("FAIZ_STATUSLINE_NO_BG", None)
            subprocess.run([str(STATUSLINE_SH)], input=payload, env=env_bg, capture_output=True, check=True)
            deadline = time.monotonic() + 1.5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            self.assertIn("--once-json --quota-only", marker.read_text(encoding="utf-8"))

    def test_statusline_triggers_cloud_bg_with_colab_cli_even_during_drive_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            (fake_home / ".config" / "colab-cli").mkdir(parents=True)
            now = time.time()
            u_file = cache_dir / "usage_snapshot.json"
            u_file.write_text(
                json.dumps({"pro_5h_pct": 80.0, "pro_wk_pct": 70.0, "pty_fetched_epoch": int(now)}),
                encoding="utf-8",
            )
            (cache_dir / ".bg_pty_ts").write_text("", encoding="utf-8")
            c_file = cache_dir / "cloud_snapshot.json"
            c_file.write_text(json.dumps({"colab_state": "STOPPED", "cloud_ok": True}), encoding="utf-8")
            os.utime(c_file, (now - 120, now - 120))
            (cache_dir / "cloud_backoff.json").write_text(
                json.dumps({"next_allowed_epoch": now + 240}),
                encoding="utf-8",
            )

            marker = root / "dash_cloud_args.log"
            fake_dash = root / "fake-dash"
            fake_dash.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {str(marker)!r}\n", encoding="utf-8")
            fake_dash.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_DASH_BIN": str(fake_dash),
                "FAIZ_TRANSCRIPT_PATH": "",
                "FAIZ_TERM_WIDTH": "160",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            env.pop("FAIZ_STATUSLINE_NO_BG", None)
            res = subprocess.run([str(STATUSLINE_SH)], input=b"{}", env=env, capture_output=True, check=True)
            deadline = time.monotonic() + 1.5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            self.assertIn("--once-json --no-pty", marker.read_text(encoding="utf-8"))
            plain = re.sub(r"\x1b\[[0-9;]*m", "", res.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Colab IDLE", plain)
            self.assertNotIn("LIMIT", plain)

    def test_statusline_two_bucket_claude_replaces_argon_across_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 15.25,
                        "pro_wk_pct": 42.5,
                        "pro_5h_reset_at_epoch": int(now + 2 * 3600 + 30 * 60),
                        "pro_wk_reset_at_epoch": int(now + 5 * 86400 + 12 * 3600),
                        "argon_wk_pct": None,
                        "quota_gate": "WARN_PRO",
                        "pro_count": 2,
                        "gcp_count": 0,
                        "tokens_1h": 250000,
                        "pty_fetched_epoch": int(now - 5),
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "ONLINE", "idle_remaining_seconds": 1800, "mailbox_tasks_count": 0, "mailbox_failed_count": 0}),
                encoding="utf-8",
            )
            t_file = root / "transcript.jsonl"
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 8))
            t_e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 4))
            t_file.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 1000, "cache_read_tokens": 9000, "output_tokens": 400}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_e}),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            cfg_file = fake_home / ".agents" / "statusline_config.json"
            base_env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_STATUSLINE_CONFIG": str(cfg_file),
                "FAIZ_TRANSCRIPT_PATH": str(t_file),
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            payload = b'{"context_window":{"used_percentage":42}}'

            # 1. Auto wide (180 cols): Reset pill replaces Argon/Claude, WARN_PRO preserved
            cfg_file.write_text(json.dumps({"density": "auto"}), encoding="utf-8")
            res_wide = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "180"}, capture_output=True, check=True)
            plain_wide = re.sub(r"\x1b\[[0-9;]*m", "", res_wide.stdout.decode("utf-8", errors="replace"))
            lines_wide = plain_wide.splitlines()
            self.assertEqual(len(lines_wide), 4)
            self.assertIn("Reset 5h:2h", lines_wide[3])
            self.assertIn("Wk:5d", lines_wide[3])
            self.assertIn("Pro!", lines_wide[3])
            self.assertNotIn("Argon", plain_wide)
            self.assertNotIn("Claude", plain_wide)

            # 2. Auto narrow 90 cols (2x4): stays 4-line aligned full-width matrix (88c), stacks Reset in Col 4
            res_90 = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "90"}, capture_output=True, check=True)
            plain_90 = re.sub(r"\x1b\[[0-9;]*m", "", res_90.stdout.decode("utf-8", errors="replace"))
            lines_90 = plain_90.splitlines()
            self.assertEqual(len(lines_90), 4)
            self.assertEqual({len(ln) for ln in lines_90}, {88})
            self.assertIn("Gemini 15.25%·42.50%", lines_90[1])
            self.assertIn("Reset 2h", lines_90[3])
            self.assertNotIn("Arg", plain_90)
            self.assertNotIn("Claude", plain_90)

            # 3. Auto narrow 46 cols (4x2): stays 5-line aligned matrix, shows compact reset, no Argon/Claude
            res_46 = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "46"}, capture_output=True, check=True)
            plain_46 = re.sub(r"\x1b\[[0-9;]*m", "", res_46.stdout.decode("utf-8", errors="replace"))
            lines_46 = plain_46.splitlines()
            self.assertEqual(len(lines_46), 5)
            self.assertEqual(len({len(ln) for ln in lines_46}), 1)
            self.assertIn("2h", lines_46[3])
            self.assertNotIn("Arg", plain_46)
            self.assertNotIn("Cla", plain_46)

            # 4. Expanded mode (180 cols): single Reset capsule + WARN_PRO, plus capsule_colors.argon fallback
            cfg_file.write_text(json.dumps({"density": "expanded", "capsule_colors": {"argon": "green"}}), encoding="utf-8")
            res_exp = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "180"}, capture_output=True, check=True)
            out_exp = res_exp.stdout.decode("utf-8", errors="replace")
            plain_exp = re.sub(r"\x1b\[[0-9;]*m", "", out_exp)
            self.assertIn("Reset 5h:2h", plain_exp)
            self.assertIn("(Pro!)", plain_exp)
            self.assertNotIn("Argon", plain_exp)
            self.assertNotIn("Claude", plain_exp)
            self.assertIn("27;52;38", out_exp)  # green capsule glass bg from capsule_colors.argon fallback

            # 5. Compact mode: WARN_PRO ! badge + inline reset timer on Gem pill, and pace alert escalation
            cfg_file.write_text(json.dumps({"density": "compact", "color_mode": "semantic"}), encoding="utf-8")
            res_cmp_full = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "120"}, capture_output=True, check=True)
            plain_cmp_full = re.sub(r"\x1b\[[0-9;]*m", "", res_cmp_full.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Gem 15.25/42.50%!·↻2h", plain_cmp_full)
            self.assertNotIn("Arg", plain_cmp_full)
            self.assertNotIn("Cla", plain_cmp_full)

            # Over-pace 5h burn (runs out <=30m) while Pro >20% -> compact escalates to alert ("64;30;31")
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 25.0,
                        "pro_wk_pct": 90.0,
                        "pro_5h_reset_at_epoch": int(now + 4 * 3600),
                        "argon_wk_pct": None,
                        "quota_gate": "GREEN",
                        "pty_fetched_epoch": int(now - 5),
                    }
                ),
                encoding="utf-8",
            )
            res_cmp_low = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "120"}, capture_output=True, check=True)
            out_cmp_low = res_cmp_low.stdout.decode("utf-8", errors="replace")
            plain_cmp_low = re.sub(r"\x1b\[[0-9;]*m", "", out_cmp_low)
            self.assertIn("Gem 25.00/90.00%·↻", plain_cmp_low)
            self.assertNotIn("Cla", plain_cmp_low)
            self.assertIn("64;30;31", out_cmp_low)  # alert glass bg from <=30m run-out pace

            # 6. Missing Claude & Argon -> no dead Argon -- capsule emitted
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps({"pro_5h_pct": 85.0, "pro_wk_pct": 90.0, "argon_wk_pct": None, "quota_gate": "GREEN", "pty_fetched_epoch": int(now - 5)}),
                encoding="utf-8",
            )
            cfg_file.write_text(json.dumps({"density": "expanded"}), encoding="utf-8")
            res_no_cla = subprocess.run([str(STATUSLINE_SH)], input=payload, env={**base_env, "FAIZ_TERM_WIDTH": "160"}, capture_output=True, check=True)
            plain_no_cla = re.sub(r"\x1b\[[0-9;]*m", "", res_no_cla.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Gemini 5h:85.00% · Wk:90.00%", plain_no_cla)
            self.assertNotIn("Argon", plain_no_cla)
            self.assertNotIn("Claude", plain_no_cla)

    def test_statusline_new_session_ignores_previous_brain_transcript_and_shows_zero_burn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()

            old_dir = fake_home / ".gemini" / "antigravity-cli" / "brain" / "old-conv" / ".system_generated" / "logs"
            old_dir.mkdir(parents=True)
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10))
            (old_dir / "transcript.jsonl").write_text(
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 10975758, "cache_read_tokens": 0, "output_tokens": 50}) + "\n",
                encoding="utf-8",
            )
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 35.92,
                        "pro_wk_pct": 82.37,
                        "claude_5h_pct": 100.0,
                        "claude_wk_pct": 10.27,
                        "tokens_1h": 2631796,
                        "pty_fetched_epoch": int(now),
                    }
                ),
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            env.pop("FAIZ_TRANSCRIPT_PATH", None)
            env.pop("FAIZ_TERM_WIDTH", None)

            # Case A: empty conversation_id before first prompt (90 cols) -> full 2x4 symmetry with placeholders
            payload_a = json.dumps(
                {
                    "conversation_id": "",
                    "context_window": {"used_percentage": 0, "total_input_tokens": 0, "total_output_tokens": 0},
                    "terminal_width": 90,
                }
            ).encode("utf-8")
            res_a = subprocess.run([str(STATUSLINE_SH)], input=payload_a, env=env, capture_output=True, check=True)
            plain_a = re.sub(r"\x1b\[[0-9;]*m", "", res_a.stdout.decode("utf-8", errors="replace"))
            lines_a = plain_a.splitlines()
            self.assertEqual(len(lines_a), 4)
            self.assertEqual({len(ln) for ln in lines_a}, {88})
            self.assertIn("Ctx 0%", plain_a)
            self.assertIn("Cache --", plain_a)
            self.assertIn("Speed --", plain_a)
            self.assertIn("Gemini 35.92%·82.37%", lines_a[3])
            self.assertIn("Reset --", lines_a[3])
            self.assertNotIn("Claude", plain_a)
            self.assertIn("Burn 0", plain_a)
            self.assertNotIn("10.98M", plain_a)
            self.assertNotIn("2.6M", plain_a)
            self.assertNotIn("t/s", plain_a)

            # Case B: new conversation_id assigned, transcript_path not yet written
            payload_b = json.dumps(
                {
                    "session_id": "new-conv",
                    "conversation_id": "new-conv",
                    "transcript_path": str(fake_home / ".gemini" / "antigravity-cli" / "brain" / "new-conv" / ".system_generated" / "logs" / "transcript.jsonl"),
                    "context_window": {"used_percentage": 0, "total_input_tokens": 0, "total_output_tokens": 0},
                }
            ).encode("utf-8")

            res_b90 = subprocess.run([str(STATUSLINE_SH)], input=payload_b, env={**env, "FAIZ_TERM_WIDTH": "90"}, capture_output=True, check=True)
            plain_b90 = re.sub(r"\x1b\[[0-9;]*m", "", res_b90.stdout.decode("utf-8", errors="replace"))
            lines_b90 = plain_b90.splitlines()
            self.assertEqual(len(lines_b90), 4)
            self.assertEqual({len(ln) for ln in lines_b90}, {88})
            self.assertIn("Ctx 0%", plain_b90)
            self.assertIn("Cache --", plain_b90)
            self.assertIn("Speed --", plain_b90)
            self.assertIn("Burn 0", plain_b90)
            self.assertNotIn("10.98M", plain_b90)
            self.assertNotIn("t/s", plain_b90)

            res_b180 = subprocess.run([str(STATUSLINE_SH)], input=payload_b, env={**env, "FAIZ_TERM_WIDTH": "180"}, capture_output=True, check=True)
            plain_b180 = re.sub(r"\x1b\[[0-9;]*m", "", res_b180.stdout.decode("utf-8", errors="replace"))
            lines_b180 = plain_b180.splitlines()
            self.assertEqual(len(lines_b180), 4)
            self.assertEqual({len(ln) for ln in lines_b180}, {178})
            self.assertIn("Context 0%", plain_b180)
            self.assertIn("Cache --", plain_b180)
            self.assertIn("Speed --", plain_b180)
            self.assertIn("Session 0 · 1h:2.6M", plain_b180)
            self.assertNotIn("10.98M", plain_b180)
            self.assertNotIn("tok/s", plain_b180)

            # Case C: small decimal used_percentage (0.5) or >=1% with explicit total_input_tokens: 0 must NOT fall back to ls -1t
            for pct in (0.5, 1.5):
                payload_c = json.dumps(
                    {
                        "conversation_id": "",
                        "context_window": {"used_percentage": pct, "total_input_tokens": 0, "total_output_tokens": 0},
                        "terminal_width": 90,
                    }
                ).encode("utf-8")
                res_c = subprocess.run([str(STATUSLINE_SH)], input=payload_c, env=env, capture_output=True, check=True)
                plain_c = re.sub(r"\x1b\[[0-9;]*m", "", res_c.stdout.decode("utf-8", errors="replace"))
                self.assertIn("Burn 0", plain_c)
                self.assertIn("Cache --", plain_c)
                self.assertIn("Speed --", plain_c)
                self.assertNotIn("10.98M", plain_c)
                self.assertNotIn("t/s", plain_c)

    def test_statusline_concurrent_session_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10))
            t_u = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 2))

            dir_a = fake_home / ".gemini" / "antigravity-cli" / "brain" / "conv-a" / ".system_generated" / "logs"
            dir_b = fake_home / ".gemini" / "antigravity-cli" / "brain" / "conv-b" / ".system_generated" / "logs"
            dir_a.mkdir(parents=True)
            dir_b.mkdir(parents=True)
            t_a = dir_a / "transcript.jsonl"
            t_b = dir_b / "transcript.jsonl"

            t_a.write_text(
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 1000, "cache_read_tokens": 9000, "output_tokens": 500}) + "\n",
                encoding="utf-8",
            )
            t_b.write_text(
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 10000, "cache_read_tokens": 90000, "output_tokens": 2000}) + "\n",
                encoding="utf-8",
            )
            os.utime(t_a, (now - 5, now - 5))
            os.utime(t_b, (now - 5, now - 5))

            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_DASH_BIN": "/nonexistent-dash",
                "FAIZ_TERM_WIDTH": "180",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            env.pop("FAIZ_STATUSLINE_NO_BG", None)
            env.pop("FAIZ_TRANSCRIPT_PATH", None)

            pay_a = json.dumps({"conversation_id": "conv-a", "transcript_path": str(t_a), "context_window": {"used_percentage": 20}}).encode("utf-8")
            pay_b = json.dumps({"conversation_id": "conv-b", "transcript_path": str(t_b), "context_window": {"used_percentage": 30}}).encode("utf-8")

            res_a1 = subprocess.run([str(STATUSLINE_SH)], input=pay_a, env=env, capture_output=True, check=True)
            plain_a1 = re.sub(r"\x1b\[[0-9;]*m", "", res_a1.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Speed 100 tok/s", plain_a1)
            self.assertIn("Session 1.5K", plain_a1)

            res_b1 = subprocess.run([str(STATUSLINE_SH)], input=pay_b, env=env, capture_output=True, check=True)
            plain_b1 = re.sub(r"\x1b\[[0-9;]*m", "", res_b1.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Speed 400 tok/s", plain_b1)
            self.assertIn("Session 12.0K", plain_b1)

            spd_a = cache_dir / ".speed_conv-a.json"
            spd_b = cache_dir / ".speed_conv-b.json"
            burn_a = cache_dir / ".session_burn_conv-a"
            burn_b = cache_dir / ".session_burn_conv-b"
            deadline = time.monotonic() + 1.5
            while (not spd_a.exists() or not spd_b.exists()) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(spd_a.exists())
            self.assertTrue(spd_b.exists())
            self.assertTrue(burn_a.exists())
            self.assertTrue(burn_b.exists())
            burn_b_before = burn_b.read_text(encoding="utf-8")

            # Append USER_INPUT to both so speed relies on per-session speed cache for step_index=1
            with t_a.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"step_index": 2, "type": "USER_INPUT", "created_at": t_u}) + "\n")
                fh.write(json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_u, "input_tokens": 5000, "cache_read_tokens": 5000, "output_tokens": 0}) + "\n")
            with t_b.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"step_index": 2, "type": "USER_INPUT", "created_at": t_u}) + "\n")

            res_a2 = subprocess.run([str(STATUSLINE_SH)], input=pay_a, env=env, capture_output=True, check=True)
            plain_a2 = re.sub(r"\x1b\[[0-9;]*m", "", res_a2.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Speed 100 tok/s", plain_a2)
            self.assertNotIn("400 tok/s", plain_a2)
            self.assertIn("Session 6.5K", plain_a2)

            res_b2 = subprocess.run([str(STATUSLINE_SH)], input=pay_b, env=env, capture_output=True, check=True)
            plain_b2 = re.sub(r"\x1b\[[0-9;]*m", "", res_b2.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Speed 400 tok/s", plain_b2)
            self.assertNotIn("100 tok/s", plain_b2)
            self.assertIn("Session 12.0K", plain_b2)
            self.assertEqual(burn_b.read_text(encoding="utf-8").split("\t")[2], burn_b_before.split("\t")[2])

    def test_statusline_partial_trailing_jsonl_and_large_tail_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10))
            t_e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5))

            t_small = fake_home / "small_partial.jsonl"
            t_small.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 2000, "cache_read_tokens": 18000, "output_tokens": 500}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_e}),
                    ]
                )
                + '\n{"step_index": 3, "type": "PLANNER_RESPONSE", "status": ',
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_TRANSCRIPT_PATH": str(t_small),
                "FAIZ_TERM_WIDTH": "180",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            payload = json.dumps({"context_window": {"used_percentage": 25}}).encode("utf-8")
            res_small = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env, capture_output=True, check=True)
            plain_small = re.sub(r"\x1b\[[0-9;]*m", "", res_small.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Context 25%", plain_small)
            self.assertIn("Cache 90.0%", plain_small)
            self.assertIn("Speed 100 tok/s", plain_small)
            self.assertIn("Session 2.5K", plain_small)

            # Large transcript (>32KB) with 55 GENERIC lines after PLANNER_RESPONSE + truncated trailing line
            t_large = fake_home / "large_partial.jsonl"
            pad_blob = "x" * 700
            lines = [
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 2000, "cache_read_tokens": 18000, "output_tokens": 500}),
            ]
            for idx in range(2, 58):
                lines.append(json.dumps({"step_index": idx, "type": "GENERIC", "status": "DONE", "created_at": t_e, "pad": pad_blob}))
            t_large.write_text("\n".join(lines) + '\n{"step_index": 58, "type": "PLANNER_', encoding="utf-8")
            self.assertGreater(t_large.stat().st_size, 32768)

            res_large = subprocess.run(
                [str(STATUSLINE_SH)],
                input=payload,
                env={**env, "FAIZ_TRANSCRIPT_PATH": str(t_large)},
                capture_output=True,
                check=True,
            )
            plain_large = re.sub(r"\x1b\[[0-9;]*m", "", res_large.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Context 25%", plain_large)
            self.assertIn("Cache 90.0%", plain_large)
            self.assertIn("Speed 100 tok/s", plain_large)
            self.assertIn("Session 2.5K", plain_large)

    def test_statusline_scheme_a_full_width_2x4_precision_and_ultrawide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()
            t_s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 6))
            t_e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 2))

            t_active = fake_home / "active.jsonl"
            t_active.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_s, "input_tokens": 90000, "cache_read_tokens": 969348, "output_tokens": 652}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_e}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            t_cold = fake_home / "cold.jsonl"
            t_cs = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 360))
            t_ce = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 356))
            t_cold.write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": t_cs, "input_tokens": 370000, "cache_read_tokens": 11969132, "output_tokens": 868}),
                        json.dumps({"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": t_ce}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(t_cold, (now - 300, now - 300))

            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 97.84,
                        "pro_wk_pct": 80.74,
                        "pro_5h_reset_at_epoch": int(now + 4 * 3600 + 20 * 60),
                        "pro_wk_reset_at_epoch": int(now + 6 * 86400 + 12 * 3600),
                        "quota_gate": "GREEN",
                        "tokens_1h": 1800000,
                        "pty_fetched_epoch": int(now),
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "STOPPED", "mailbox_tasks_count": 0, "mailbox_failed_count": 2}),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_STATUSLINE_NO_BG": "1",
                "FAIZ_TRANSCRIPT_PATH": str(t_active),
                "FAIZ_TERM_WIDTH": "91",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            payload = json.dumps({"context_window": {"used_percentage": 21}}).encode("utf-8")

            # 1. 91-col Active keeps full 2dp Gemini precision, Reset countdown, Speed label, and 89/89 full fill
            res_91 = subprocess.run([str(STATUSLINE_SH)], input=payload, env=env, capture_output=True, check=True)
            lines_91 = re.sub(r"\x1b\[[0-9;]*m", "", res_91.stdout.decode("utf-8", errors="replace")).splitlines()
            self.assertEqual(len(lines_91), 4)
            self.assertEqual({len(ln) for ln in lines_91}, {89})
            self.assertIn("Speed 163t/s", lines_91[1])
            self.assertIn("Gemini 97.84%·80.74%", lines_91[1])
            self.assertIn("Reset 4h", lines_91[3])
            self.assertIn("·6d1", lines_91[3])
            self.assertNotIn("Claude", "\n".join(lines_91))
            self.assertTrue(lines_91[1].rstrip().endswith("Gemini 97.84%·80.74% "))

            # 2. 91-col Worst-Case Stress (cold + online + WARN_PRO + 2R · 1F) keeps full labels & 2dp at 89/89
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 100.0,
                        "pro_wk_pct": 100.0,
                        "quota_gate": "WARN_PRO",
                        "tokens_1h": 2400000,
                        "pty_fetched_epoch": int(now),
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "ONLINE", "idle_remaining_seconds": 2685, "mailbox_tasks_count": 2, "mailbox_failed_count": 1}),
                encoding="utf-8",
            )
            res_stress = subprocess.run(
                [str(STATUSLINE_SH)],
                input=json.dumps({"context_window": {"used_percentage": 95}}).encode("utf-8"),
                env={**env, "FAIZ_TRANSCRIPT_PATH": str(t_cold)},
                capture_output=True,
                check=True,
            )
            lines_stress = re.sub(r"\x1b\[[0-9;]*m", "", res_stress.stdout.decode("utf-8", errors="replace")).splitlines()
            self.assertEqual(len(lines_stress), 4)
            self.assertEqual({len(ln) for ln in lines_stress}, {89})
            self.assertIn("Cache 97% cold", lines_stress[1])
            self.assertIn("Speed 217t/s", lines_stress[1])
            self.assertIn("Gemini 100.00%·100.00%", lines_stress[1])
            self.assertIn("Reset Full!", lines_stress[3])
            self.assertNotIn("Claude", "\n".join(lines_stress))

            # 3. 240-col Ultrawide fills edge-to-edge (238/238, no 210c cap) with unified Col 4 quota alignment
            res_240 = subprocess.run(
                [str(STATUSLINE_SH)],
                input=payload,
                env={**env, "FAIZ_TERM_WIDTH": "240"},
                capture_output=True,
                check=True,
            )
            lines_240 = re.sub(r"\x1b\[[0-9;]*m", "", res_240.stdout.decode("utf-8", errors="replace")).splitlines()
            self.assertEqual(len(lines_240), 4)
            self.assertEqual({len(ln) for ln in lines_240}, {238})
            self.assertIn("Gemini 5h:100.00%", lines_240[1])
            self.assertIn("Reset Full Pro!", lines_240[3])
            self.assertNotIn("Claude", "\n".join(lines_240))

    def test_statusline_stdin_quota_width_priority_cache_fallback_and_expiry_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            now = time.time()

            # Active transcript WITHOUT PLANNER_RESPONSE (so Cache falls back to stdin.context_window.current_usage)
            t_file = root / "transcript_gen.jsonl"
            t_file.write_text(json.dumps({"step_index": 1, "type": "USER_INPUT"}) + "\n", encoding="utf-8")
            os.utime(t_file, (now - 2, now - 2))

            p_stamp = cache_dir / ".bg_pty_ts"
            p_stamp.write_text("", encoding="utf-8")
            os.utime(p_stamp, (now - 55, now - 55))

            marker = root / "dash_stdin_skip.log"
            fake_dash = root / "fake-dash"
            fake_dash.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {str(marker)!r}\n", encoding="utf-8")
            fake_dash.chmod(0o755)

            env = {
                **os.environ,
                "HOME": str(fake_home),
                "FAIZ_DASH_BIN": str(fake_dash),
                "FAIZ_TRANSCRIPT_PATH": str(t_file),
                "COLUMNS": "80",
                "TERM": "xterm-kitty",
                "COLORTERM": "truecolor",
            }
            env.pop("FAIZ_STATUSLINE_NO_BG", None)
            env.pop("FAIZ_TERM_WIDTH", None)

            # 1+2+3: No usage_snapshot.json; stdin.quota renders live 2dp Gemini & Claude, sets WARN_PRO (<20%),
            # skips PTY bg refresh despite 55s .bg_pty_ts & active transcript, terminal_width=180 beats COLUMNS=80,
            # and current_usage computes Cache 90%.
            (cache_dir / "cloud_snapshot.json").write_text(
                json.dumps({"colab_state": "STOPPED", "mailbox_tasks_count": 0, "mailbox_failed_count": 0}),
                encoding="utf-8",
            )
            payload_live = json.dumps(
                {
                    "terminal_width": 180,
                    "context_window": {
                        "used_percentage": 24,
                        "total_input_tokens": 10000,
                        "total_output_tokens": 500,
                        "current_usage": {
                            "input_tokens": 1000,
                            "output_tokens": 200,
                            "cache_read_input_tokens": 9000,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                    "quota": {
                        "gemini-5h": {"remaining_fraction": 0.1525, "reset_in_seconds": 7200},
                        "gemini-weekly": {"remaining_fraction": 0.8074, "reset_in_seconds": 500000},
                        "3p-5h": {"remaining_fraction": 0.9525, "reset_in_seconds": 7200},
                        "3p-weekly": {"remaining_fraction": 0.885, "reset_in_seconds": 500000},
                    },
                }
            ).encode("utf-8")
            res_live = subprocess.run([str(STATUSLINE_SH)], input=payload_live, env=env, capture_output=True, check=True)
            time.sleep(0.08)
            self.assertFalse(marker.exists())
            plain_live = re.sub(r"\x1b\[[0-9;]*m", "", res_live.stdout.decode("utf-8", errors="replace"))
            lines_live = plain_live.splitlines()
            self.assertEqual(len(lines_live), 4)
            self.assertEqual({len(ln) for ln in lines_live}, {178})
            self.assertIn("Cache 90.0%", lines_live[1])
            self.assertIn("Gemini 5h:15.25%", lines_live[1])
            self.assertIn("Wk:80.74%", lines_live[1])
            self.assertIn("Reset 5h:2h00m · Wk:5d18h · ~32m Pro!", lines_live[3])
            self.assertNotIn("Claude", plain_live)

            # 4: Cached reset-expiry 60s grace window
            env_nb = {**env, "FAIZ_STATUSLINE_NO_BG": "1", "FAIZ_TERM_WIDTH": "180"}
            base_payload = json.dumps({"context_window": {"used_percentage": 24}}).encode("utf-8")

            # Inside 60s grace (now - 30): must NOT prematurely reset
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 12.5,
                        "pro_wk_pct": 80.0,
                        "pro_5h_reset_at_epoch": int(now - 30),
                        "quota_gate": "WARN_PRO",
                        "pty_fetched_epoch": int(now - 100),
                    }
                ),
                encoding="utf-8",
            )
            res_grace = subprocess.run([str(STATUSLINE_SH)], input=base_payload, env=env_nb, capture_output=True, check=True)
            plain_grace = re.sub(r"\x1b\[[0-9;]*m", "", res_grace.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Gemini 5h:12.50% · Wk:80.00%", plain_grace)

            # Past 60s grace (now - 90): auto-resets pro_5h to 100.00% and clears WARN_PRO
            (cache_dir / "usage_snapshot.json").write_text(
                json.dumps(
                    {
                        "pro_5h_pct": 12.5,
                        "pro_wk_pct": 80.0,
                        "claude_5h_pct": 100.0,
                        "claude_wk_pct": 90.0,
                        "pro_5h_reset_at_epoch": int(now - 90),
                        "quota_gate": "WARN_PRO",
                        "pty_fetched_epoch": int(now - 100),
                    }
                ),
                encoding="utf-8",
            )
            res_exp = subprocess.run([str(STATUSLINE_SH)], input=base_payload, env=env_nb, capture_output=True, check=True)
            plain_exp = re.sub(r"\x1b\[[0-9;]*m", "", res_exp.stdout.decode("utf-8", errors="replace"))
            self.assertIn("Gemini 5h:100.00% · Wk:80.00%", plain_exp)
            self.assertNotIn("Pro!", plain_exp)

            # 5: Weekly-zero coupling (weekly == 0 couples 5h -> 0.00% and escalates WARN_PRO)
            payload_wk0 = json.dumps(
                {
                    "terminal_width": 91,
                    "context_window": {"used_percentage": 24},
                    "quota": {
                        "gemini-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 18000},
                        "gemini-weekly": {"remaining_fraction": 0.0, "reset_in_seconds": 300000},
                        "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 18000},
                        "3p-weekly": {"remaining_fraction": 0.0, "reset_in_seconds": 300000},
                    },
                }
            ).encode("utf-8")
            res_wk0 = subprocess.run([str(STATUSLINE_SH)], input=payload_wk0, env={**env, "FAIZ_STATUSLINE_NO_BG": "1"}, capture_output=True, check=True)
            plain_wk0 = re.sub(r"\x1b\[[0-9;]*m", "", res_wk0.stdout.decode("utf-8", errors="replace"))
            lines_wk0 = plain_wk0.splitlines()
            self.assertEqual(len(lines_wk0), 4)
            self.assertIn("Gemini 0.00%·0.00%", lines_wk0[1])
            self.assertIn("Reset 5h00m·3d11h!", lines_wk0[3])
            self.assertNotIn("Claude", plain_wk0)

    def test_install_sh_wire_settings_safe_merge_idempotent_and_invalid_json_guard(self) -> None:
        install_sh = REPO_ROOT / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            fake_home.mkdir()
            settings_f = fake_home / ".gemini" / "antigravity-cli" / "settings.json"
            env = {**os.environ, "HOME": str(fake_home)}

            # 1. Fresh install creates valid settings.json + copies scripts
            res1 = subprocess.run(
                ["bash", str(install_sh), "--copy", "--wire-settings", "--settings-path", str(settings_f)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Wired statusLine (new file)", res1.stdout)
            self.assertTrue((fake_home / ".agents" / "scripts" / "statusline.sh").exists())
            data1 = json.loads(settings_f.read_text(encoding="utf-8"))
            self.assertEqual(data1["statusLine"]["command"], "~/.agents/scripts/statusline.sh")

            # 2. Existing settings.json preserves custom keys & stack_with_default and writes .bak.*
            settings_f.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "statusLine": {"stack_with_default": False, "padding": 1, "command": "/old/cmd"},
                    }
                ),
                encoding="utf-8",
            )
            res2 = subprocess.run(
                ["bash", str(install_sh), "--copy", "--wire-settings", "--settings-path", str(settings_f)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("backup:", res2.stdout)
            data2 = json.loads(settings_f.read_text(encoding="utf-8"))
            self.assertEqual(data2["theme"], "dark")
            self.assertFalse(data2["statusLine"]["stack_with_default"])
            self.assertEqual(data2["statusLine"]["padding"], 1)
            self.assertEqual(data2["statusLine"]["command"], "~/.agents/scripts/statusline.sh")
            baks = list(settings_f.parent.glob("settings.json.bak.*"))
            self.assertEqual(len(baks), 1)

            # 3. Idempotent second run creates no extra backups
            res3 = subprocess.run(
                ["bash", str(install_sh), "--copy", "--wire-settings", "--settings-path", str(settings_f)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Already wired", res3.stdout)
            self.assertEqual(len(list(settings_f.parent.glob("settings.json.bak.*"))), 1)

            # 4. Corrupted JSON refuses to overwrite and exits non-zero
            settings_f.write_text("{corrupt json", encoding="utf-8")
            res4 = subprocess.run(
                ["bash", str(install_sh), "--copy", "--wire-settings", "--settings-path", str(settings_f)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res4.returncode, 0)
            self.assertIn("Invalid JSON", res4.stderr)
            self.assertEqual(settings_f.read_text(encoding="utf-8"), "{corrupt json")


if __name__ == "__main__":
    unittest.main()

"""Integration tests for Grok CLI capsule powerline (scripts/grok-statusline.sh)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
GROK_SL_SH = REPO_ROOT / "scripts" / "grok-statusline.sh"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


class TestGrokStatusline(unittest.TestCase):
    def _base_env(self, fake_home: Path, width: int = 120) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["COLUMNS"] = str(width)
        env.pop("FAIZ_TERM_WIDTH", None)
        env["TERM"] = "xterm-ghostty"
        env["TERM_PROGRAM"] = "ghostty"
        env["COLORTERM"] = "truecolor"
        env["GROK_STATUSLINE_HOST"] = "mozart"
        return env

    def test_2x3_capsules_telemetry_invariants_and_latency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            repo_dir = fake_home / "src" / "demo-repo"
            repo_dir.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
            (repo_dir / "dirty.txt").write_text("untracked\n", encoding="utf-8")

            now_s = int(time.time())
            updates_path = fake_home / "updates.jsonl"
            updates_path.write_text(
                json.dumps(
                    {
                        "timestamp": now_s - 12,
                        "method": "_x.ai/session/update",
                        "params": {
                            "sessionId": "sess-123",
                            "update": {
                                "sessionUpdate": "turn_completed",
                                "usage": {
                                    "inputTokens": 200000,
                                    "cachedReadTokens": 193600,
                                    "outputTokens": 1890,
                                    "modelCalls": 7,
                                    "apiDurationMs": 30000,
                                },
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            env = self._base_env(fake_home, width=128)
            payload = json.dumps(
                {
                    "session_id": "sess-123",
                    "version": "1.0.92",
                    "transcript_path": str(updates_path),
                    "model": {"id": "grok-4.7", "display_name": "Grok 4.7"},
                    "effort": {"level": "high"},
                    "workspace": {"current_dir": str(repo_dir), "branch": "main"},
                    "context_window": {
                        "used_percentage": 28,
                        "context_tokens": 35840,
                        "context_window_size": 131072,
                        "session_input_tokens": 1420000,
                        "session_output_tokens": 48000,
                        "auto_compact_threshold_percent": 85,
                    },
                    "cost": {"total_cost_usd": 1.24, "total_api_duration_ms": 30000},
                }
            ).encode("utf-8")

            # Seed caches once, then benchmark warm state run
            subprocess.run([str(GROK_SL_SH)], input=payload, env=env, capture_output=True, check=True)
            timings: list[float] = []
            for _ in range(3):
                t0 = time.perf_counter()
                res = subprocess.run([str(GROK_SL_SH)], input=payload, env=env, capture_output=True, check=True)
                timings.append((time.perf_counter() - t0) * 1000.0)

            self.assertLess(min(timings), 85.0)
            raw_lines = res.stdout.decode("utf-8").splitlines()
            # Grok status-line hard limits: <= 5 lines, each < 1024 bytes & chars
            self.assertEqual(len(raw_lines), 4)
            for ln in raw_lines:
                self.assertLess(len(ln), 1024)
                self.assertLess(len(ln.encode("utf-8")), 1024)

            plain = _strip_ansi("\n".join(raw_lines))
            self.assertIn("Ctx 28%", plain)
            self.assertIn("Cache 96.8%", plain)
            self.assertIn("Speed 63t/s", plain)
            self.assertIn("Grok 4.7·high", plain)
            self.assertIn("mozart:", plain)
            self.assertIn("(main*1)", plain)
            self.assertIn("$1.24 · 1.47M", plain)
            # Row 1 and Row 2 equalized to exact same visible column width
            plain_lines = [_strip_ansi(x) for x in raw_lines]
            self.assertEqual(len(plain_lines[1]), len(plain_lines[3]))

    def test_grok_line_and_byte_limits_across_all_widths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            payload = json.dumps(
                {
                    "session_id": "w-test",
                    "model": {"id": "nvidia/GLM-5.3-Flash-FP4-Local", "display_name": "GLM 5.3 Flash"},
                    "workspace": {"current_dir": str(fake_home / "obsidian-local" / "bmb-notes"), "branch": "feature/very-long-branch-name"},
                    "worktree": {"name": "worktree-refactor-2026"},
                    "prompt_id": "p-live-1",
                    "turn": {"started_at_ms": int((time.time() - 14) * 1000)},
                    "context_window": {
                        "used_percentage": 82,
                        "auto_compact_threshold_percent": 85,
                        "session_input_tokens": 84500,
                        "session_output_tokens": 3200,
                        "session_usage": {
                            "input_tokens": 8000,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 76500,
                            "output_tokens": 3200,
                        },
                    },
                }
            ).encode("utf-8")

            for width in (42, 58, 86, 95, 120, 168, 300):
                env = self._base_env(fake_home, width=width)
                env["GROK_STATUSLINE_NO_GIT"] = "1"
                res = subprocess.run([str(GROK_SL_SH)], input=payload, env=env, capture_output=True, check=True)
                lines = res.stdout.decode("utf-8").splitlines()
                self.assertGreaterEqual(len(lines), 1)
                self.assertLessEqual(len(lines), 5, f"exceeded 5-line ceiling at width={width}")
                for ln in lines:
                    self.assertLess(len(ln), 1024, f"exceeded 1024 chars at width={width}")
                    self.assertLess(len(ln.encode("utf-8")), 1024, f"exceeded 1024 bytes at width={width}")

                plain = _strip_ansi("\n".join(lines))
                if width >= 86:
                    self.assertIn("Local", plain)
                    self.assertIn("wt:", plain)
                    self.assertIn("Turn ", plain)

    def test_apple_terminal_16color_and_compact_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            cfg = fake_home / "sl_cfg.json"
            cfg.write_text(json.dumps({"density": "compact", "theme": "nord"}), encoding="utf-8")
            env = self._base_env(fake_home, width=100)
            env["TERM_PROGRAM"] = "Apple_Terminal"
            env["COLORTERM"] = ""
            env["TERM"] = "xterm"
            env["FAIZ_STATUSLINE_CONFIG"] = str(cfg)
            env["GROK_STATUSLINE_NO_GIT"] = "1"

            payload = json.dumps(
                {
                    "session_id": "t-apple",
                    "model": {"display_name": "Grok 4.7"},
                    "workspace": {"current_dir": str(fake_home), "branch": "main"},
                    "context_window": {"used_percentage": 45},
                    "cost": {"total_cost_usd": 0.42},
                }
            ).encode("utf-8")
            res = subprocess.run([str(GROK_SL_SH)], input=payload, env=env, capture_output=True, check=True)
            out = res.stdout.decode("utf-8")
            self.assertNotIn("\x1b[38;2;", out)
            plain = _strip_ansi(out)
            self.assertIn("Ctx 45%", plain)
            self.assertIn("mozart:Grok 4.7(main)·$0.42", plain)

    def test_large_updates_tail_fallback_and_config_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            updates_path = fake_home / "updates.jsonl"
            turn_line = json.dumps(
                {
                    "timestamp": int(time.time()) - 10,
                    "method": "_x.ai/session/update",
                    "params": {
                        "sessionId": "sess-big",
                        "update": {
                            "sessionUpdate": "turn_completed",
                            "usage": {
                                "inputTokens": 100000,
                                "cachedReadTokens": 95000,
                                "outputTokens": 2400,
                                "modelCalls": 3,
                                "apiDurationMs": 40000,
                            },
                        },
                    },
                }
            )
            # Append >70KB of non-turn_completed noise so tail -c 65536 misses turn_line
            trailing_noise = (json.dumps({"method": "_x.ai/session/update", "params": {"blob": "x" * 900}}) + "\n") * 80
            updates_path.write_text(turn_line + "\n" + trailing_noise, encoding="utf-8")

            cfg = fake_home / "sl_cfg.json"
            cfg.write_text(
                json.dumps(
                    {
                        "theme": "custom",
                        "colors": {"context": "magenta"},
                        "capsule_colors": {"sessions": "green"},
                        "modules": {"speed": False},
                    }
                ),
                encoding="utf-8",
            )
            env = self._base_env(fake_home, width=120)
            env["FAIZ_STATUSLINE_CONFIG"] = str(cfg)
            env["GROK_STATUSLINE_NO_GIT"] = "1"

            payload = json.dumps(
                {
                    "session_id": "sess-big",
                    "transcript_path": str(updates_path),
                    "model": {"display_name": "Grok 4.7 Build"},
                    "workspace": {"current_dir": str(fake_home / "src" / "faiz-control-center"), "branch": "main"},
                    "context_window": {
                        "used_percentage": 30,
                        "session_input_tokens": 100000,
                        "session_output_tokens": 2400,
                    },
                    "cost": {"total_cost_usd": 0.85},
                }
            ).encode("utf-8")

            res = subprocess.run([str(GROK_SL_SH)], input=payload, env=env, capture_output=True, check=True)
            out = res.stdout.decode("utf-8")
            plain = _strip_ansi(out)
            # Recovered turn_completed across >64KB tail noise
            self.assertIn("Cache 95.0%", plain)
            # speed module disabled -> omitted
            self.assertNotIn("Speed ", plain)
            # Full repo name preserved at width=120 without mid-word truncation
            self.assertIn("mozart:~/…/faiz-control-center(main)", plain)
            # capsule_colors.sessions fallback applied green slot ("46;74;60" in custom palette) to burn capsule
            self.assertIn("46;74;60", out)


if __name__ == "__main__":
    unittest.main()

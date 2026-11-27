"""Unit and CLI tests for `faiz-dash doctor` environment diagnostics."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from faiz_dash.__main__ import main as cli_main
from faiz_dash.doctor import format_doctor_output, run_doctor_checks


class TestDoctor(unittest.TestCase):
    def test_healthy_environment_and_privacy_masking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "alice_home"
            script_path = fake_home / ".agents" / "scripts" / "statusline.sh"
            script_path.parent.mkdir(parents=True)
            script_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            script_path.chmod(0o755)

            settings_path = fake_home / ".gemini" / "antigravity-cli" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "command",
                            "command": "~/.agents/scripts/statusline.sh",
                            "stack_with_default": False,
                        }
                    }
                ),
                encoding="utf-8",
            )

            cache_dir = fake_home / ".agents" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "usage_snapshot.json").write_text("{}", encoding="utf-8")
            (cache_dir / "cloud_snapshot.json").write_text("{}", encoding="utf-8")

            report = run_doctor_checks(
                home=fake_home,
                env={"COLORTERM": "truecolor", "TERM": "xterm-kitty", "LANG": "en_US.UTF-8"},
                which_fn=lambda name: f"/usr/bin/{name}" if name in {"bash", "jq", "agy", "rex"} else None,
                version_info=(3, 12, 4),
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["summary"]["fail"], 0)
            self.assertIn("\ue0b6", report["glyph_sample"])

            md = format_doctor_output(report, as_markdown=True, home=fake_home)
            self.assertIn("Faiz Control Center Doctor", md)
            self.assertNotIn("alice_home", md)
            self.assertIn("~/.agents/scripts/statusline.sh", md)

            raw_json = format_doctor_output(report, as_json=True, home=fake_home)
            parsed = json.loads(raw_json)
            self.assertTrue(parsed["ok"])
            self.assertEqual(len(parsed["checks"]), 9)
            self.assertNotIn("alice_home", raw_json)

    def test_broken_settings_json_fails_and_unwired_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            script_path = fake_home / ".agents" / "scripts" / "statusline.sh"
            script_path.parent.mkdir(parents=True)
            script_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            script_path.chmod(0o755)

            settings_path = fake_home / ".gemini" / "antigravity-cli" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text("{broken json", encoding="utf-8")

            bad_report = run_doctor_checks(
                home=fake_home,
                env={"COLORTERM": "truecolor", "LANG": "en_US.UTF-8"},
                which_fn=lambda name: f"/usr/bin/{name}" if name in {"bash", "jq"} else None,
            )
            self.assertFalse(bad_report["ok"])
            agy_chk = next(c for c in bad_report["checks"] if c["id"] == "agy_settings")
            self.assertEqual(agy_chk["status"], "fail")

            # Valid settings.json without statusLine -> ok=True but agy_settings warns with fix hint
            settings_path.write_text(json.dumps({"model": "gemini"}), encoding="utf-8")
            warn_report = run_doctor_checks(
                home=fake_home,
                env={"COLORTERM": "truecolor", "LANG": "en_US.UTF-8"},
                which_fn=lambda name: f"/usr/bin/{name}" if name in {"bash", "jq"} else None,
            )
            self.assertTrue(warn_report["ok"])
            agy_warn = next(c for c in warn_report["checks"] if c["id"] == "agy_settings")
            self.assertEqual(agy_warn["status"], "warn")
            self.assertIn("--wire-settings", agy_warn["fix"])

            # CLI integration test (`faiz-dash doctor --json`) with HOME override
            with mock.patch.dict(os.environ, {"HOME": str(fake_home), "COLORTERM": "truecolor"}):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli_main(["doctor", "--json"])
                self.assertEqual(rc, 0)
                parsed = json.loads(buf.getvalue())
                self.assertTrue(parsed["ok"])

                # Corrupt JSON makes CLI return exit code 1
                settings_path.write_text("{bad", encoding="utf-8")
                buf_bad = io.StringIO()
                with contextlib.redirect_stdout(buf_bad):
                    rc_bad = cli_main(["doctor", "--markdown"])
                self.assertEqual(rc_bad, 1)
                self.assertIn("FAILED", buf_bad.getvalue())

    def test_grok_statusline_doctor_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            for name in ("statusline.sh", "grok-statusline.sh"):
                p = fake_home / ".agents" / "scripts" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
                p.chmod(0o755)

            grok_cfg = fake_home / ".grok" / "config.toml"
            grok_cfg.parent.mkdir(parents=True)
            grok_cfg.write_text(
                '[ui.status_line]\ntype = "command"\ncommand = "~/.grok/statusline.sh"\npadding = 0\nrefresh_interval = 60\n',
                encoding="utf-8",
            )

            report = run_doctor_checks(
                home=fake_home,
                env={"COLORTERM": "truecolor", "LANG": "en_US.UTF-8"},
                which_fn=lambda name: f"/usr/bin/{name}" if name in {"bash", "jq", "grok"} else None,
            )
            grok_chk = next(c for c in report["checks"] if c["id"] == "grok_statusline")
            self.assertEqual(grok_chk["status"], "pass")

            grok_cfg.write_text("[broken toml", encoding="utf-8")
            bad_report = run_doctor_checks(
                home=fake_home,
                env={"COLORTERM": "truecolor", "LANG": "en_US.UTF-8"},
                which_fn=lambda name: f"/usr/bin/{name}" if name in {"bash", "jq", "grok"} else None,
            )
            self.assertFalse(bad_report["ok"])
            bad_chk = next(c for c in bad_report["checks"] if c["id"] == "grok_statusline")
            self.assertEqual(bad_chk["status"], "fail")


if __name__ == "__main__":
    unittest.main()

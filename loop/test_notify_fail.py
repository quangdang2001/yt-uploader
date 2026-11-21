"""loop/notify_fail.py and loop/run.ps1 (I6c): a failing step is never silent, and never a flood.

Chay tu goc repo:  python -m unittest loop.test_notify_fail -v
"""
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from loop import notify_fail as nf

REPO = Path(__file__).resolve().parent.parent


class NotifyFailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "cache"
        self.sent = []

    def run_nf(self, *argv, now="2026-11-20T03:00:00Z"):
        t = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return nf.main([*argv, "--cache", str(self.cache), "--config", str(Path(self.tmp.name) / "none.json")],
                       send=self.sent.append, now=t)

    def test_one_telegram_per_day_per_step_and_code(self):
        self.assertEqual(self.run_nf("fetch", "1"), 0)
        self.assertEqual(self.run_nf("fetch", "1", now="2026-11-20T10:00:00Z"), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("step fetch failed with exit code 1", self.sent[0])
        self.run_nf("fetch", "3")                   # other code: its own message
        self.run_nf("analyse", "1")                 # other step: its own message
        self.assertEqual(len(self.sent), 3)
        self.assertIn("token-drive-link.json", self.sent[1])
        self.run_nf("fetch", "1", now="2026-11-20T18:00:00Z")   # 01:00 next local day (UTC+7)
        self.assertEqual(len(self.sent), 4)
        state = json.loads((self.cache / nf.NOTIFIED_NAME).read_text(encoding="utf-8"))
        self.assertEqual(state["fetch:1"], "2026-11-21")

    def test_token_failure_key_shared_with_run_ps1(self):
        # fetch already sent its token Telegram today under "fetch:3": run.ps1's call stays quiet
        nf.notify_once(self.cache, "fetch:3", "token", now=datetime(2026, 11, 20, 3, tzinfo=timezone.utc),
                       send=self.sent.append)
        self.run_nf("fetch", "3")
        self.assertEqual(self.sent, ["token"])

    def test_run_ps1_calls_notify_fail_and_keeps_the_code(self):
        text = (REPO / "loop" / "run.ps1").read_text(encoding="utf-8")
        for step in ("fetch", "analyse"):
            block = re.search(r"python -m loop\.%s .*?\n(.*?)\n\}" % step, text, re.S).group(1)
            self.assertIn("$code = $LASTEXITCODE", block)
            self.assertIn("python -m loop.notify_fail %s $code" % step, block)
            self.assertIn("exit $code", block)
            self.assertLess(block.index("notify_fail"), block.index("exit $code"))


class HeartStepTest(unittest.TestCase):
    def test_run_ps1_calls_heart_non_fatal(self):
        text = (REPO / "loop" / "run.ps1").read_text(encoding="utf-8")
        self.assertIn("loop.heart", text)
        after = text[text.index("loop.heart"):]
        before_dash = after[:after.index("loop.dashboard")]
        self.assertIsNone(re.search(r"(?m)^\s*exit\b", before_dash))
        self.assertIn("Write-Warning", before_dash)
        self.assertIn("alerts.md", before_dash)
        self.assertLess(text.index("loop.ledger"), text.index("loop.heart"))


class RunbookTest(unittest.TestCase):
    def test_runbook_covers_setup_messages_and_release(self):
        text = (REPO / "docs" / "loop-pc.md").read_text(encoding="utf-8")
        for need in ("config.example.json", "studio_folder_id", "go_live", "TELEGRAM_BOT_TOKEN",
                     "TELEGRAM_CHAT_ID", '"User"', "token-drive-link.json", "release_manual",
                     "/SC HOURLY /MO 2", "powershell -NoProfile -ExecutionPolicy Bypass -File "
                     "D:\\Youtube\\n8n-upload\\loop\\run.ps1",
                     "Studio loop started:", "no fresh data from the VPS since", "(manual - alert only)",
                     "step fetch failed with exit code", "stale request"):
            self.assertIn(need, text)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from workbench import econf
from workbench.econf import EconfBox, mail_fetch_status
from workbench.store import Invalid, Store

SGT = timezone(timedelta(hours=8))
CLEAN = """[2026-10-27T05:15:04+08:00] START com.schwab.mail-export
[2026-10-27T05:15:07+08:00] END com.schwab.mail-export exit=0 seen=2 exported=2 read=2
[2026-10-27T20:15:17+08:00] START com.schwab.mail-export
[2026-10-27T20:15:19+08:00] END com.schwab.mail-export exit=0 seen=8 exported=7 read=7 launched=1 closed=1
"""


class MailStatusTests(unittest.TestCase):
    def status(self, text, now=None):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.log"
            p.write_text(text)
            return mail_fetch_status(p, now or datetime(2026, 10, 27, 21, 0, tzinfo=SGT))

    def test_last_round_is_summarised_with_launch_and_close(self):
        s = self.status(CLEAN)
        self.assertEqual((s["exported"], s["read"], s["launched"], s["closed"], s["running"]), (7, 7, True, True, False))
        self.assertIn("新收 7 封，标已读 7 封", s["text"])
        self.assertIn("Mail 是任务后台拉起的，读完已关闭 Mail", s["text"])

    def test_failed_round_says_so(self):
        failed = ("[2026-10-27T21:00:00+08:00] START com.schwab.mail-export\n"
                  "[2026-10-27T21:00:09+08:00] END com.schwab.mail-export exit=5 seen=0 exported=0 read=0\n")
        s = self.status(CLEAN + failed)
        self.assertEqual(s["exit"], 5)
        self.assertIn("失败（退出码 5）", s["text"])

    def test_round_without_an_end_line_is_running_until_it_goes_stale(self):
        log = CLEAN + "[2026-10-27T21:00:00+08:00] START com.schwab.mail-export\n"
        self.assertTrue(self.status(log, datetime(2026, 10, 27, 21, 1, tzinfo=SGT))["running"])
        self.assertFalse(self.status(log, datetime(2026, 10, 27, 22, 0, tzinfo=SGT))["running"])    # 过了 15 分钟，当它挂了

    def test_missing_or_empty_log_is_unknown(self):
        self.assertFalse(mail_fetch_status(Path("/nonexistent/export.log"))["known"])
        self.assertFalse(self.status("")["known"])


class FetchTests(unittest.TestCase):
    def box(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return EconfBox(Path(d.name) / "inbox", Store(d.name))

    def test_fetch_kickstarts_the_export_job(self):
        calls = []
        with mock.patch.object(econf.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stderr="")):
            out = self.box().fetch()
        self.assertTrue(out["started"])
        self.assertEqual(calls[0][:2], ["/bin/launchctl", "kickstart"])
        self.assertTrue(calls[0][2].endswith("/com.schwab.mail-export"))

    def test_missing_job_is_a_readable_error(self):
        with mock.patch.object(econf.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=113, stderr="Could not find service")):
            with self.assertRaises(Invalid) as ctx:
                self.box().fetch()
        self.assertIn("没装或没加载", str(ctx.exception))

    def test_view_carries_the_mail_status(self):
        self.assertIn("installed", self.box().view()["mail"])

    def test_button_only_shows_when_the_job_is_installed(self):
        from workbench.econf import mail_job_installed
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(mail_job_installed("x.job", tmp))
            (Path(tmp) / "x.job.plist").write_text("<plist/>")
            self.assertTrue(mail_job_installed("x.job", tmp))


if __name__ == "__main__":
    unittest.main()

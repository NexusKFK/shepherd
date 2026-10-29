import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from datetime import date

from workbench.sources import ET, Sources, held_note, pick_earnings
from workbench.sources import datetime as _dt


def today():
    return _dt.now(ET).date()


class HeldEarningsTests(unittest.TestCase):
    def calendar(self, data, positions):
        with tempfile.TemporaryDirectory() as root:
            digest = Path(root) / "tools/daily_digest"
            (digest / "cache").mkdir(parents=True)
            (digest / "calendar_data.json").write_text(json.dumps({"events": []}))
            (digest / "cache/earnings.json").write_text(json.dumps({"data": data}))
            return Sources(root).calendar([], [], {"positions": positions})

    def test_held_company_earnings_are_listed_with_what_i_hold(self):
        day = (today() + timedelta(days=5)).isoformat()
        out = self.calendar({"HD": day, "NOW": day},
                            [{"id": "a", "sym": "HD", "kind": "stock", "qty": 12, "side": "long"}])
        arm = next(e for e in out["events"] if e["symbol"] == "HD")
        self.assertTrue(arm["held"])
        self.assertEqual(arm["name"], "HD 财报")                     # 不带「待确认」，主列表里直接显示
        self.assertEqual(arm["held_note"], "持有正股 12 股")
        now = next(e for e in out["events"] if e["symbol"] == "NOW")
        self.assertNotIn("held", now)
        self.assertIn("待确认", now["name"])                          # 没持有的仍放进待确认折叠区

    def test_option_that_outlives_the_report_is_called_out(self):
        report = today() + timedelta(days=5)
        expiry = (report + timedelta(days=3)).isoformat()
        note = held_note([{"kind": "option", "label": "Dec18'26 30P", "side": "short", "qty": 2, "expiry": expiry}], report)
        self.assertIn("Dec18'26 30P 卖出 2 张", note)
        self.assertIn("这次财报在到期之前", note)
        early = held_note([{"kind": "option", "label": "x", "side": "long", "qty": 1,
                            "expiry": (report - timedelta(days=1)).isoformat()}], report)
        self.assertNotIn("注意", early)

    def test_combo_summary_rows_do_not_count_as_holdings(self):
        day = (today() + timedelta(days=2)).isoformat()
        out = self.calendar({"XLE": day}, [{"id": "s", "sym": "XLE", "kind": "option", "qty": 3, "display_only": True}])
        self.assertNotIn("held", out["events"][0])

    def test_dates_outside_the_window_are_dropped(self):
        far = (today() + timedelta(days=200)).isoformat()
        out = self.calendar({"HD": far}, [{"id": "a", "sym": "HD", "kind": "stock", "qty": 1, "side": "long"}])
        self.assertEqual(out["events"], [])


class CoreEarningsTests(unittest.TestCase):
    """大科技名单：M7、博通、台积电、SpaceX 的财报不管持不持有都要写进日历。"""

    def calendar(self, core, cache=None, positions=()):
        with tempfile.TemporaryDirectory() as root:
            digest = Path(root) / "tools/daily_digest"
            (digest / "cache").mkdir(parents=True)
            (digest / "calendar_data.json").write_text(json.dumps({"events": []}))
            (digest / "cache/earnings.json").write_text(json.dumps({"data": cache or {}}))
            if core is not None:
                (digest / "core_earnings.json").write_text(json.dumps({"symbols": core}))
            return Sources(root).calendar([], [], {"positions": list(positions)})

    def row(self, days, **kw):
        return dict({"name": "英伟达", "date": (today() + timedelta(days=days)).isoformat(),
                     "at": "2026-01-01T12:00:00Z", "confirmed": False, "time_known": False}, **kw)

    def test_core_company_is_listed_even_when_not_held_and_not_tentative(self):
        out = self.calendar({"NVDA": self.row(20)})
        e = out["events"][0]
        self.assertEqual((e["symbol"], e["name"], e["core"], e["core_name"]), ("NVDA", "NVDA 财报", True, "英伟达"))
        self.assertNotIn("待确认", e["name"])                           # 主列表里直接显示，不进「待确认」折叠区
        self.assertNotIn("held", e)
        self.assertIn("预估日期（TradingView）", e["source"])

    def test_core_company_is_shown_beyond_the_90_day_window_but_not_beyond_200(self):
        out = self.calendar({"META": self.row(150), "SPCX": self.row(250)})
        self.assertEqual([e["symbol"] for e in out["events"]], ["META"])
        self.assertTrue(any("SPCX" in w for w in out["errors"]))        # 超出范围的也要说一声，不能悄悄没了

    def test_a_confirmed_date_says_so_and_carries_the_report_time(self):
        row = self.row(3, confirmed=True, time_known=True, at="2026-10-28T20:00:00Z", date="2026-10-28")
        pick = pick_earnings(row, None, date(2026, 10, 27), date(2026, 10, 20))
        self.assertEqual((pick["day"], pick["time"], pick["confirmed"]), (date(2026, 10, 28), "16:00", True))
        self.assertIn("公司已公告", pick["source"])
        winter = pick_earnings(dict(row, date="2026-11-02", at="2026-11-02T21:30:00Z"), None, date(2026, 10, 27), date(2026, 10, 20))
        self.assertEqual(winter["time"], "16:30")                       # 夏令时结束后同样按美东换算

    def test_expired_list_date_falls_back_to_the_next_date_in_the_cache(self):
        old = self.row(-20)
        new_day = (today() + timedelta(days=60)).isoformat()
        out = self.calendar({"NVDA": old}, cache={"NVDA": new_day})
        self.assertEqual([(e["symbol"], e["day"]) for e in out["events"]], [("NVDA", new_day)])
        self.assertIn("雅虎财经", out["events"][0]["source"])
        out = self.calendar({"NVDA": old}, cache={})
        self.assertEqual(out["events"], [])
        self.assertTrue(any("NVDA" in w and "重新导入" in w for w in out["errors"]))

    def test_held_core_company_is_one_event_with_both_marks(self):
        out = self.calendar({"NVDA": self.row(10)}, positions=[{"id": "a", "sym": "NVDA", "kind": "stock", "qty": 5, "side": "long"}])
        self.assertEqual(len(out["events"]), 1)
        e = out["events"][0]
        self.assertTrue(e["core"] and e["held"])
        self.assertEqual(e["held_note"], "持有正股 5 股")

    def test_no_list_file_means_no_core_events_and_no_error(self):
        out = self.calendar(None)
        self.assertEqual(out["events"], [])
        self.assertFalse(any("大科技" in w or "名单" in w for w in out["errors"]))


if __name__ == "__main__":
    unittest.main()

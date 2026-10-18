import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from workbench import weekly_watch


def sample(**over):
    tier = lambda span, ema, first=False: {"span": span, "ema": ema, "dist_pct": -1.0, "dist_atr": -0.3, "close_below": False,
                                            "touched_this_week": first, "first_touch": first, "hist_reach": 56, "hist_deepest": 12}
    body = {"schema": "weekly-watch/1", "data_day": "2026-10-16",
            "symbols": {"VUG": {"symbol": "VUG", "tiers": [tier(20, 101.2, True), tier(50, 96.4)]},
                        "RSP": {"symbol": "RSP", "tiers": [tier(20, 180.3), tier(50, 175.0)]}},
            "triggers": [{"key": "warning", "severity": "amber", "label": "预警", "status": "off", "detail": ""},
                         {"key": "confirm", "severity": "red", "label": "确认：标普 500 周线反弹失败", "status": "off", "detail": ""},
                         {"key": "credit", "severity": "red", "label": "BB 利差 63 个交易日走阔 ≥ 90bp", "status": "off", "detail": "现在 +37bp"}]}
    body.update(over)
    return body


class WeeklyWatchTest(unittest.TestCase):
    def write(self, body):
        d = tempfile.mkdtemp()
        p = Path(d) / "state.json"
        p.write_text(json.dumps(body, ensure_ascii=False))
        return p

    def test_missing_file_is_reported_not_empty(self):
        w = weekly_watch.load(Path(tempfile.mkdtemp()) / "nope.json", today=date(2026, 10, 18))
        self.assertEqual(w["status"], "missing")
        self.assertEqual(weekly_watch.attention(w), [])

    def test_ok_and_first_touch_is_info(self):
        w = weekly_watch.load(self.write(sample()), today=date(2026, 10, 18))
        self.assertEqual(w["status"], "ok")
        lines = weekly_watch.attention(w)
        self.assertEqual([x[0] for x in lines], ["info"])
        self.assertIn("VUG 本周第一次碰到 20 周 EMA", lines[0][1])

    def test_trigger_on_is_red(self):
        body = sample()
        body["triggers"][1]["status"] = "on"
        lines = weekly_watch.attention(weekly_watch.load(self.write(body), today=date(2026, 10, 18)))
        self.assertEqual(lines[0][0], "red")
        self.assertIn("升红条件满足", lines[0][1])

    def test_warning_on_is_amber_not_red(self):
        body = sample()
        body["triggers"][0]["status"] = "on"
        lines = weekly_watch.attention(weekly_watch.load(self.write(body), today=date(2026, 10, 18)))
        self.assertEqual(lines[0][0], "amber")
        self.assertIn("周线预警", lines[0][1])

    def test_armed_warning_is_info_with_price(self):
        body = sample()
        body["triggers"][0]["armed"] = {"price": 96.4, "dist_pct": -4.2, "dist_atr": -1.3, "rsp_floor": 175.0}
        lines = weekly_watch.attention(weekly_watch.load(self.write(body), today=date(2026, 10, 18)))
        self.assertIn("info", [x[0] for x in lines])
        armed = [x for x in lines if x[1].startswith("重点观察")]
        self.assertEqual(armed[0][1], "重点观察：VUG 周收盘低于 96.4 就出预警")
        self.assertIn("RSP 要收在 175 以上", armed[0][2])

    def test_unknown_trigger_is_amber(self):
        body = sample()
        body["triggers"][2]["status"] = "unknown"
        lines = weekly_watch.attention(weekly_watch.load(self.write(body), today=date(2026, 10, 18)))
        self.assertIn(("amber", "周线条件读不到：BB 利差 63 个交易日走阔 ≥ 90bp", "现在 +37bp"), lines)

    def test_stale_file_is_amber_and_hides_details(self):
        w = weekly_watch.load(self.write(sample()), today=date(2026, 10, 30))
        self.assertEqual(w["status"], "stale")
        lines = weekly_watch.attention(w)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0], "amber")

    def test_missing_fields_is_invalid(self):
        w = weekly_watch.load(self.write({"data_day": "2026-10-16"}), today=date(2026, 10, 18))
        self.assertEqual(w["status"], "invalid")


if __name__ == "__main__":
    unittest.main()

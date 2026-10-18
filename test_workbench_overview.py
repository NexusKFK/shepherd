import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from workbench import overview

TODAY = date(2026, 9, 30)  # Wednesday


def state(**over):
    base = {
        "day": "2026-09-30",
        "snapshot": {"status": "available", "day": "2026-09-29"},
        "exposure": {"market_day": "2026-09-29",
                     "macro_panel": {"status": "ok", "data_day": "2026-09-28"},
                     "portfolio_heat": {"items": [], "no_stop": [], "unknown": [], "unbounded": [], "status": "ok"},
                     "factor_clusters": {"clusters": []},
                     "market_scenario": {"coverage": {"used": 18, "total": 18}}},
        "brief": {"day": "2026-09-29", "status": "ok"},
        "positions": [], "calendar": {"events": []}, "econf": {"pending": 0}, "conflicts": [],
        "preparation": {"checks": ["snapshot", "events", "plans"]},
    }
    base.update(over)
    return base


def titles(result):
    return [row["title"] for row in result["attention"]]


class FreshnessTests(unittest.TestCase):
    def test_missed_trading_days_skips_weekend_and_today(self):
        self.assertEqual(overview.missed_trading_days(date(2026, 9, 29), TODAY), 0)
        self.assertEqual(overview.missed_trading_days(date(2026, 9, 25), date(2026, 9, 29)), 1)  # Fri -> Tue misses Mon
        self.assertEqual(overview.missed_trading_days(date(2026, 9, 25), date(2026, 9, 28)), 0)  # Fri -> Mon: weekend only
        self.assertEqual(overview.missed_trading_days(None, TODAY), 0)

    def test_fresh_state_is_quiet(self):
        result = overview.build(state(), TODAY)
        self.assertEqual(result["attention"], [])
        self.assertTrue(all(r["level"] == "ok" for r in result["freshness"]))

    def test_stale_sources_are_reported_not_hidden(self):
        s = state(snapshot={"status": "available", "day": "2026-09-25"},
                  brief={"day": "2026-09-24", "status": "stale", "error": "盘前资料超过 4 天未更新"})
        s["exposure"]["market_day"] = "2026-09-25"
        result = overview.build(s, date(2026, 9, 29))
        levels = {r["key"]: r["level"] for r in result["freshness"]}
        self.assertEqual((levels["snapshot"], levels["market"], levels["brief"]), ("warn", "warn", "warn"))
        self.assertIn("持仓数据 2026-09-25 · 落后 1 个交易日", titles(result))
        self.assertTrue(any(x.startswith("盘前资料：漏了") for x in titles(result)))  # missed digests are named

    def test_unavailable_snapshot_and_missing_macro_are_red(self):
        s = state(snapshot={"status": "unavailable", "day": "2026-09-20"})
        s["exposure"]["macro_panel"] = {"status": "missing", "notice": "宏观面板文件不存在"}
        result = overview.build(s, TODAY)
        red = [r["title"] for r in result["attention"] if r["severity"] == "red"]
        self.assertEqual(len(red), 2)
        self.assertTrue(any("持仓数据" in t for t in red) and any("宏观数据" in t for t in red))

    def test_macro_tolerates_one_missed_day(self):
        s = state()
        s["exposure"]["macro_panel"]["data_day"] = "2026-09-28"  # missed 09-29 only
        self.assertEqual(overview.build(s, TODAY)["freshness"][2]["level"], "ok")
        s["exposure"]["macro_panel"]["data_day"] = "2026-09-25"
        self.assertEqual(overview.build(s, TODAY)["freshness"][2]["level"], "warn")


ET = ZoneInfo("America/New_York")


class BriefDueTests(unittest.TestCase):
    def test_expected_close_moves_at_report_time(self):
        mon_morning = datetime(2026, 10, 12, 8, 30, tzinfo=ET)
        self.assertEqual(overview.expected_brief_day(mon_morning), date(2026, 10, 8))   # Fri report used Thu close
        self.assertEqual(overview.expected_brief_day(mon_morning.replace(hour=14)), date(2026, 10, 9))
        self.assertEqual(overview.expected_brief_day(datetime(2026, 10, 10, 12, tzinfo=ET)), date(2026, 10, 8))  # Sat

    def test_missing_monday_digest_is_amber_not_green(self):
        s = state(day="2026-10-12", brief={"day": "2026-10-08", "status": "available"})
        result = overview.build(s, date(2026, 10, 12), datetime(2026, 10, 12, 14, 24, tzinfo=ET))
        brief = next(r for r in result["freshness"] if r["key"] == "brief")
        self.assertEqual(brief["level"], "warn")
        self.assertIn("漏了 1 期", brief["note"])
        self.assertTrue(any(i["severity"] == "amber" and i["title"].startswith("盘前资料") for i in result["attention"]))

    def test_before_report_time_yesterdays_digest_is_still_current(self):
        s = state(day="2026-10-12", brief={"day": "2026-10-08", "status": "available"})
        result = overview.build(s, date(2026, 10, 12), datetime(2026, 10, 12, 8, 30, tzinfo=ET))
        self.assertEqual(next(r for r in result["freshness"] if r["key"] == "brief")["level"], "ok")

    def test_every_row_says_when_it_refreshes(self):
        now = datetime(2026, 10, 12, 14, 24, tzinfo=ET)  # Mon 14:24 ET = 18:24 UTC
        saved, overview.DISPLAY_TZ = overview.DISPLAY_TZ, timezone.utc
        try:
            rows = {r["key"]: r for r in overview.build(state(), date(2026, 10, 12), now)["freshness"]}
        finally:
            overview.DISPLAY_TZ = saved
        self.assertEqual(rows["snapshot"]["next"], "10-12 20:05")  # 16:05 ET close, shown in the display zone
        self.assertEqual(rows["brief"]["next"], "10-13 12:45")
        self.assertEqual(rows["macro"]["next"], "10-12 23:50")
        self.assertTrue(all(r["cadence"] for r in rows.values()))


class AttentionTests(unittest.TestCase):
    def test_stop_flag_and_expiry_rank_above_budget_notes(self):
        s = state(positions=[
            {"sym": "IEF", "label": "Sep30'26 96C", "kind": "option", "side": "long", "qty": 2, "mult": 100,
             "mark": 0.155, "expiry": "2026-09-30"},
            {"sym": "IAU", "label": "Mar19'27 420C", "kind": "option", "side": "long", "qty": 1, "mult": 100,
             "mark": 8.8, "expiry": "2027-03-19"}])
        s["exposure"]["portfolio_heat"]["items"] = [
            {"symbol": "IAU", "label": "Mar19'27 420C", "flag": "现价已在止损之外，需要立即处理", "stop": "止损 IAU 385"}]
        s["exposure"]["factor_clusters"]["clusters"] = [
            {"factor": "非科技", "status": "near", "stress_loss_r": 2.7, "cap_r": 3.0, "cap_base_r": 4}]
        rows = overview.build(s, TODAY)["attention"]
        self.assertEqual([r["severity"] for r in rows], ["red", "red", "amber"])
        self.assertIn("今天到期", rows[1]["title"])
        self.assertIn("$31", rows[1]["detail"])  # long premium at risk, from mark x qty x multiplier
        self.assertEqual(rows[2]["detail"], "宏观灯已把上限 4→3 R")
        self.assertTrue(all("420C" not in r["title"] or "止损" in r["title"] for r in rows))  # far expiry is not listed

    def test_short_option_near_expiry_mentions_assignment_not_premium(self):
        s = state(positions=[{"sym": "IAU", "label": "C", "kind": "option", "side": "short", "qty": 6, "mult": 100,
                              "mark": 0.7, "expiry": "2026-10-01"}])
        row = overview.build(s, TODAY)["attention"][0]
        self.assertEqual(row["severity"], "red")
        self.assertIn("明天到期", row["title"])
        self.assertIn("指派", row["detail"])

    def test_expiry_horizon_and_display_only_rows(self):
        s = state(positions=[
            {"sym": "A", "label": "x", "kind": "option", "side": "long", "qty": 1, "mark": 1, "expiry": "2026-10-03"},
            {"sym": "B", "label": "x", "kind": "option", "side": "long", "qty": 1, "mark": 1, "expiry": "2026-10-04"},
            {"sym": "C", "label": "x", "kind": "option", "side": "long", "qty": 1, "mark": 1, "expiry": "2026-09-30",
             "display_only": True}])
        rows = overview.build(s, TODAY)["attention"]
        self.assertEqual([r["severity"] for r in rows], ["amber"])  # 3 days out; 4 days out and combo summary skipped
        self.assertIn("A", rows[0]["title"])

    def test_no_stop_row_lists_only_stocks_the_owner_has_not_dealt_with(self):
        s = state()
        s["exposure"]["portfolio_heat"]["no_stop"] = [{"symbol": "UNH", "value_usd": 3500, "group": ""}]
        s["exposure"]["portfolio_heat"]["exempt"] = [{"symbol": "CRM", "value_usd": 2600, "group": "长期持有（免判据）"}]
        row = overview.build(s, TODAY)["attention"][0]
        self.assertEqual(row["title"], "无止损正股 $3,500")
        self.assertEqual(row["detail"], "UNH")
        s["exposure"]["portfolio_heat"]["no_stop"] = []
        self.assertEqual(overview.build(s, TODAY)["attention"], [])

    def test_cluster_over_is_red_ok_and_unknown_are_silent(self):
        s = state()
        s["exposure"]["factor_clusters"]["clusters"] = [
            {"factor": "科技", "status": "over", "stress_loss_r": 3.4, "cap_r": 3.0, "cap_base_r": 3.0},
            {"factor": "黄金", "status": "ok", "stress_loss_r": 0.7, "cap_r": 1.5},
            {"factor": "利率", "status": "r_unavailable", "stress_loss_r": None, "cap_r": 1.5}]
        rows = overview.build(s, TODAY)["attention"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["severity"], rows[0]["detail"]), ("red", ""))
        self.assertIn("达到或超过上限", rows[0]["title"])

    def test_bookkeeping_items_and_scenario_coverage(self):
        s = state(econf={"pending": 6, "pending_legs": 12}, preparation={"checks": ["snapshot"]},
                  positions=[{"sym": "MU", "confirm": True, "kind": "stock"}], conflicts=["IAU 数量不一致"])
        s["exposure"]["market_scenario"]["coverage"] = {"used": 0, "total": 18}
        rows = overview.build(s, TODAY)["attention"]
        got = {r["title"]: r for r in rows}
        self.assertEqual(got["6 份券商确认单待核对"]["page"], "journal")
        self.assertIn("大盘情景仅覆盖 0 / 18 条", got)
        self.assertIn("1 条持仓待核对", got)
        self.assertEqual(got["盘前检查 1 / 3 未完成"]["severity"], "info")
        self.assertEqual(rows[-1]["severity"], "info")  # info sorts last

    def test_unconfirmed_nlv_is_named(self):
        s = state()
        s["exposure"]["portfolio_heat"]["status"] = "r_unavailable"
        self.assertEqual(overview.build(s, TODAY)["attention"][0]["page"], "plans")


class UpcomingTests(unittest.TestCase):
    def test_upcoming_sorted_from_today_and_flags_tentative(self):
        events = [
            {"day": "2026-09-01", "name": "过去的", "kind": "自定义"},
            {"day": "2026-10-09", "name": "GE 财报日期待确认", "kind": "财报", "symbol": "GE"},
            {"day": "2026-09-30", "name": "IEF · Sep30'26 96C", "kind": "到期", "symbol": "IEF"},
            {"day": "2026-10-02", "name": "非农就业 (9月)", "kind": "NFP", "time": "08:30"}]
        rows = overview.build(state(calendar={"events": events}), TODAY)["upcoming"]
        self.assertEqual([r["days"] for r in rows], [0, 2, 9])
        self.assertEqual([r["tentative"] for r in rows], [False, False, True])
        self.assertEqual(len(overview.upcoming(state(calendar={"events": events * 5}), TODAY, limit=4)), 4)

    def test_missing_calendar_is_empty_not_error(self):
        self.assertEqual(overview.build(state(calendar={}), TODAY)["upcoming"], [])


if __name__ == "__main__":
    unittest.main()

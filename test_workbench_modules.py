"""Optional modules, a ledger-only plan's stop in heat, and the demo data set."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from workbench import modules, overview
from workbench.application import Application
from workbench.demo import build as build_demo
from workbench.sources import ET, merged_positions
from workbench.store import Invalid, Store


class ModuleSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_ledger_is_not_onboarded_and_starts_with_the_defaults(self):
        m = modules.current(self.store)
        self.assertFalse(m["onboarded"])
        self.assertEqual({k for k, v in m["enabled"].items() if v}, {"calendar", "optionPlans"})

    def test_confirming_r_during_the_walkthrough_keeps_it_a_new_ledger(self):
        modules.current(self.store)                      # the app reads state before the walkthrough
        self.store.save_risk_policy({"version": 0, "nlv": 50000, "risk_pct": 2})
        m = modules.current(self.store)
        self.assertFalse(m["onboarded"])
        self.assertFalse(m["enabled"]["weekly"])

    def test_ledger_from_before_modules_keeps_every_module(self):
        self.store.save_risk_policy({"version": 0, "nlv": 50000, "risk_pct": 2})
        m = modules.current(self.store)
        self.assertTrue(m["onboarded"])
        self.assertTrue(all(m["enabled"].values()))

    def test_save_checks_keys_and_hides_pages(self):
        with self.assertRaises(Invalid):
            modules.save(self.store, {"enabled": {"nope": True}})
        with self.assertRaises(Invalid):
            modules.save(self.store, {"enabled": {"paper": "yes"}})
        saved = modules.save(self.store, {"enabled": {"paper": True}})
        self.assertTrue(saved["onboarded"])
        self.assertTrue(saved["enabled"]["paper"])
        self.assertFalse(saved["enabled"]["weekly"])
        self.assertIn("weekly", modules.hidden_pages(saved["enabled"]))
        self.assertNotIn("paper", modules.hidden_pages(saved["enabled"]))
        self.assertEqual(modules.current(self.store), saved)  # survives a reload


class OverviewRespectsModulesTests(unittest.TestCase):
    def state(self, enabled):
        return {"snapshot": {"status": "available", "day": "2026-10-16"},
                "exposure": {"market_day": "2026-10-16", "macro_panel": {"status": "missing", "notice": "没有文件"}},
                "brief": {}, "econf": {"pending": 2, "pending_legs": 2},
                "paper": {"status": "invalid", "notice": "坏文件"}, "weekly_watch": {"status": "invalid", "notice": "坏文件"},
                "preparation": {"checks": []}, "modules": {"enabled": enabled}}

    def test_disabled_modules_raise_nothing(self):
        out = overview.build(self.state({k: False for k in modules.KEYS}), datetime(2026, 10, 19).date())
        self.assertEqual([r["key"] for r in out["freshness"]], ["snapshot", "market"])
        self.assertEqual(out["attention"], [])

    def test_enabled_modules_still_report_their_sources(self):
        out = overview.build(self.state({k: True for k in modules.KEYS}), datetime(2026, 10, 19).date())
        titles = " ".join(r["title"] for r in out["attention"])
        for word in ("宏观", "纸盘", "周线", "确认单", "盘前"):
            self.assertIn(word, titles)


class PlanStopTests(unittest.TestCase):
    def test_ledger_only_holding_uses_the_plan_stop_and_snapshot_quote(self):
        plan = {"id": "x", "status": "holding", "symbol": "HD", "title": "HD", "kind": "stock", "side": "long",
                "open_qty": 10, "mult": 1, "avg_entry": 396.0, "broker": "A", "expiry": None, "stop": 372.0}
        rows, conflicts = merged_positions({"positions": [], "quotes": {"HD": 401.5}}, [plan])
        self.assertEqual(conflicts, [])
        self.assertEqual(rows[0]["mark"], 401.5)
        self.assertEqual(rows[0]["trigs"][0]["level"], 372.0)
        self.assertEqual(rows[0]["trigs"][0]["dir"], "<=")
        short = dict(plan, side="short", stop=420.0)
        self.assertEqual(merged_positions({"positions": []}, [short])[0][0]["trigs"][0]["dir"], ">=")
        self.assertNotIn("trigs", merged_positions({"positions": []}, [dict(plan, stop=None)])[0][0])


class DemoTests(unittest.TestCase):
    def test_demo_builds_a_complete_state_inside_its_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = build_demo(tmp, now=datetime(2026, 10, 19, 10, 0, tzinfo=ET))
            app = Application(out / "data", out / "sources", out / "sources/tools/schwab_confirms/inbox")
            s = app.state()
            self.assertEqual(s["snapshot"]["status"], "available")
            self.assertEqual(s["snapshot"]["nlv"], 100000.0)
            self.assertFalse(s["modules"]["onboarded"])  # the demo opens on the R walkthrough
            self.assertEqual(s["risk_policy"]["nlv"], 100000.0)
            self.assertEqual([(p["symbol"], p["status"]) for p in s["plans"]],
                             [("HD", "holding"), ("DE", "ready"), ("PG", "draft")])
            self.assertEqual(s["calendar"]["events"][0]["day"] >= "2026-10-12", True)
            for key in ("paper", "weekly_watch"):
                self.assertEqual(s[key]["status"], "ok", key)
            self.assertEqual(s["exposure"]["macro_panel"]["status"], "ok")
            self.assertEqual(s["option_plans"]["cards"][0]["status"], "ok")
            self.assertEqual(s["econf"]["pending"], 1)
            self.assertEqual(s["calendar"]["errors"], [])
            self.assertIsNone(s["overview"].get("error"))
            self.assertEqual({r["level"] for r in s["overview"]["freshness"]}, {"ok"})
            self.assertIsNotNone(s["exposure"]["portfolio_heat"]["heat_r"])
            text = json.dumps(s, ensure_ascii=False)
            self.assertNotIn(str(Path.home()), text.replace(tmp, ""))  # nothing read from the real home folder


if __name__ == "__main__":
    unittest.main()

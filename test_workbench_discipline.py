"""Fixed-R behavior, migration boundaries and factual ledger accounting."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from workbench.store import Invalid, Store, et_today, clean_plan, now, size_plan


def plan(**updates):
    return dict(symbol="TEST", title="标准交易", kind="stock", side="long", broker="IBKR",
                thesis="计划理由", invalidation="价格失效", exit_rule="按计划退出", entry=250,
                stop=240, **updates)


class FixedRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def policy(self, **changes):
        return self.store.save_risk_policy(dict(self.store.risk_policy(), nlv=10000, **changes))

    def ready(self, raw=None):
        p = self.store.save_plan(raw or plan())
        return self.store.transition(p["id"], {"status": "ready", "version": p["version"]})

    def fill(self, p, qty, price=250, action="entry", fee=0):
        return self.store.fill(p["id"], {"version": p["version"], "request_id": f"r-{p['version']}",
                              "action": action, "qty": qty, "price": price,
                              "fee": fee, "day": et_today().isoformat()})

    def test_policy_must_be_explicit_before_ready(self):
        defaults = self.store.risk_policy()
        self.assertEqual((defaults["risk_pct"], defaults["max_risk_pct"], defaults["min_utilization"]), (2, 4, .9))
        self.assertIsNone(defaults["nlv"])
        raw = plan(nlv=1000000, risk_pct=4)
        result = self.store.size(raw)
        self.assertFalse(result["eligible"])
        self.assertIn("组合净值", result["blockers"][0])
        with self.assertRaises(Invalid):
            self.ready(raw)

    def test_plan_cannot_override_global_risk_or_nav(self):
        self.policy()
        raw = plan(nlv=1000000, risk_pct=99)
        sized = self.store.size(raw)
        self.assertEqual((sized["budget"], sized["target_qty"]), (200, 20))
        self.assertTrue(sized["eligible"])
        p = self.ready(raw)
        self.assertEqual((p["nlv"], p["risk_pct"]), (10000, 2))
        self.assertEqual(p["discipline"]["baseline_risk"], 200)

    def test_policy_limits_versions_and_audit_are_atomic(self):
        for invalid in ({"nlv": 0}, {"risk_pct": 0}, {"risk_pct": 4.01}, {"nlv": float("nan")}, {"risk_pct": True}):
            with self.assertRaises(Invalid):
                self.store.save_risk_policy(dict({"version": 0, "nlv": 10000, "risk_pct": 2}, **invalid))
        self.assertEqual(self.store.risk_policy()["version"], 0)
        self.assertEqual(self.store.export()["tables"]["audit"], [])
        changed = self.policy()
        with self.assertRaises(Invalid):
            self.store.save_risk_policy({"version": 0, "nlv": 20000, "risk_pct": 4})
        self.assertEqual(self.store.risk_policy(), changed)
        self.assertEqual([r["kind"] for r in self.store.export()["tables"]["audit"]], ["risk_policy_saved"])

    def test_concurrent_policy_updates_only_one_wins(self):
        def update(_):
            try:
                self.store.save_risk_policy({"version": 0, "nlv": 10000, "risk_pct": 2})
                return True
            except Invalid:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(update, range(4)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.store.risk_policy()["version"], 1)

    def test_ninety_to_one_hundred_percent_is_hard_ready_band(self):
        self.policy()
        for qty in (0, 2, 17, 21, 30):
            with self.subTest(qty=qty):
                raw = plan(planned_qty=qty)
                self.assertFalse(self.store.size(raw)["eligible"])
                with self.assertRaises(Invalid):
                    self.ready(raw)
        for qty in (18, 19, 20):
            self.assertTrue(self.store.size(plan(planned_qty=qty))["eligible"])
        with self.assertRaises(Invalid):
            self.store.save_plan(plan(planned_qty=18.5))

    def test_integer_granularity_stays_in_observation_without_rounding_up(self):
        self.policy()
        raw = dict(plan(), kind="option", entry=1.1, mult=100)
        sized = self.store.size(raw)
        self.assertEqual((sized["target_qty"], sized["planned_qty"]), (1, 1))
        self.assertAlmostEqual(sized["utilization"], .55)
        self.assertFalse(sized["eligible"])
        self.assertTrue(any("不适配" in text for text in sized["blockers"]))

    def test_one_expensive_share_is_valid_by_risk_but_leverage_is_visible(self):
        self.policy()
        sized = self.store.size(dict(plan(), entry=25000, stop=24800))
        self.assertEqual(sized["target_qty"], 1)
        self.assertTrue(sized["eligible"])
        self.assertTrue(any("杠杆" in text for text in sized["warnings"]))
        self.assertFalse(self.store.size(dict(plan(), entry=25000, stop=24700))["eligible"])

    def test_unknown_short_option_risk_never_qualifies(self):
        self.policy()
        sized = self.store.size(dict(plan(), kind="option", side="short", entry=2))
        self.assertFalse(sized["eligible"])
        self.assertIn("最大亏损", sized["blockers"][0])

    def test_ready_freezes_r_and_policy_edit_does_not_rebase_it(self):
        self.policy()
        p = self.ready()
        frozen = p["sizing"]
        self.store.save_risk_policy(dict(self.store.risk_policy(), nlv=50000, risk_pct=4))
        with self.assertRaises(Invalid):
            self.store.save_plan(dict(p, stop=200))
        p = self.store.get_plan(p["id"])
        self.assertEqual(p["sizing"], frozen)
        self.assertEqual(p["discipline"]["baseline_risk"], 200)
        self.assertEqual(Store(self.tmp.name).get_plan(p["id"])["sizing"], frozen)

    def test_partial_entry_and_reduced_balance_never_require_top_up(self):
        self.policy()
        p = self.fill(self.ready(), 2)
        self.assertEqual(p["discipline"]["state"], "partial_entry")
        self.assertEqual(p["discipline"]["actual_entry_r"], .1)
        self.assertEqual(p["execution_warnings"], [])
        p = self.fill(p, 18)
        self.assertEqual(p["discipline"]["state"], "holding")
        p = self.fill(p, 19, price=260, action="exit")
        self.assertEqual(p["discipline"]["state"], "reduced")
        self.assertEqual(p["discipline"]["current_r"], .05)
        self.assertEqual(p["discipline"]["actual_entry_r"], 1)
        self.assertEqual(p["execution_warnings"], [])

    def test_actual_oversize_and_fees_are_saved_and_measured_in_frozen_r(self):
        self.policy()
        p = self.fill(self.ready(), 30, fee=2)
        self.assertEqual(p["open_qty"], 30)
        self.assertEqual(p["discipline"]["state"], "over_budget")
        self.assertAlmostEqual(p["discipline"]["actual_entry_r"], 1.51)
        self.assertAlmostEqual(p["discipline"]["execution_deviation_r"], .51)
        self.assertTrue(p["execution_warnings"])
        self.assertIn("request_id", p["fills"][0])
        p = self.fill(p, 29, price=260, action="exit")
        self.assertEqual(p["discipline"]["state"], "over_budget")
        self.assertAlmostEqual(p["discipline"]["execution_deviation_r"], .51)

    def test_estimated_costs_reduce_target_and_real_fees_use_the_reserve(self):
        self.policy()
        raw = plan(estimated_costs=10)
        result = self.store.size(raw)
        self.assertEqual((result["target_qty"], result["cost_reserve"], result["price_risk"]), (19, 10, 190))
        self.assertEqual(result["planned_risk"], 200)
        self.assertEqual(result["utilization"], 1)
        self.assertTrue(result["eligible"])
        self.assertFalse(any("未预留" in text for text in result["warnings"]))
        p = self.fill(self.ready(raw), 19, fee=3)
        self.assertEqual(p["discipline"]["state"], "holding")
        self.assertEqual(p["discipline"]["actual_entry_r"], .965)
        self.assertEqual(p["execution_warnings"], [])

    def test_cost_reserve_is_part_of_utilization_and_never_exceeds_one_r(self):
        self.policy()
        result = self.store.size(plan(estimated_costs=10, planned_qty=20))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["planned_risk"], 210)
        self.assertTrue(self.store.size(plan(estimated_costs=10, planned_qty=17))["eligible"])
        self.assertFalse(self.store.size(plan(estimated_costs=9, planned_qty=17))["eligible"])
        for reserve in (200, 201, 199.99):
            result = self.store.size(plan(estimated_costs=reserve))
            self.assertFalse(result["eligible"])
            self.assertEqual(result["target_qty"], 0)
        self.assertTrue(any("预留已超过" in text for text in self.store.size(plan(estimated_costs=201))["blockers"]))
        for invalid in (-1, True, float("nan"), float("inf")):
            self.assertFalse(self.store.size(plan(estimated_costs=invalid))["eligible"])
            with self.assertRaises(Invalid):
                self.store.save_plan(plan(estimated_costs=invalid))

    def test_correction_recomputes_execution_without_changing_baseline(self):
        self.policy()
        p = self.fill(self.ready(), 30)
        p = self.store.void_fill(p["id"], {"version": p["version"], "fill_id": p["fills"][-1]["id"], "reason": "登记数量错误"})
        self.assertEqual(p["discipline"]["state"], "ready")
        self.assertEqual(p["discipline"]["actual_entry_r"], 0)
        self.assertEqual(p["discipline"]["baseline_risk"], 200)

    def test_execution_through_invalidation_is_unknown_not_zero_risk(self):
        self.policy()
        p = self.fill(self.ready(), 20, price=235)
        self.assertEqual(p["open_qty"], 20)
        self.assertIsNone(p["discipline"]["actual_entry_r"])
        self.assertIsNone(p["discipline"]["current_r"])
        self.assertEqual(p["discipline"]["state"], "unknown_risk")
        self.assertTrue(any("穿越" in text for text in p["execution_warnings"]))

    def test_opening_balance_and_receipt_do_not_need_or_invent_r(self):
        p = self.store.adopt({"id": "snapshot", "sym": "TEST", "kind": "stock", "side": "long",
                              "broker": "IBKR", "fingerprint": "x"},
                             {"qty": 1, "open": 250, "day": et_today().isoformat()}, "snapshot time")
        self.assertEqual(p["discipline"]["state"], "opening_balance")
        self.assertIsNone(p["discipline"]["baseline_risk"])
        receipt = self.store.econf_open({"request_id": "receipt", "symbol": "XYZ", "title": "成交事实",
                 "kind": "stock", "side": "long", "qty": 1000, "price": 250, "day": et_today().isoformat()})
        self.assertEqual(receipt["open_qty"], 1000)
        self.assertEqual(receipt["discipline"]["state"], "unplanned_execution")
        self.assertIsNone(receipt["discipline"]["actual_entry_r"])

    def test_legacy_ready_keeps_old_budget_but_legacy_draft_uses_new_policy(self):
        old = clean_plan(dict(plan(), nlv=10000, risk_pct=1))
        old["sizing"] = size_plan(old)
        old["status"] = "ready"
        with self.store.connection() as c:
            c.execute("INSERT INTO plans(id,body,created_at,updated_at) VALUES(?,?,?,?)",
                      ("old-ready", json.dumps(old), now(), now()))
            other = dict(old, symbol="OTHER", status="draft")
            c.execute("INSERT INTO plans(id,body,created_at,updated_at) VALUES(?,?,?,?)",
                      ("old-draft", json.dumps(other), now(), now()))
        self.policy()
        existing = self.store.get_plan("old-ready")
        self.assertEqual(existing["discipline"]["baseline_risk"], 100)
        self.assertTrue(existing["discipline"]["legacy"])
        newly_ready = self.store.transition("old-draft", {"version": 1, "status": "ready"})
        self.assertEqual(newly_ready["discipline"]["baseline_risk"], 200)
        self.assertFalse(newly_ready["discipline"]["legacy"])

    def test_duplicate_ready_cannot_get_a_new_budget(self):
        self.policy()
        self.ready()
        result = self.store.size(plan())
        self.assertFalse(result["eligible"])
        self.assertTrue(any("共用原始 1R" in text for text in result["blockers"]))
        with self.assertRaises(Invalid):
            self.ready()

    def test_other_account_and_other_contract_warn_without_inventing_strategy_identity(self):
        self.policy()
        self.ready()
        sized = self.store.size(dict(plan(), broker="Schwab"))
        self.assertTrue(sized["eligible"])
        self.assertTrue(any("不能借新计划重置 1R" in text for text in sized["warnings"]))
        self.ready(dict(plan(), symbol="OPT", kind="option", entry=2, contract="OPT 2027-03-19 100 C", expiry="2027-03-19"))
        other = self.store.size(dict(plan(), symbol="OPT", kind="option", entry=2, contract="OPT 2027-03-19 105 C", expiry="2027-03-19"))
        self.assertTrue(other["eligible"])
        self.assertTrue(any("换腿" in text for text in other["warnings"]))


if __name__ == "__main__":
    unittest.main()

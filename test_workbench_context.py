"""Source evidence, annotations and operation attribution; synthetic data only."""
import json
import tempfile
import unittest
from pathlib import Path

from workbench.store import et_today
from workbench.context import enrich_positions, operation_positions, supplemental_history


class ContextTests(unittest.TestCase):
    def test_annotation_overrides_are_local_and_adoption_preserves_identity(self):
        original = {"id": "plan-a", "snapshot_id": "snap-a", "sym": "XYZ", "kind": "option",
                    "label": "Mar19'27 100C", "delta": .4}
        result = enrich_positions([original], {"quotes": {"XYZ": 101}, "day": "2026-09-22"},
                                  {"snap-a": {"delta": .6, "asof": "2026-09-22", "source": "broker"}})[0]
        self.assertEqual(result["delta"], .6)
        self.assertEqual(result["strike"], 100)
        self.assertEqual(result["delta_source"], "broker")
        self.assertEqual(original["delta"], .4)

    def test_same_day_is_not_same_operation(self):
        positions = [{"id": "a", "plan_id": "p", "sym": "XYZ", "qty": 2}]
        plans = [{"id": "p", "fills": [
            {"id": "f1", "request_id": "r1", "action": "entry", "qty": 1, "day": "2026-09-22"},
            {"id": "f2", "request_id": "r2", "action": "entry", "qty": 1, "day": "2026-09-22"}]}]
        rows = operation_positions(positions, plans, [])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("operation_id" not in row for row in rows))

    def test_unknown_greeks_date_and_existing_price_date_are_not_invented(self):
        p = {"id": "a", "sym": "XYZ", "delta": .5, "underlying_price": 90,
             "underlying_price_asof": "2026-08-01"}
        row = enrich_positions([p], {"quotes": {"XYZ": 100}, "day": "2026-09-22"}, {})[0]
        self.assertIsNone(row.get("delta_asof"))
        self.assertEqual(row["underlying_price"], 90)
        self.assertEqual(row["underlying_price_asof"], "2026-08-01")

    def test_position_annotation_cannot_combine_multiple_entry_dates(self):
        p = {"id": "a", "plan_id": "a", "operation_id": "annotated", "executed_at": "2026-09-23T09:00:00-04:00"}
        plan = {"id": "a", "fills": [{"id": "f1", "action": "entry", "qty": 1, "day": "2026-09-21"},
                                       {"id": "f2", "action": "entry", "qty": 1, "day": "2026-09-23"}]}
        rows = operation_positions([p], [plan], [])
        self.assertTrue(all(not r.get("operation_id") and not r.get("executed_at") for r in rows))

    def test_corrected_receipt_date_cannot_reuse_original_batch_clock(self):
        p = {"id": "a", "plan_id": "a"}
        plan = {"id": "a", "fills": [{"id": "f", "request_id": "r", "action": "entry", "qty": 1, "day": "2026-09-23"}]}
        receipt = {"proposal": {"trade_day": "2026-09-21", "executed_clock": "10:30 ET", "legs": [{}]},
                   "recorded": [{"leg": 0, "request_id": "r", "day": "2026-09-23"}]}
        self.assertNotIn("operation_id", operation_positions([p], [plan], [receipt])[0])

    def test_adopted_positions_preserve_currency_and_market_metadata(self):
        from workbench.sources import merged_positions
        source = {"id": "s", "sym": "XYZ", "qty": 1, "currency": "HKD", "underlying_price": 100,
                  "underlying_price_asof": "2026-08-01", "delta_asof": "2026-08-01", "fingerprint": "f"}
        p = {"id": "p", "snapshot_id": "s", "status": "holding", "open_qty": 1, "symbol": "XYZ", "title": "x",
             "kind": "stock", "side": "long", "mult": 1, "avg_entry": 100, "broker": "test", "expiry": ""}
        row = merged_positions({"positions": [source]}, [p])[0][0]
        self.assertEqual(row["currency"], "HKD")
        self.assertEqual(row["underlying_price_asof"], "2026-08-01")

    def test_confirmed_receipt_clock_groups_but_separate_receipts_do_not(self):
        positions, plans, receipts = [], [], []
        for suffix in ("a", "b"):
            records = []
            for i in range(2):
                pid, rid = suffix + str(i), f"econf-{suffix}-hash{suffix}-{i}"
                positions.append({"id": pid, "plan_id": pid, "sym": "XYZ", "qty": 1})
                plans.append({"id": pid, "fills": [{"id": rid, "request_id": rid, "action": "entry", "qty": 1}]})
                records.append({"leg": i, "request_id": rid})
            receipts.append({"proposal": {"account": "test", "trade_day": "2026-09-22", "executed_clock": "11:05 ET",
                                          "legs": [{"description": "XYZ Mar19'27 100 Call"}, {"description": "XYZ Mar19'27 110 Call"}]},
                             "recorded": records})
        rows = operation_positions(positions, plans, receipts)
        self.assertEqual(rows[0]["operation_id"], rows[1]["operation_id"])
        self.assertNotEqual(rows[1]["operation_id"], rows[2]["operation_id"])
        self.assertEqual(rows[0]["broker_account"], "test")
        self.assertEqual(rows[0]["strike"], 100)

    def test_fifo_reduction_does_not_relabel_later_operation(self):
        positions = [{"id": "p", "plan_id": "p", "sym": "XYZ", "qty": 2}]
        plans = [{"id": "p", "fills": [
            {"id": "a", "action": "entry", "qty": 2},
            {"id": "b", "action": "entry", "qty": 3},
            {"id": "c", "action": "exit", "qty": 3},
            {"id": "d", "action": "exit", "qty": 1, "voided": True}]}]
        rows = operation_positions(positions, plans, [])
        self.assertEqual([(r["id"], r["qty"]) for r in rows], [("p:b", 2)])

    def _supplement(self, history, series):
        raw = {"schema": "shepherd-market-data/1", "price_basis": "regular_split_adjusted", "source": "test",
               "series": series}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market-data.json"
            path.write_text(json.dumps(raw))
            return supplemental_history(history, path)

    def test_supplement_without_newer_benchmark_uses_primary_cutoff(self):
        # 只有个股带更晚的报价、基准不比主包新: 仍截在主包日期
        spx = [{"day": "2026-09-21", "close": 5000}, {"day": "2026-09-22", "close": 5010}]
        history = {"day": "2026-09-22", "series": {"SPX": spx}, "errors": []}
        result = self._supplement(history, {"AAA": [{"day": "2026-09-22", "close": 10},
                                                     {"day": "2026-09-23", "close": 999}]})
        self.assertEqual(result["day"], "2026-09-22")
        self.assertEqual(result["series"]["AAA"][-1]["close"], 10)
        self.assertNotIn("day_source", result)

    def test_newer_supplement_benchmark_advances_cutoff_without_mixing(self):
        history = {"day": "2026-09-22", "errors": [],
                   "series": {"SPX": [{"day": "2026-09-22", "close": 5010}],
                              "OLD": [{"day": "2026-09-22", "close": 1}]}}
        result = self._supplement(history, {
            "SPX": [{"day": "2026-09-22", "close": 5010}, {"day": "2026-09-23", "close": 5020}],
            "AAA": [{"day": "2026-09-22", "close": 10}, {"day": "2026-09-23", "close": 11}],
            "STALE": [{"day": "2026-09-22", "close": 3}]})
        self.assertEqual(result["day"], "2026-09-23")
        self.assertEqual(result["day_source"], "supplement")
        self.assertEqual(result["primary_day"], "2026-09-22")
        self.assertEqual(result["series"]["SPX"][-1]["close"], 5020)
        self.assertEqual(result["series"]["AAA"][-1]["close"], 11)
        self.assertNotIn("OLD", result["series"])      # 主包旧序列不与更晚的截止日混用
        self.assertNotIn("STALE", result["series"])    # 未到新截止日的补充序列不采用
        self.assertEqual(history["day"], "2026-09-22")  # 不改动传入对象

    def test_spy_proxy_can_advance_cutoff(self):
        result = self._supplement({"day": "2026-09-22", "series": {}, "errors": []},
                                  {"SPY": [{"day": "2026-09-23", "close": 600}]})
        self.assertEqual(result["day"], "2026-09-23")
        self.assertIn("SPY", result["series"])

    def test_missing_primary_day_uses_supplement_benchmark_day(self):
        result = self._supplement({"day": None, "series": {}, "errors": ["没有可用的日线行情包"]},
                                  {"SPX": [{"day": "2026-09-23", "close": 5020}]})
        self.assertEqual(result["day"], "2026-09-23")
        self.assertEqual(result["errors"], ["没有可用的日线行情包"])

    def test_invalid_supplement_is_not_partially_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market-data.json"
            path.write_text(json.dumps({"schema": "shepherd-market-data/1", "price_basis": "regular_split_adjusted",
                                       "source": "test", "series": {"SPX": [{"day": "2026-09-22", "close": -1}]}}))
            result = supplemental_history({"day": "2026-09-22", "series": {}, "errors": []}, path)
            self.assertEqual(result["series"], {})
            self.assertIn("无效", result["errors"][0])

    def test_risk_annotation_api_validates_and_never_changes_positions(self):
        from workbench.server import Application
        from workbench.store import Invalid
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = Application(root / "ledger", root / "sources", root / "inbox")
            snapshot = {"positions": [{"id": "p", "kind": "stock", "sym": "XYZ", "qty": 1}],
                        "quotes": {}, "day": et_today().isoformat()}
            app.sources.snapshot = lambda: snapshot
            data = {"position_id": "p", "asof": et_today().isoformat(), "source": "Synthetic broker fixture",
                    "delta": .5, "underlying_price": 100, "sector": "test"}
            app.post("/api/exposure-input", data)
            self.assertEqual(app.store.get("risk:inputs")["p"]["delta"], .5)
            self.assertNotIn("delta", snapshot["positions"][0])
            with self.assertRaises(Invalid):
                app.post("/api/exposure-input", dict(data, delta=2))
            with self.assertRaises(Invalid):
                app.post("/api/exposure-input", dict(data, asof="2999-01-01"))
            self.assertEqual(app.store.get("risk:inputs")["p"]["delta"], .5)


if __name__ == "__main__":
    unittest.main()

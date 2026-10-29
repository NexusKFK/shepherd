import copy
import unittest

from workbench.position_display import display_positions, partition_positions


def leg(id, side, qty, open_, mark, group="XLK 250/260 看涨价差（快速反弹）", **kw):
    row = {"id": id, "broker": "IBKR", "sym": "XLK", "kind": "option", "side": side, "qty": qty, "mult": 100,
           "open": open_, "mark": mark, "expiry": "2027-02-19", "group": group, "label": id}
    row.update(kw)
    return row


class SpreadSummaryTests(unittest.TestCase):
    def test_named_group_of_opposite_legs_gets_a_display_only_summary(self):
        legs = [leg("buy", "long", 1, 4.2, 1.9), leg("sell", "short", 1, 1.6, 0.5)]
        rows = display_positions(copy.deepcopy(legs))
        summary = next(r for r in rows if r["id"].startswith("spread:"))
        self.assertTrue(summary["display_only"])
        self.assertEqual(summary["component_ids"], ["buy", "sell"])
        self.assertEqual((summary["side"], summary["qty"], summary["open"], summary["mark"]), ("long", 1, 2.6, 1.4))
        self.assertEqual(summary["label"], "XLK 250/260 看涨价差（快速反弹）")
        active, summaries = partition_positions(rows)
        self.assertEqual([r["id"] for r in active], ["buy", "sell"])     # risk still measured on the legs
        self.assertEqual(len(summaries), 1)

    def test_butterfly_quantity_is_the_common_unit(self):
        legs = [leg("a", "long", 3, 4.1, 1.9, group="fly"), leg("b", "short", 6, 1.5, 0.6, group="fly"),
                leg("c", "long", 3, 0.7, 0.25, group="fly")]
        summary = [r for r in display_positions(legs) if r["id"].startswith("spread:")][0]
        self.assertEqual(summary["qty"], 3)
        self.assertAlmostEqual(summary["open"], 4.1 - 2 * 1.5 + 0.7, places=4)

    def test_credit_spread_is_a_short_summary(self):
        legs = [leg("a", "short", 1, 5.0, 3.0, group="credit"), leg("b", "long", 1, 2.0, 1.0, group="credit")]
        summary = [r for r in display_positions(legs) if r["id"].startswith("spread:")][0]
        self.assertEqual((summary["side"], summary["open"], summary["mark"]), ("short", 3.0, 2.0))

    def test_no_summary_for_same_side_legs_single_leg_other_expiry_or_other_account(self):
        same_side = [leg("a", "long", 1, 1, 1, group="g"), leg("b", "long", 1, 1, 1, group="g")]
        single = [leg("a", "long", 1, 1, 1, group="g")]
        other_expiry = [leg("a", "long", 1, 1, 1, group="g"), leg("b", "short", 1, 1, 1, group="g", expiry="2026-11-20")]
        other_broker = [leg("a", "long", 1, 1, 1, group="g"), leg("b", "short", 1, 1, 1, group="g", broker="ToS")]
        no_group = [leg("a", "long", 1, 1, 1, group=""), leg("b", "short", 1, 1, 1, group="")]
        for rows in (same_side, single, other_expiry, other_broker, no_group):
            self.assertEqual(len(display_positions(rows)), len(rows))

    def test_stock_rows_in_a_group_are_not_summarised(self):
        rows = [leg("put", "short", 2, 1.05, 0.55, group="防御篮子 · KO"),
                {"id": "stk", "broker": "IBKR", "sym": "XLK", "kind": "stock", "side": "long", "qty": 10, "mult": 1,
                 "open": 1, "mark": 1, "group": "防御篮子 · KO"}]
        self.assertEqual(len(display_positions(rows)), 2)

    def test_existing_explicit_summary_is_kept_and_not_duplicated(self):
        legs = [leg("a", "long", 3, 4.1, 1.9, group="fly"), leg("b", "short", 6, 1.5, 0.6, group="fly"),
                leg("c", "long", 3, 0.7, 0.25, group="fly")]
        summary = {"id": "s", "broker": "IBKR", "sym": "XLK", "kind": "option", "expiry": "2027-02-19", "qty": 3, "side": "long",
                   "delta_status": "combo", "component_ids": ["a", "b", "c"], "group": ""}
        rows = display_positions(legs + [summary])
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(1 for r in rows if r.get("display_only")), 1)

    def test_missing_mark_keeps_the_summary_without_a_mark(self):
        legs = [leg("a", "long", 1, 2.0, None), leg("b", "short", 1, 1.0, 0.5)]
        summary = [r for r in display_positions(legs) if r["id"].startswith("spread:")][0]
        self.assertIsNone(summary["mark"])

    def test_input_rows_are_not_mutated(self):
        legs = [leg("a", "long", 1, 2.0, 1.0), leg("b", "short", 1, 1.0, 0.5)]
        before = copy.deepcopy(legs)
        display_positions(legs)
        self.assertEqual(legs, before)


if __name__ == "__main__":
    unittest.main()

"""Payoff geometry must remain separate from actual execution evidence."""
import copy
import unittest

from workbench.strategies import recognize_operations


def option(id="a", **updates):
    return dict({"id": id, "kind": "option", "sym": "TEST", "broker": "Broker", "account_id": "acct-a",
                 "side": "long", "qty": 1, "mult": 100, "right": "C", "strike": 100,
                 "expiry": "2027-02-19", "operation_id": "operation-one"}, **updates)


def shape(positions):
    return recognize_operations(positions)["current_structures"][0]["strategy"]["code"]


class StrategyTests(unittest.TestCase):
    def test_vertical_calendar_diagonal(self):
        a = option()
        self.assertEqual(shape([a, option("b", side="short", strike=110)]), "vertical")
        self.assertEqual(shape([a, option("b", side="short", expiry="2027-03-19")]), "calendar")
        self.assertEqual(shape([a, option("b", side="short", expiry="2027-03-19", strike=110)]), "diagonal")

    def test_straddle_and_strangle(self):
        self.assertEqual(shape([option(), option("b", right="P")]), "straddle")
        self.assertEqual(shape([option(), option("b", right="P", strike=90)]), "strangle")

    def test_butterfly_requires_true_121_quantities_and_spacing(self):
        legs = [option(strike=90), option("b", side="short", qty=2), option("c", strike=110)]
        self.assertEqual(shape(legs), "butterfly")
        legs[2]["strike"] = 120
        self.assertEqual(shape(legs), "broken_wing_butterfly")
        legs[1]["qty"] = 1
        self.assertEqual(shape(legs), "unknown")

    def test_iron_condor_and_iron_butterfly(self):
        legs = [option("a", right="P", strike=80), option("b", right="P", strike=90, side="short"),
                option("c", strike=110, side="short"), option("d", strike=120)]
        self.assertEqual(shape(legs), "iron_condor")
        legs[2]["strike"] = 90
        self.assertEqual(shape(legs), "iron_butterfly")

    def test_ratio_is_not_certified_as_zebra(self):
        r = recognize_operations([option(qty=2), option("b", side="short", strike=110)])
        s = r["groups"][0]["strategy"]
        self.assertEqual(s["code"], "ratio_2_1")
        self.assertTrue(any("不能认证 ZEBRA" in n for n in s["notes"]))

    def test_same_day_and_legacy_label_never_prove_simultaneous_execution(self):
        legs = [option(operation_id=None, executed_at="2026-09-23", group="垂直价差"),
                option("b", side="short", strike=110, operation_id=None, executed_at="2026-09-23", group="垂直价差")]
        r = recognize_operations(legs)
        self.assertEqual(r["groups"], [])
        self.assertEqual(len(r["ungrouped"]), 2)
        self.assertEqual(r["current_structures"][0]["strategy"]["code"], "vertical")
        self.assertEqual(r["current_structures"][0]["operation_count"], 0)

    def test_different_operations_retained_but_current_shape_recognized(self):
        r = recognize_operations([option(), option("b", side="short", strike=110, operation_id="two")])
        self.assertEqual(len(r["groups"]), 2)
        self.assertTrue(all(g["strategy"]["code"] == "single" for g in r["groups"]))
        self.assertEqual(r["current_structures"][0]["strategy"]["code"], "vertical")
        self.assertEqual(r["current_structures"][0]["operation_count"], 2)

    def test_exact_timestamp_normalizes_timezone_but_different_seconds_separate(self):
        a = option(operation_id=None, executed_at="2026-09-22T10:00:01-04:00")
        b = option("b", side="short", strike=110, operation_id=None, executed_at="2026-09-22T14:00:01Z")
        self.assertEqual(len(recognize_operations([a, b])["groups"]), 1)
        b["executed_at"] = "2026-09-22T14:00:02Z"
        self.assertEqual(len(recognize_operations([a, b])["groups"]), 2)

    def test_minute_level_clock_and_no_timezone_are_not_exact(self):
        for when in ("2026-09-22T10:00-04:00", "2026-09-22T10:00:00"):
            r = recognize_operations([option(operation_id=None, executed_at=when)])
            self.assertFalse(r["groups"])

    def test_accounts_brokers_and_symbols_do_not_merge(self):
        for change in ({"account_id": "acct-b"}, {"broker": "Other"}, {"sym": "OTHER"}):
            r = recognize_operations([option(), option("b", side="short", strike=110, **change)])
            self.assertEqual(len(r["groups"]), 2)
        r = recognize_operations([option(account_id=None), option("b", account_id=None)])
        self.assertFalse(r["groups"])
        self.assertTrue(any("账户" in n for n in r["current_structures"][0]["notes"]))

    def test_broker_account_alias_and_call_put_alias(self):
        a = option(account_id=None, broker_account="account", right=None, call_put="CALL")
        b = option("b", account_id=None, broker_account="account", side="short", strike=110)
        self.assertEqual(recognize_operations([a, b])["groups"][0]["strategy"]["code"], "vertical")

    def test_legacy_butterfly_shape_preserved_without_batch_evidence(self):
        legs = [option(operation_id=None, account_id=None, strike=None, right=None, label="Mar19'27 410C", group="蝶"),
                option("b", operation_id=None, account_id=None, strike=None, right=None, label="Mar19'27 460C", side="short", qty=2, group="蝶"),
                option("c", operation_id=None, account_id=None, strike=None, right=None, label="Mar19'27 510C", group="蝶")]
        r = recognize_operations(legs)
        self.assertFalse(r["groups"])
        self.assertEqual(r["current_structures"][0]["strategy"]["code"], "butterfly")
        self.assertEqual(shape([option(strike=None, right=None, label="410/460/510 蝶")]), "unknown")

    def test_legacy_groups_keep_same_underlying_structures_separate(self):
        legs = [option(group="vertical"), option("b", group="vertical", strike=110, side="short"),
                option("c", group="calendar"), option("d", group="calendar", side="short", expiry="2027-03-19")]
        r = recognize_operations(legs)
        self.assertEqual({s["strategy"]["code"] for s in r["current_structures"]}, {"vertical", "calendar"})

    def test_different_multipliers_and_missing_expiry_not_certified(self):
        self.assertEqual(shape([option(), option("b", side="short", strike=110, mult=10)]), "unknown")
        self.assertEqual(shape([option(expiry=None)]), "unknown")

    def test_recognition_does_not_change_positions(self):
        legs = [option(), option("b", side="short", strike=110)]
        original = copy.deepcopy(legs)
        recognize_operations(legs)
        self.assertEqual(legs, original)


if __name__ == "__main__":
    unittest.main()

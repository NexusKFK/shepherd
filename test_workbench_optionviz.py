import json
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from workbench import optionviz as ov

TODAY = date(2026, 10, 15)


def spread_card(**over):
    card = {"id": "spread-test", "name": "TSM 10/30 460/480 看涨价差", "underlying": "TSM", "spot": 452.4, "asof": "2026-10-15",
            "iv": 0.36, "entry_price": 7.00, "contracts": 1, "horizons": [3, 5],
            "legs": [{"side": "buy", "right": "C", "strike": 460, "expiry": "2026-10-30", "qty": 1, "bid": 13.10, "ask": 13.60},
                     {"side": "sell", "right": "C", "strike": 480, "expiry": "2026-10-30", "qty": 1, "bid": 6.00, "ask": 6.40}],
            "levels": [{"price": 437.7, "label": "作废线", "kind": "invalid"}, {"price": 480, "label": "目标", "kind": "target"}]}
    card.update(over)
    return card


class PricingTests(unittest.TestCase):
    def test_put_call_parity(self):
        S, K, T, iv, r = 100.0, 105.0, 0.5, 0.3, ov.RATE
        diff = ov.bs_price("C", S, K, T, iv, r) - ov.bs_price("P", S, K, T, iv, r)
        self.assertAlmostEqual(diff, S - K * math.exp(-r * T), places=8)

    def test_implied_vol_round_trip(self):
        price = ov.bs_price("C", 130.8, 135, 15 / 365, 0.645)
        self.assertAlmostEqual(ov.implied_vol("C", 130.8, 135, 15 / 365, price), 0.645, places=4)

    def test_implied_vol_refuses_impossible_prices(self):
        self.assertIsNone(ov.implied_vol("C", 100, 100, 0.1, 0.0))
        self.assertIsNone(ov.implied_vol("C", 100, 100, 0.1, 150.0))
        self.assertIsNone(ov.implied_vol("C", 100, 100, 0.0, 5.0))

    def test_greeks_signs(self):
        c = ov.bs_greeks("C", 100, 100, 0.1, 0.3)
        p = ov.bs_greeks("P", 100, 100, 0.1, 0.3)
        self.assertTrue(0 < c["delta"] < 1 and -1 < p["delta"] < 0)
        self.assertLess(c["theta"], 0)
        self.assertGreater(c["vega"], 0)


class CardTests(unittest.TestCase):
    def test_bull_call_spread_numbers(self):
        a = ov.analyze_card(spread_card(), TODAY, one_r=2500.0)
        self.assertEqual(a["status"], "ok")
        self.assertAlmostEqual(a["net_debit_usd"], 700.0)
        self.assertAlmostEqual(a["max_loss"], -700.0)
        self.assertAlmostEqual(a["max_profit"], 1300.0)
        self.assertEqual(len(a["breakevens"]), 1)
        self.assertAlmostEqual(a["breakevens"][0], 467.00, delta=0.5)
        self.assertAlmostEqual(a["max_loss_r"], 0.28, places=2)

    def test_calc_block_lets_the_ui_reprice_without_python(self):
        a = ov.analyze_card(spread_card(), TODAY)
        c = a["calc"]
        self.assertEqual(c["first_dte"], 15)
        self.assertEqual(c["default_days"], 7)
        self.assertEqual([(l["strike"], l["qty"], l["dte"]) for l in c["legs"]], [(460.0, 1, 15), (480.0, -1, 15)])
        # repricing from the calc block alone reproduces the Python curve value at the hold date
        S = 480.0
        value = sum(l["qty"] * l["mult"] * ov.bs_price(l["right"], S, l["strike"], (l["dte"] - c["default_days"]) / 365.0, l["iv"]) for l in c["legs"])
        row = next(r for r in a["grid"] if r["spot"] == 480.0)
        self.assertAlmostEqual(value - c["cost"], row["t5"], delta=1.0)

    def test_friction_and_natural_price_from_bid_ask(self):
        a = ov.analyze_card(spread_card(), TODAY, one_r=2500.0)
        self.assertAlmostEqual(a["natural_price"], 13.60 - 6.00)
        self.assertAlmostEqual(a["friction_usd"], 90.0)   # (0.50 + 0.40) x 100

    def test_curves_expiry_matches_intrinsic_and_hold_curves_exist(self):
        a = ov.analyze_card(spread_card(), TODAY)
        keys = [c["key"] for c in a["curves"]]
        self.assertEqual(keys, ["expiry", "t3", "t5", "t5iv"])
        at_expiry = dict((round(x, 1), y) for x, y in a["curves"][0]["points"])
        far = max(at_expiry)
        self.assertAlmostEqual(at_expiry[far], 1300.0, places=0)
        low = min(at_expiry)
        self.assertAlmostEqual(at_expiry[low], -700.0, places=0)

    def test_single_call_is_open_ended_upside(self):
        card = spread_card(legs=[spread_card()["legs"][0]], entry_price=14.0)
        a = ov.analyze_card(card, TODAY)
        self.assertIsNone(a["max_profit"])
        self.assertAlmostEqual(a["max_loss"], -1400.0)

    def test_short_put_has_finite_worst_case_and_credit_breakeven(self):
        card = {"id": "short-put", "spot": 48.0, "iv": 0.28, "entry_price": -1.05, "contracts": 2,
                "legs": [{"side": "sell", "right": "P", "strike": 45, "expiry": "2026-12-18", "qty": 1, "price": 0.40}]}
        a = ov.analyze_card(card, TODAY)
        self.assertEqual(a["status"], "ok")
        self.assertAlmostEqual(a["max_profit"], 210.0)
        self.assertAlmostEqual(a["max_loss"], -(45 - 1.05) * 200, places=0)
        self.assertAlmostEqual(a["breakevens"][0], 43.95, delta=0.2)

    def test_bad_cards_are_reported_not_drawn(self):
        self.assertEqual(ov.analyze_card(spread_card(spot="abc"), TODAY)["status"], "error")
        self.assertEqual(ov.analyze_card(spread_card(legs=[]), TODAY)["status"], "error")
        expired = spread_card(legs=[dict(spread_card()["legs"][0], expiry="2026-10-14")])
        self.assertIn("过去", ov.analyze_card(expired, TODAY)["error"])
        nov = {k: v for k, v in spread_card().items() if k not in ("entry_price", "iv")}
        nov["legs"] = [{"side": "buy", "right": "C", "strike": 460, "expiry": "2026-10-30", "qty": 1}]
        self.assertEqual(ov.analyze_card(nov, TODAY)["status"], "error")

    def test_stale_card_is_flagged(self):
        a = ov.analyze_card(spread_card(asof="2026-10-09"), TODAY)
        self.assertTrue(a["stale"])

    def test_without_one_r_there_is_no_r_and_a_note(self):
        a = ov.analyze_card(spread_card(), TODAY, one_r=None)
        self.assertIsNone(a["max_loss_r"])
        self.assertIsNotNone(a["r_note"])


class BuildTests(unittest.TestCase):
    def test_missing_directory_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            r = ov.build([], TODAY, directory=Path(d) / "nope")
        self.assertEqual(r["status"], "missing")
        self.assertTrue(r["notices"])

    def test_unreadable_card_shows_as_error_row(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "broken.json").write_text("{not json")
            (Path(d) / "ok.json").write_text(json.dumps(spread_card()))
            r = ov.build([], TODAY, directory=d)
        by_id = {c["id"]: c for c in r["cards"]}
        self.assertEqual(by_id["broken"]["status"], "error")
        self.assertEqual(by_id["spread-test"]["status"], "ok")
        self.assertEqual(r["cards"][0]["status"], "ok")   # good cards first


def held(**over):
    row = {"id": "short-put", "broker": "IBKR", "sym": "T", "label": "Dec18'26 45P 卖出 ×2", "kind": "option", "qty": -2, "mult": 100,
           "open": 1.05, "mark": 0.40, "expiry": "2026-12-18", "underlying_price": 48.0}
    row.update(over)
    return row


class HeldTests(unittest.TestCase):
    def test_short_put_group(self):
        out = ov.held_groups([held()], TODAY, one_r=2500.0)
        self.assertEqual(len(out), 1)
        g = out[0]
        self.assertEqual(g["status"], "ok")
        self.assertAlmostEqual(g["net_debit_usd"], -210.0)
        self.assertAlmostEqual(g["unrealized"], 130.0)
        self.assertAlmostEqual(g["max_profit"], 210.0)
        self.assertTrue(g["max_loss"] < -8000)

    def test_legs_of_one_expiry_are_one_chart_and_summary_rows_are_skipped(self):
        legs = [held(id="a", sym="IAU", label="Mar19'27 410C", qty=2, open=4.10, mark=2.00, expiry="2027-03-19", underlying_price=380.0),
                held(id="b", sym="IAU", label="Mar19'27 460C", qty=-4, open=1.60, mark=0.70, expiry="2027-03-19", underlying_price=380.0),
                held(id="c", sym="IAU", label="Mar19'27 510C", qty=2, open=0.55, mark=0.30, expiry="2027-03-19", underlying_price=380.0),
                held(id="s", sym="IAU", label="410/460/510 蝶", qty=2, display_only=True, delta_status="combo", expiry="2027-03-19")]
        out = ov.held_groups(legs, TODAY)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["legs"]), 3)

    def test_distant_breakeven_is_found_even_outside_the_drawn_range(self):
        # a 420 call bought at 30.00: breakeven 450.00, which is more than 10% above a 372.5 spot
        row = held(sym="IAU", label="Mar19'27 420C", qty=1, side="long", open=30.00, mark=6.2, expiry="2027-03-19", underlying_price=372.5)
        g = ov.held_groups([row], TODAY)[0]
        self.assertEqual(len(g["breakevens"]), 1)
        self.assertAlmostEqual(g["breakevens"][0], 450.00, delta=0.6)

    def test_unsigned_quantity_with_short_side_is_a_short_leg(self):
        # the app state reports qty 2 and side "short" for the T puts; the ledger stores -2
        out = ov.held_groups([held(qty=2, side="short")], TODAY)
        self.assertAlmostEqual(out[0]["net_debit_usd"], -210.0)
        self.assertAlmostEqual(out[0]["max_profit"], 210.0)
        self.assertEqual(out[0]["legs"][0]["side"], "sell")

    def test_missing_underlying_price_is_an_error_row(self):
        out = ov.held_groups([held(underlying_price=None)], TODAY)
        self.assertEqual(out[0]["status"], "error")
        self.assertIn("标的价格", out[0]["error"])

    def test_held_legs_carry_open_price_mark_and_leg_pnl(self):
        g = ov.held_groups([held()], TODAY)[0]
        leg = g["legs"][0]
        self.assertEqual((leg["open"], leg["mark"]), (1.05, 0.40))
        self.assertEqual(leg["pnl_usd"], 130.0)   # short 2 puts: -2 x 100 x (0.40 - 1.05)

    def test_label_parser(self):
        self.assertEqual(ov._parse_label("Mar19'27 420C"), (date(2027, 3, 19), 420.0, "C"))
        self.assertEqual(ov._parse_label("Dec18'26 45P 卖出 ×2"), (date(2026, 12, 18), 45.0, "P"))
        self.assertIsNone(ov._parse_label("410/460/510 蝶"))


if __name__ == "__main__":
    unittest.main()


def test_held_groups_spot_falls_back_to_quotes():
    """腿的 underlying_price 缺失（冻结希腊字母路径）时，用快照顶层 quotes 兜底，不再整卡报错。"""
    from datetime import date
    from workbench import optionviz
    legs = [
        {"kind": "option", "broker": "ToS", "sym": "XLE", "label": "Nov20'26 95C", "qty": 1, "side": "long",
         "mult": 100, "open": 3.6, "mark": 2.4, "expiry": "2026-11-20", "strike": 95.0, "right": "C",
         "delta_status": "ok"},  # 没有 underlying_price
        {"kind": "option", "broker": "ToS", "sym": "XLE", "label": "Nov20'26 100C", "qty": -1, "side": "short",
         "mult": 100, "open": 1.7, "mark": 1.0, "expiry": "2026-11-20", "strike": 100.0, "right": "C",
         "delta_status": "ok"},
    ]
    # 没有 quotes → 报错；有 quotes → 正常出图
    out = optionviz.held_groups(legs, date(2026, 10, 28))
    assert out[0]["status"] == "error" and "标的价格" in out[0]["error"]
    out = optionviz.held_groups(legs, date(2026, 10, 28), quotes={"XLE": {"close": 93.0}})
    assert out[0]["status"] == "ok" and abs(out[0]["spot"] - 93.0) < 1e-9
    assert [(l["side"], l["qty"], l["strike"]) for l in out[0]["legs"]] == [("buy", 1.0, 95.0), ("sell", 1.0, 100.0)]


def test_held_groups_skips_synthetic_plan_rows():
    """plan-* 合成行（未关联快照的计划聚合占位）不是腿：画图只用快照真腿，合成行不该把整组拖成「无法识别」。"""
    from datetime import date
    from workbench import optionviz
    positions = [
        {"kind": "option", "broker": "ToS", "sym": "ADBE", "label": "Mar19'27 400C", "qty": 2, "side": "long",
         "mult": 100, "open": 30.4, "mark": 29.8, "expiry": "2027-03-19", "strike": 400.0, "right": "C",
         "underlying_price": 436.0, "delta_status": "ok"},
        {"kind": "option", "broker": "ToS", "sym": "ADBE", "label": "Mar19'27 430C", "qty": 1, "side": "short",
         "mult": 100, "open": 16.2, "mark": 15.7, "expiry": "2027-03-19", "strike": 430.0, "right": "C",
         "underlying_price": 436.0, "delta_status": "ok"},
        # 合成行：计划标题当标签，无行权价无方向
        {"id": "plan-x", "plan_id": "x", "kind": "option", "broker": "ToS", "sym": "ADBE",
         "label": "3/19 ZEBRA · 买2×400C 卖1×430C（已开仓）", "qty": 1, "side": "long",
         "expiry": "2027-03-19", "delta_status": None},
    ]
    out = optionviz.held_groups(positions, date(2026, 10, 28))
    assert len(out) == 1 and out[0]["status"] == "ok"
    assert [(l["side"], l["qty"], l["strike"]) for l in out[0]["legs"]] == [("buy", 2.0, 400.0), ("sell", 1.0, 430.0)]

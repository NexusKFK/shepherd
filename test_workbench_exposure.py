"""Exposure accounting and regression tests use synthetic, deterministic prices."""
import copy
import math
import unittest
from datetime import datetime, timedelta

from workbench.exposure import (FACTOR_BUCKETS, FACTOR_CLUSTERS, R_UNAVAILABLE, analyze_exposure, estimate_beta,
                                factor_clusters, market_scenario, portfolio_heat, r_budget, standard_r)
from workbench.sources import ET
from workbench.position_display import partition_positions


TODAY = datetime.now(ET).date()


def prices(scale=1, n=280, offset=0, initial=100):
    value = initial
    result = []
    for i in range(n):
        # Nonconstant daily returns; beta of scale=2 relative to scale=1 is 2.
        value *= 1 + scale * (.004 * math.sin(i * .73) + .001)
        result.append({"day": (TODAY - timedelta(days=n - 1 - i + offset)).isoformat(), "close": value})
    return result


def history(benchmark="SPX", offset=0):
    return {"day": (TODAY - timedelta(days=offset)).isoformat(),
            "series": {"TEST": prices(2, offset=offset), benchmark: prices(1, offset=offset, initial=5000)}, "errors": []}


def position(**updates):
    return dict({"id": "one", "kind": "stock", "sym": "TEST", "broker": "test", "qty": 10, "side": "long"}, **updates)


def option(**updates):
    return position(**dict({"kind": "option", "qty": 2, "mult": 100, "delta": .6,
                            "right": "C", "delta_asof": TODAY.isoformat(), "delta_source": "test model"}, **updates))


class ExposureTests(unittest.TestCase):
    def test_signed_short_put_delta_applies_direction_once(self):
        result = analyze_exposure([option(side="short", delta=-.3, right="P")], history(), 10000)
        row = result["rows"][0]
        self.assertEqual(row["position_delta"], 60)
        self.assertAlmostEqual(row["dollar_delta"], 60 * history()["series"]["TEST"][-1]["close"], places=5)
        self.assertEqual(result["coverage"]["delta_covered"], 1)

    def test_offsetting_long_short_retains_gross_without_summing_share_delta(self):
        h = history()
        h["series"]["OTHER"] = prices(1, initial=250)
        r = analyze_exposure([position(qty=10), position(id="hedge", qty=10, side="short")], h, 10000)
        self.assertEqual(r["summary"]["net_dollar_delta"], 0)
        self.assertGreater(r["summary"]["gross_dollar_delta"], 0)
        self.assertEqual(r["rows"][0]["gross_share_pct"], 50)
        self.assertNotIn("position_delta", r["summary"])
        r = analyze_exposure([position(qty=1), position(id="other", qty=1, sym="OTHER")], h, 10000)
        expected = h["series"]["TEST"][-1]["close"] + h["series"]["OTHER"][-1]["close"]
        self.assertAlmostEqual(r["summary"]["net_dollar_delta"], expected, places=5)

    def test_spx_weighted_units_and_one_percent_scenario(self):
        r = analyze_exposure([position()], history(), 10000)
        s = r["summary"]
        self.assertAlmostEqual(s["beta_net_dollar_delta"], s["net_dollar_delta"] * 2, places=4)
        self.assertAlmostEqual(s["spx_delta"], s["beta_net_dollar_delta"] / r["benchmark"]["price"], places=5)
        self.assertAlmostEqual(s["benchmark_1pct_pnl"], s["beta_net_dollar_delta"] * .01, places=5)
        self.assertEqual(s["benchmark_down1pct_pnl"], -s["benchmark_1pct_pnl"])
        self.assertEqual(s["scope"], "portfolio")

    def test_spy_proxy_never_becomes_spx_point_delta(self):
        r = analyze_exposure([position()], history("SPY"), 10000)
        self.assertTrue(r["benchmark"]["proxy"])
        self.assertIsNone(r["summary"]["spx_delta"])
        self.assertIsNotNone(r["summary"]["benchmark_delta"])
        self.assertTrue(any("固定十倍" in s for s in r["notices"]))

    def test_real_spx_preferred_over_spy(self):
        h = history("SPY")
        h["series"]["SPX"] = prices(1, initial=5000)
        r = analyze_exposure([position()], h, 10000)
        self.assertEqual(r["benchmark"]["symbol"], "SPX")
        self.assertFalse(r["benchmark"]["proxy"])

    def test_newer_daily_close_replaces_older_snapshot_underlying_price(self):
        p = position(underlying_price=50, underlying_price_asof=(TODAY - timedelta(days=1)).isoformat())
        h = history()
        r = analyze_exposure([p], h, 10000)
        row = r["rows"][0]
        self.assertEqual(row["price_asof"], TODAY.isoformat())
        self.assertEqual(row["price_source"], "日线收盘")
        self.assertEqual(row["underlying_price"], round(h["series"]["TEST"][-1]["close"], 6))
        self.assertTrue(r["coverage"]["beta_complete"])

    def test_price_date_mismatch_preserves_dated_dollar_delta_not_beta_total(self):
        for underlying_newer in (True, False):
            with self.subTest(underlying_newer=underlying_newer):
                h = history()
                key = "SPX" if underlying_newer else "TEST"
                h["series"][key] = prices(1 if key == "SPX" else 2, offset=1)
                r = analyze_exposure([position()], h, 10000)
                row = r["rows"][0]
                self.assertIsNotNone(row["dollar_delta"])
                self.assertIsNone(row["beta_dollar_delta"])
                self.assertIsNone(r["summary"]["spx_delta"])
                self.assertIsNone(r["summary"]["benchmark_1pct_pnl"])
                self.assertTrue(r["coverage"]["delta_complete"])
                self.assertFalse(r["coverage"]["beta_complete"])
                self.assertTrue(any("标的价格与基准价格不在同一天" in s for s in row["issues"]))

    def test_intraday_snapshot_spx_aligns_benchmark_day(self):
        # 开盘校准：持仓是今天盘中价，日线（含 SPX）停在昨天；快照里同一时刻的 SPX 点位补上基准
        h = history(offset=1)
        p = position(underlying_price=150, underlying_price_asof=TODAY.isoformat())
        quote = {"symbol": "SPX", "price": 5100.0, "day": TODAY.isoformat(), "asof": "t", "source": "test"}
        without = analyze_exposure([p], h, 10000)
        self.assertIsNone(without["rows"][0]["beta_dollar_delta"])
        r = analyze_exposure([p], h, 10000, benchmark_quote=quote)
        row = r["rows"][0]
        self.assertEqual(row["issues"], [])
        self.assertIsNotNone(row["beta_dollar_delta"])
        self.assertEqual(r["benchmark"]["price"], 5100.0)
        self.assertEqual(r["benchmark"]["day"], TODAY.isoformat())
        self.assertTrue(r["benchmark"]["intraday"])
        self.assertAlmostEqual(r["summary"]["spx_delta"], row["beta_dollar_delta"] / 5100.0, places=4)
        self.assertEqual(row["beta"], without["rows"][0]["beta"])          # Beta 不受盘中点位影响
        self.assertTrue(any("盘中" in n for n in r["notices"]))

    def test_snapshot_spx_ignored_unless_newer_and_real_spx(self):
        h = history()
        close = h["series"]["SPX"][-1]["close"]
        cases = {"same day as daily close": {"symbol": "SPX", "price": 1.0, "day": TODAY.isoformat()},
                 "older": {"symbol": "SPX", "price": 1.0, "day": (TODAY - timedelta(days=1)).isoformat()},
                 "not SPX": {"symbol": "SPY", "price": 1.0, "day": (TODAY + timedelta(days=0)).isoformat()},
                 "bad price": {"symbol": "SPX", "price": 0, "day": TODAY.isoformat()}}
        for name, quote in cases.items():
            with self.subTest(name):
                r = analyze_exposure([position()], h, 10000, benchmark_quote=quote)
                self.assertAlmostEqual(r["benchmark"]["price"], close, places=4)
                self.assertNotIn("intraday", r["benchmark"])
        spy = history(benchmark="SPY", offset=1)                            # SPY 代理时不拿 SPX 点位混用
        r = analyze_exposure([position()], spy, 10000,
                             benchmark_quote={"symbol": "SPX", "price": 5100.0, "day": TODAY.isoformat()})
        self.assertTrue(r["benchmark"]["proxy"])
        self.assertNotIn("intraday", r["benchmark"])

    def test_newer_underlying_price_does_not_refresh_old_option_delta(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        p = option(delta_asof=yesterday, underlying_price=100, underlying_price_asof=yesterday)
        r = analyze_exposure([p], history(), 10000)
        self.assertEqual(r["rows"][0]["price_asof"], TODAY.isoformat())
        self.assertEqual(r["rows"][0]["delta_asof"], yesterday)
        self.assertIsNone(r["rows"][0]["dollar_delta"])
        self.assertIsNone(r["summary"]["spx_delta"])

    def test_missing_benchmark_is_unknown_not_spx(self):
        h = history()
        h["series"].pop("SPX")
        r = analyze_exposure([position()], h, 10000)
        self.assertEqual(r["benchmark"]["label"], "基准未知")
        self.assertIsNone(r["benchmark"]["symbol"])
        self.assertIsNone(r["summary"]["spx_delta"])

    def test_missing_delta_is_not_zero_and_partial_scope_is_explicit(self):
        r = analyze_exposure([position(), option(id="option", delta=None)], history(), 10000)
        self.assertIsNone(r["rows"][1]["dollar_delta"])
        self.assertEqual(r["coverage"]["delta_covered"], 1)
        self.assertFalse(r["coverage"]["delta_complete"])
        self.assertEqual(r["summary"]["scope"], "covered_subset")
        self.assertIsNone(analyze_exposure([option(delta=None)], history(), 10000)["summary"]["net_dollar_delta"])

    def test_option_mark_never_substitutes_for_underlying_price(self):
        h = history()
        h["series"].pop("TEST")
        r = analyze_exposure([option(mark=3)], h, 10000)
        self.assertIsNone(r["rows"][0]["underlying_price"])
        self.assertIsNone(r["rows"][0]["dollar_delta"])

    def test_future_unknown_old_and_date_mismatched_delta_not_current(self):
        for asof in (None, "invalid", (TODAY + timedelta(days=1)).isoformat(),
                     (TODAY - timedelta(days=5)).isoformat(), (TODAY - timedelta(days=1)).isoformat()):
            with self.subTest(asof=asof):
                r = analyze_exposure([option(delta_asof=asof)], history(), 10000)
                self.assertIsNone(r["rows"][0]["dollar_delta"])
                self.assertFalse(r["coverage"]["delta_complete"])

    def test_zero_delta_is_real_when_timestamp_and_price_valid(self):
        r = analyze_exposure([option(delta=0)], history(), 10000)
        self.assertEqual(r["rows"][0]["dollar_delta"], 0)
        self.assertTrue(r["coverage"]["delta_complete"])

    def test_stale_history_and_missing_stock_price_timestamp_excluded(self):
        r = analyze_exposure([position()], history(offset=8), 10000)
        self.assertEqual(r["status"], "stale")
        self.assertIsNone(r["rows"][0]["dollar_delta"])
        h = history()
        h["series"].pop("TEST")
        r = analyze_exposure([position(mark=100)], h, 10000)
        self.assertIsNone(r["rows"][0]["dollar_delta"])

    def test_future_bars_are_cut_off_at_history_day(self):
        h = history()
        h["series"]["TEST"].append({"day": (TODAY + timedelta(days=1)).isoformat(), "close": 1e6})
        r = analyze_exposure([position()], h, 10000)
        self.assertLess(r["summary"]["net_dollar_delta"], 1e6)
        self.assertEqual(r["rows"][0]["beta_end"], TODAY.isoformat())

    def test_put_sign_confirmed_position_and_foreign_currency_fail_closed(self):
        for p in (option(delta=.3, right=None, call_put="P"), position(confirm=True), position(currency="SGD")):
            r = analyze_exposure([p], history(), 10000)
            self.assertIsNone(r["rows"][0]["dollar_delta"])

    def test_metadata_is_editable_etf_lookthrough_unknown_and_inputs_unchanged(self):
        h = history()
        h["series"]["QQQ"] = h["series"]["TEST"]
        inputs = [position(sym="QQQ")]
        original = copy.deepcopy(inputs)
        r = analyze_exposure(inputs, h, None)
        self.assertIn("未知", r["rows"][0]["sector"])
        self.assertIn("非纯科技", r["rows"][0]["theme"])
        self.assertIsNone(r["summary"]["gross_nlv_pct"])
        r = analyze_exposure(inputs, h, 10000, {"QQQ": {"theme": "自定义主题"}})
        self.assertEqual(r["rows"][0]["theme"], "自定义主题")
        self.assertEqual(inputs, original)


POLICY = {"nlv": 20000, "risk_pct": 2.0}


def noisy(n=280, initial=80):
    # Returns nearly uncorrelated with the benchmark: low R-squared.
    value, result = initial, []
    for i in range(n):
        value *= 1 + .02 * math.sin(i * 2.9 + 1.3) * math.cos(i * 1.7) + .0005 * math.sin(i * .73)
        result.append({"day": (TODAY - timedelta(days=n - 1 - i)).isoformat(), "close": value})
    return result


class MarketScenarioTests(unittest.TestCase):
    def scenario(self, positions, h=None, nlv=10000, policy=POLICY):
        exposure = analyze_exposure(positions, h or history(), nlv)
        return exposure, market_scenario(exposure, nlv, policy)

    def test_beta_weighted_net_pct_and_r_conversion(self):
        e, s = self.scenario([position()])
        beta_net = e["summary"]["beta_net_dollar_delta"]
        self.assertAlmostEqual(s["beta_net_dollar_delta"], beta_net, places=2)
        self.assertAlmostEqual(s["beta_net_nlv_pct"], beta_net / 10000 * 100, places=3)
        self.assertEqual(standard_r(POLICY), 400)
        self.assertEqual(s["one_r"], 400)
        by_key = {x["key"]: x for x in s["scenarios"]}
        self.assertEqual(set(by_key), {"up1", "down5", "down10"})
        self.assertAlmostEqual(by_key["up1"]["pnl"], beta_net * .01, places=2)
        self.assertAlmostEqual(by_key["down5"]["pnl"], -beta_net * .05, places=2)
        self.assertAlmostEqual(by_key["down10"]["r"], -beta_net * .10 / 400, places=3)
        self.assertIsNone(by_key["down10"]["r_note"])
        self.assertEqual(s["scope"], "portfolio")
        self.assertEqual((s["coverage"]["used"], s["coverage"]["total"]), (1, 1))
        self.assertTrue(any("不是最大损失或 VaR" in n for n in s["notices"]))
        self.assertTrue(any("约 100% ≈ 满仓持有 SPX" in n for n in s["notices"]))

    def test_short_hedge_gross_retained_as_unweighted_contrast(self):
        e, s = self.scenario([position(qty=10), position(id="hedge", qty=10, side="short")])
        self.assertEqual(s["beta_net_dollar_delta"], 0)
        self.assertAlmostEqual(s["gross_dollar_delta"], e["summary"]["gross_dollar_delta"], places=2)
        self.assertGreater(s["gross_dollar_delta"], 0)

    def test_unconfirmed_sizing_nlv_never_assumes_r(self):
        for policy in (None, {"nlv": None, "risk_pct": 2.0}, {"nlv": 0, "risk_pct": 2.0}, {"nlv": 20000, "risk_pct": None}):
            with self.subTest(policy=policy):
                _, s = self.scenario([position()], policy=policy)
                self.assertIsNone(s["one_r"])
                self.assertEqual(s["r_note"], R_UNAVAILABLE)
                self.assertTrue(all(x["r"] is None and x["r_note"] == R_UNAVAILABLE for x in s["scenarios"]))
                self.assertIsNotNone(s["scenarios"][1]["pnl"])

    def test_snapshot_nlv_missing_leaves_pct_unknown(self):
        _, s = self.scenario([position()], nlv=None)
        self.assertIsNone(s["beta_net_nlv_pct"])
        self.assertIsNotNone(s["beta_net_dollar_delta"])

    def test_missing_delta_not_zero_and_subset_listed(self):
        e, s = self.scenario([position(), option(id="option", delta=None)])
        self.assertEqual(s["scope"], "covered_subset")
        self.assertTrue(s["coverage"]["subset"])
        self.assertEqual((s["coverage"]["used"], s["coverage"]["total"]), (1, 2))
        self.assertEqual([x["id"] for x in s["coverage"]["excluded"]], ["option"])
        self.assertEqual(s["coverage"]["excluded"][0]["missing"], "缺 Delta 美元")
        self.assertIn("不得当作零", s["coverage"]["excluded"][0]["reason"])
        self.assertAlmostEqual(s["beta_net_dollar_delta"], e["rows"][0]["beta_dollar_delta"], places=2)
        self.assertTrue(any("仅覆盖子集" in n for n in s["notices"]))
        _, only_missing = self.scenario([option(delta=None)])
        self.assertEqual(only_missing["scope"], "unavailable")
        self.assertIsNone(only_missing["beta_net_dollar_delta"])
        self.assertTrue(all(x["pnl"] is None and x["r"] is None for x in only_missing["scenarios"]))

    def test_missing_beta_excluded_and_listed(self):
        h = history()
        h["series"]["SHORTHIST"] = prices(1, n=30, initial=40)
        _, s = self.scenario([position(), position(id="young", sym="SHORTHIST")], h)
        self.assertEqual((s["coverage"]["used"], s["coverage"]["total"]), (1, 2))
        self.assertEqual(s["coverage"]["excluded"][0]["missing"], "缺当前 Beta 加权")
        self.assertIn("共同日收益不足", s["coverage"]["excluded"][0]["reason"])

    def test_low_r_squared_symbols_listed(self):
        h = history()
        h["series"]["NOISY"] = noisy()
        e, s = self.scenario([position(), position(id="noisy", sym="NOISY")], h)
        noisy_row = next(r for r in e["rows"] if r["symbol"] == "NOISY")
        self.assertIsNotNone(noisy_row["beta_dollar_delta"])
        self.assertLess(noisy_row["r_squared"], .3)
        self.assertEqual([x["symbol"] for x in s["low_r_squared"]], ["NOISY"])
        self.assertTrue(any("低估这些标的的自身波动" in n for n in s["notices"]))
        _, clean = self.scenario([position()])
        self.assertEqual(clean["low_r_squared"], [])

    def test_spy_proxy_labels_scenario_as_spy(self):
        _, s = self.scenario([position()], history("SPY"))
        self.assertTrue(s["proxy"])
        self.assertTrue(s["scenarios"][1]["label"].startswith("SPY 代理"))
        self.assertTrue(any("满仓持有 SPY" in n for n in s["notices"]))

    def test_empty_portfolio(self):
        _, s = self.scenario([])
        self.assertEqual(s["scope"], "empty")
        self.assertEqual((s["coverage"]["used"], s["coverage"]["total"]), (0, 0))
        self.assertIsNone(s["beta_net_dollar_delta"])
        self.assertIsNone(s["gross_dollar_delta"])
        self.assertEqual(s["low_r_squared"], [])
        self.assertTrue(all(x["pnl"] is None and x["r"] is None for x in s["scenarios"]))


class BetaTests(unittest.TestCase):
    def test_ols_known_beta_correlation_window_and_volatility(self):
        r = estimate_beta(prices(2), prices(1))
        self.assertAlmostEqual(r["beta"], 2)
        self.assertEqual(r["samples"], 252)
        self.assertAlmostEqual(r["correlation"], 1)
        self.assertAlmostEqual(r["r_squared"], 1)
        self.assertGreater(r["annualized_vol_pct"], 0)

    def test_return_date_pairs_not_only_end_dates(self):
        market = prices(1, n=61)
        stock = prices(2, n=61)
        stock.pop(30)
        r = estimate_beta(stock, market)
        self.assertEqual(r["samples"], 58)
        self.assertIsNone(r["beta"])

    def test_minimum_boundary_and_zero_market_variance(self):
        self.assertIsNone(estimate_beta(prices(2, n=60), prices(1, n=60))["beta"])
        self.assertAlmostEqual(estimate_beta(prices(2, n=61), prices(1, n=61))["beta"], 2)
        constant = [{"day": p["day"], "close": 100} for p in prices(n=61)]
        self.assertIsNone(estimate_beta(prices(2, n=61), constant)["beta"])

    def test_duplicate_dates_rejected(self):
        bars = prices(2)
        bars.insert(10, bars[10])
        self.assertIsNone(estimate_beta(bars, prices(1))["beta"])



CLUSTER_POLICY = {"nlv": 100000, "risk_pct": 2}  # 1R = 2,000


def cluster_history(**series):
    return {"day": TODAY.isoformat(), "series": dict({"SPX": prices(1, initial=5000)}, **series), "errors": []}


def by_factor(result):
    return {g["factor"]: g for g in result["clusters"]}


class FactorClusterTests(unittest.TestCase):
    def run_clusters(self, positions, hist, policy=CLUSTER_POLICY):
        exposure = analyze_exposure(positions, hist, 100000)
        return exposure, factor_clusters(exposure, hist, 100000, policy)

    def test_default_explicit_and_fallback_labels(self):
        hist = cluster_history(JPM=prices(1, initial=900), META=prices(1), ZZZ=prices(1), XLF=prices(1, initial=50))
        positions = [position(id="gs", sym="JPM", qty=1), position(id="n", sym="META", qty=1),
                     position(id="z", sym="ZZZ", qty=1), position(id="x", sym="XLF", qty=1, factor="宽基"),
                     position(id="old", sym="KO", qty=1, factor="防御")]
        exposure, result = self.run_clusters(positions, hist)
        labels = {r["symbol"]: r["factor"] for r in exposure["rows"]}
        self.assertEqual(labels, {"JPM": "金融", "META": "M7", "ZZZ": "未分簇", "XLF": "宽基", "KO": "消费"})
        self.assertEqual(set(by_factor(result)), {"非科技", "科技", "宽基", "未分簇"})
        self.assertIsNone(by_factor(result)["未分簇"]["stress_pnl"])
        self.assertEqual(by_factor(result)["未分簇"]["status"], "unclustered")

    def test_proxy_stress_in_r_and_headroom(self):
        hist = cluster_history(XLF=prices(1, initial=50))
        _, result = self.run_clusters([position(sym="XLF", qty=200)], hist)
        g = by_factor(result)["非科技"]
        dollars = 200 * hist["series"]["XLF"][-1]["close"]
        shock = FACTOR_CLUSTERS["金融"]["shock"]
        self.assertEqual(g["cap_r"], FACTOR_BUCKETS["非科技"]["cap_r"])
        self.assertAlmostEqual(g["stress_pnl"], dollars * shock, places=1)
        self.assertAlmostEqual(g["stress_loss_r"], -dollars * shock / 2000, places=2)
        sleeve = g["sleeves"][0]
        self.assertEqual(sleeve["name"], "金融")
        self.assertAlmostEqual(sleeve["headroom_proxy_dollars"], (4 * 2000 + g["stress_pnl"]) / abs(shock), delta=1)
        self.assertEqual(g["members"][0]["sensitivity_method"], "代理本身")

    def test_regression_beta_scales_member_stress(self):
        hist = cluster_history(XLF=prices(1, initial=50), JPM=prices(2, initial=900))
        _, result = self.run_clusters([position(sym="JPM", qty=10)], hist)
        member = by_factor(result)["非科技"]["members"][0]
        self.assertAlmostEqual(member["sensitivity"], 2, places=2)
        self.assertIn("对 XLF 回归", member["sensitivity_method"])

    def test_missing_proxy_uses_volatility_ratio_upper_bound(self):
        hist = cluster_history(KO=prices(3, initial=100))
        _, result = self.run_clusters([position(sym="KO", qty=10)], hist)
        member = by_factor(result)["非科技"]["members"][0]
        self.assertIn("波动率比", member["sensitivity_method"])
        self.assertTrue(.5 <= member["sensitivity"] <= 3)

    def test_status_near_over_and_uncovered_not_zero(self):
        hist = cluster_history(XLF=prices(1, initial=50))
        close = hist["series"]["XLF"][-1]["close"]
        qty_for = lambda r: math.ceil(r * 2000 / (close * abs(FACTOR_CLUSTERS["金融"]["shock"])))
        _, ok = self.run_clusters([position(sym="XLF", qty=qty_for(2.1))], hist)
        _, near = self.run_clusters([position(sym="XLF", qty=qty_for(3.1))], hist)
        _, over = self.run_clusters([position(sym="XLF", qty=qty_for(4.1)), position(id="gs", sym="JPM", qty=5, confirm=True)], hist)
        self.assertEqual(by_factor(ok)["非科技"]["status"], "ok")  # 2R is a per-trade cap, not a bucket cap
        self.assertEqual(by_factor(near)["非科技"]["status"], "near")
        g = by_factor(over)["非科技"]
        self.assertEqual(g["status"], "over")
        self.assertEqual([u["symbol"] for u in g["uncovered"]], ["JPM"])
        self.assertTrue(any("缺 Delta 美元" in n for n in over["notices"]))

    def test_unconfirmed_nlv_leaves_r_unknown(self):
        hist = cluster_history(XLF=prices(1, initial=50))
        _, result = self.run_clusters([position(sym="XLF", qty=10)], hist, policy={})
        g = by_factor(result)["非科技"]
        self.assertIsNone(g["stress_loss_r"]); self.assertEqual(g["status"], "r_unavailable")

    def test_cross_cluster_co_movement_is_flagged(self):
        hist = cluster_history(XLF=prices(1, initial=50), META=prices(1.5, initial=100))
        _, result = self.run_clusters([position(id="a", sym="XLF", qty=10), position(id="b", sym="META", qty=10)], hist)
        pair = result["linked_pairs"][0]
        self.assertEqual({pair["a"], pair["b"]}, {"XLF", "META"})
        self.assertGreaterEqual(pair["correlation"], .7)

    def test_sleeves_sum_into_one_bucket_with_own_shocks(self):
        hist = cluster_history(IGV=prices(1), SMH=prices(1), QQQ=prices(1))
        positions = [position(id="s", sym="IGV", qty=100), position(id="h", sym="SMH", qty=100),
                     position(id="m", sym="QQQ", qty=100)]
        _, result = self.run_clusters(positions, hist)
        g = by_factor(result)["科技"]
        self.assertEqual(len(result["clusters"]), 1)
        self.assertEqual({s["name"] for s in g["sleeves"]}, {"软件", "硬件", "M7"})
        close = hist["series"]["IGV"][-1]["close"]
        expected = sum(100 * close * FACTOR_CLUSTERS[k]["shock"] for k in ("软件", "硬件", "M7"))
        self.assertAlmostEqual(g["stress_pnl"], expected, places=1)
        self.assertAlmostEqual(sum(s["stress_pnl"] for s in g["sleeves"]), g["stress_pnl"], places=1)
        self.assertEqual(result["weak_pairs"], [])  # hedges across sleeves are not "weak" labels


class PortfolioHeatTests(unittest.TestCase):
    def heat(self, positions, hist=None, policy=CLUSTER_POLICY):
        hist = hist or cluster_history(JPM=prices(1, initial=900), IAU=prices(1, initial=400))
        exposure = analyze_exposure(positions, hist, 100000)
        return hist, portfolio_heat(positions, exposure, policy)

    def stop(self, level, basis="und", direction="<="):
        return [{"kind": "止损", "basis": basis, "level": level, "dir": direction, "text": f"止损 {level}"}]

    def test_stock_heat_is_distance_to_stop_from_current_price(self):
        hist, h = self.heat([position(id="gs", sym="JPM", qty=20, trigs=self.stop(855))])
        close = hist["series"]["JPM"][-1]["close"]
        self.assertAlmostEqual(h["heat_usd"], 20 * (close - 855), places=1)
        self.assertAlmostEqual(h["heat_r"], 20 * (close - 855) / 2000, places=2)
        self.assertEqual(h["cap_r"], 8)

    def test_first_stop_wins_and_breached_stop_is_flagged(self):
        stops = self.stop(700) + self.stop(855)
        hist, h = self.heat([position(id="gs", sym="JPM", qty=20, trigs=stops)])
        self.assertAlmostEqual(h["heat_usd"], 20 * (hist["series"]["JPM"][-1]["close"] - 855), places=1)
        _, breached = self.heat([position(id="gs", sym="JPM", qty=20, trigs=self.stop(5000))])
        self.assertEqual(breached["heat_usd"], 0)
        self.assertIn("止损之外", breached["items"][0]["flag"])

    def test_long_option_uses_delta_capped_by_premium(self):
        opt = option(id="c", sym="IAU", qty=1, delta=.4, mark=2.0, trigs=self.stop(300))
        hist, h = self.heat([opt])
        self.assertEqual(h["heat_usd"], 200)  # linear 40 x ~100 would exceed the $200 premium
        self.assertIn("封顶", h["items"][0]["method"])
        near = option(id="c", sym="IAU", qty=1, delta=.4, mark=20.0,
                      trigs=self.stop(hist["series"]["IAU"][-1]["close"] - 5))
        _, h2 = self.heat([near])
        self.assertAlmostEqual(h2["heat_usd"], 40 * 5, places=1)

    def test_no_stop_rules(self):
        positions = [position(id="basket", sym="JPM", qty=2),
                     option(id="lc", sym="IAU", qty=2, mark=1.5),
                     option(id="naked", sym="IAU", side="short", qty=1, mark=3.0, strike=500, expiry="2027-03-19"),
                     option(id="a", sym="IAU", qty=2, mark=2.20, group="蝶", strike=50, expiry="2027-03-19"),
                     option(id="b", sym="IAU", side="short", qty=4, mark=.70, group="蝶", strike=60, expiry="2027-03-19"),
                     option(id="c", sym="IAU", qty=2, mark=.40, group="蝶", strike=70, expiry="2027-03-19")]
        _, h = self.heat(positions)
        by_id = {i["id"]: i for i in h["items"]}
        self.assertEqual(by_id["lc"]["heat_usd"], 300)
        self.assertAlmostEqual(by_id["a,b,c"]["heat_usd"], 240, places=2)
        self.assertEqual([x["id"] for x in h["no_stop"]], ["basket"])
        self.assertEqual(h["exempt"], [])
        self.assertEqual([x["id"] for x in h["unbounded"]], ["naked"])
        self.assertAlmostEqual(h["heat_usd"], 540, places=2)

    def test_stop_against_cost_trailed_up_and_measured_from_the_first_stop(self):
        def stop(level, level0=None, moves=0):
            return [{"kind": "止损", "basis": "und", "dir": "<=", "level": level, "level0": level0, "moves": [{}] * moves}]
        positions = [position(id="a", sym="AAA", qty=50, open=100.0, mark=112.0, trigs=stop(95.0, 95.0)),
                     position(id="b", sym="BBB", qty=50, open=100.0, mark=112.0, trigs=stop(105.0, 95.0, 2)),
                     position(id="c", sym="CCC", qty=50, open=100.0, mark=112.0, trigs=stop(105.0, 105.0))]
        _, h = self.heat(positions)
        by = {i["id"]: i for i in h["items"]}
        # a: stop below cost; the multiple is -1 at the first stop and open profit is 12/5 of the initial risk
        self.assertEqual(by["a"]["cost_result_usd"], -250.0)
        self.assertEqual(by["a"]["initial_risk_usd"], 250.0)
        self.assertEqual(by["a"]["stop_multiple"], -1.0)
        self.assertEqual(by["a"]["open_multiple"], 2.4)
        # b: trailed up to 105: profit locked, heat (from the current price) is still 7 x 50, denominator stays at the first stop
        self.assertEqual(by["b"]["cost_result_usd"], 250.0)
        self.assertEqual(by["b"]["heat_usd"], 350.0)
        self.assertEqual(by["b"]["stop_multiple"], 1.0)
        self.assertEqual(by["b"]["moves"], 2)
        # c: the first stop was already above cost, so there is no initial risk and no multiple
        self.assertEqual(by["c"]["cost_result_usd"], 250.0)
        self.assertNotIn("initial_risk_usd", by["c"])
        self.assertNotIn("stop_multiple", by["c"])
        self.assertEqual(h["locked_usd"], 500.0)
        self.assertEqual(h["loss_risk_usd"], 250.0)

    def test_exempt_holdings_are_listed_apart_from_no_stop_stocks(self):
        positions = [position(id="open", sym="UNH", qty=5, mark=500.0),
                     position(id="cash", sym="BIL", qty=250, mark=91.5, group="现金管理 · 闲钱停放（免判据）")]
        _, h = self.heat(positions)
        self.assertEqual([x["id"] for x in h["no_stop"]], ["open"])
        self.assertEqual([x["id"] for x in h["exempt"]], ["cash"])
        self.assertEqual(h["no_stop_usd"], 2500.0)
        self.assertEqual(h["exempt_usd"], 22875.0)

    def test_uncovered_short_group_is_unbounded_and_unconfirmed_nlv_unknown(self):
        positions = [option(id="a", sym="IAU", qty=1, mark=2.0, group="x", strike=100, expiry="2027-03-19"),
                     option(id="b", sym="IAU", side="short", qty=2, mark=1.0, group="x", strike=110, expiry="2027-03-19")]
        _, h = self.heat(positions, policy={})
        self.assertEqual(h["heat_usd"], 0)
        self.assertEqual(len(h["unbounded"]), 1)
        self.assertIsNone(h["heat_r"]); self.assertEqual(h["status"], "r_unavailable")

    def butterfly(self):
        return [option(id=id, sym="IAU", qty=q, side=side, mark=mark, strike=k,
                       expiry="2027-03-19", group="fly")
                for id,q,side,mark,k in [("a",3,"long",2.23,500),("b",6,"short",.715,600),("c",3,"long",.45,700)]]

    def test_explicit_summary_is_display_only_and_never_double_counted(self):
        legs = self.butterfly()
        summary = option(id="summary", sym="IAU", qty=3, mark=1.25, delta=None,
                         expiry="2027-03-19", delta_status="combo", component_ids=["a","b","c"])
        original = copy.deepcopy(legs + [summary])
        _, h = self.heat(original)
        self.assertEqual(h["heat_usd"], 375)
        self.assertEqual(len(h["display_summaries"]), 1)
        e = analyze_exposure(original, cluster_history(IAU=prices(1)), 100000)
        self.assertEqual(e["coverage"]["total"], 3)
        self.assertTrue(e["coverage"]["delta_complete"])
        self.assertEqual(original, legs + [summary])

    def test_legacy_summary_ids_require_all_components_and_same_identity(self):
        legs = self.butterfly()
        summary = option(id="summary", sym="IAU", qty=3, mark=1.25, expiry="2027-03-19",
                         delta_status="combo", delta_missing_reason="组合汇总行；见各腿 a/b/c")
        active, summaries = partition_positions(legs + [summary])
        self.assertEqual(len(active), 3)
        self.assertEqual(summaries[0]["component_ids"], ["a","b","c"])
        for altered in (legs[:2], [dict(p, account_id="other") for p in legs],
                        [dict(p, expiry="2027-02-19") for p in legs]):
            _, h = self.heat(altered+[summary])
            self.assertTrue(any(x["id"]=="summary" for x in h["unknown"]))
            self.assertIsNone(h["headroom_r"])

    def test_same_labels_and_prices_never_establish_summary_identity(self):
        legs = self.butterfly()
        independent = option(id="independent",sym="IAU",qty=3,mark=1.25,label="fly")
        _, h = self.heat(legs+[independent])
        self.assertEqual(h["heat_usd"],750)
        self.assertEqual(h["display_summaries"],[])

    def test_broken_wing_payoff_can_lose_more_than_current_debit(self):
        legs=[option(id=str(i),sym="IAU",qty=q,side=side,mark=mark,strike=k,
                     expiry="2027-03-19",group="broken wing")
              for i,(q,side,mark,k) in enumerate([(1,"long",17,90),(2,"short",10,100),(1,"long",4,120)])]
        _,h=self.heat(legs)
        self.assertEqual(h["heat_usd"],1100)

    def test_credit_spread_and_short_put_have_finite_downside(self):
        legs=[option(id="a",sym="IAU",qty=1,side="short",mark=5,strike=100,right="P",expiry="2027-03-19",group="credit"),
              option(id="b",sym="IAU",qty=1,side="long",mark=2,strike=90,right="P",expiry="2027-03-19",group="credit")]
        _,h=self.heat(legs)
        self.assertEqual(h["heat_usd"],700)
        _,single=self.heat([dict(legs[0],group="")])
        self.assertEqual(single["heat_usd"],9500)
        self.assertEqual(single["unbounded"],[])

    def test_calendar_and_incomplete_contract_never_assumed_premium_only(self):
        for change in ({"expiry":"2027-02-19"},{"strike":None},{"right":""},{"mult":10}):
            legs=self.butterfly();legs[1].update(change)
            _,h=self.heat(legs)
            self.assertEqual(h["heat_usd"],0)
            self.assertTrue(h["unknown"])
            self.assertEqual(h["status"],"partial")
            self.assertIsNone(h["headroom_r"])

    def test_same_group_name_never_offsets_across_accounts_or_symbols(self):
        for change in ({"account_id":"other"},{"sym":"OTHER"}):
            legs=self.butterfly();legs[1].update(change)
            _,h=self.heat(legs)
            self.assertTrue(h["unbounded"])
            self.assertIsNone(h["headroom_r"])


if __name__ == "__main__":
    unittest.main()


class RBudgetTests(unittest.TestCase):
    """owner 2026-10-27: core 3-5R; options expiring within two weeks have their own allowance."""
    def heat(self, items, one_r=2000):
        for i in items:
            i.setdefault("heat_r", i["heat_usd"] / one_r)
        return {"one_r": one_r, "items": items}

    def day(self, n):
        return (TODAY + timedelta(days=n)).isoformat()

    def test_short_dated_options_are_split_from_the_core(self):
        positions = [position(id="s"), option(id="far", expiry=self.day(45), delta=.7),
                     option(id="wk", expiry=self.day(5), delta=.5)]
        heat = self.heat([{"id": "s", "symbol": "TEST", "label": "正股", "heat_usd": 4000, "cost_result_usd": -500},
                          {"id": "far", "symbol": "TEST", "label": "45 天", "heat_usd": 2000},
                          {"id": "wk", "symbol": "TEST", "label": "周档", "heat_usd": 300}])
        b = r_budget(heat, positions, TODAY)
        self.assertAlmostEqual(b["core_r"], 3.0)
        self.assertAlmostEqual(b["short_r"], .15)
        self.assertAlmostEqual(b["expiring_r"], .15)
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["lottery_r"], 0)

    def test_locked_stock_takes_zero_r_and_low_core_warns(self):
        heat = self.heat([{"id": "s", "symbol": "NOW", "label": "正股", "heat_usd": 1000, "cost_result_usd": 200}])
        b = r_budget(heat, [position(id="s")], TODAY)
        self.assertEqual(b["core_r"], 0)
        self.assertEqual(len(b["locked"]), 1)
        self.assertEqual(b["status"], "below")
        self.assertAlmostEqual(b["core_gap_r"], 3.0)
        self.assertIn("低于目标下限", b["warnings"][0])

    def test_otm_short_dated_is_a_lottery_with_tighter_caps(self):
        positions = [option(id="a", expiry=self.day(3), delta=.15), option(id="b", expiry=self.day(3), delta=.2),
                     option(id="c", expiry=self.day(10), delta=.55)]
        heat = self.heat([{"id": "a", "symbol": "HAL", "label": "45C", "heat_usd": 300},
                          {"id": "b", "symbol": "PFE", "label": "32C", "heat_usd": 160},
                          {"id": "c", "symbol": "SMH", "label": "SMH 价差", "heat_usd": 500}])
        b = r_budget(heat, positions, TODAY)
        self.assertAlmostEqual(b["lottery_r"], .23)
        self.assertAlmostEqual(b["short_r"], .48)
        text = " ".join(b["warnings"])
        self.assertIn("彩票额度", text)          # 0.23R > 0.2R
        self.assertIn("HAL 45C", text)         # 0.15R > 0.1R single lottery
        self.assertIn("SMH 价差 占 0.25R", text)  # 0.25R > 0.2R single short; name not doubled
        self.assertNotIn("两周内到期的期权合计", text)  # 0.48R within 0.5R
        self.assertEqual(b["lottery_room_r"], 0)

    def test_spread_group_uses_long_leg_delta_and_nearest_expiry(self):
        positions = [option(id="l", expiry=self.day(12), delta=.45, group="g"),
                     option(id="s", side="short", expiry=self.day(12), delta=.2, group="g")]
        b = r_budget(self.heat([{"id": "l,s", "symbol": "QCOM", "label": "150/160", "heat_usd": 500}]), positions, TODAY)
        self.assertEqual(b["short"][0]["dte"], 12)
        self.assertFalse(b["short"][0]["lottery"])

    def test_without_one_r_there_are_no_warnings(self):
        b = r_budget({"one_r": None, "items": []}, [], TODAY)
        self.assertEqual(b["status"], "r_unavailable")
        self.assertEqual(b["warnings"], [])

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from workbench import macro
from workbench.exposure import FACTOR_BUCKETS, FACTOR_CLUSTERS, HEAT_CAP_R, analyze_exposure, factor_clusters
from test_workbench_exposure import CLUSTER_POLICY, by_factor, cluster_history, position, prices


def write_state(directory, **over):
    body = {"schema": "macro-panel/1", "data_day": "2026-09-28", "light": "黄", "stage": "normal",
            "knobs": {"heat_cap_r": 6, "bucket_scale": 0.75, "unconfirmed": "x", "insurance": "y"},
            "families": [{"key": "F1", "grade": "红"}], "history": []}
    body.update(over)
    path = Path(directory) / "state.json"
    path.write_text(json.dumps(body, ensure_ascii=False))
    return path


class MacroLoadTests(unittest.TestCase):
    def test_ok_scales_caps(self):
        with tempfile.TemporaryDirectory() as d:
            m = macro.load(write_state(d), today=date(2026, 9, 29))
        self.assertEqual(m["status"], "ok")
        self.assertEqual(macro.heat_cap(HEAT_CAP_R, m), 6)
        scaled = macro.scaled_buckets(FACTOR_BUCKETS, m)
        self.assertEqual(scaled["科技"]["cap_r"], 3)
        self.assertEqual(scaled["科技"]["cap_base_r"], FACTOR_BUCKETS["科技"]["cap_r"])
        self.assertEqual(FACTOR_BUCKETS["科技"]["cap_r"], 4)  # static table untouched

    def test_missing_stale_invalid_keep_static_caps(self):
        with tempfile.TemporaryDirectory() as d:
            missing = macro.load(Path(d) / "none.json")
            stale = macro.load(write_state(d), today=date(2026, 10, 20))
            (Path(d) / "bad.json").write_text("{")
            invalid = macro.load(Path(d) / "bad.json")
            nofield = macro.load(write_state(d, knobs={}), today=date(2026, 9, 29))
        for m, status in [(missing, "missing"), (stale, "stale"), (invalid, "invalid"), (nofield, "invalid")]:
            self.assertEqual(m["status"], status)
            self.assertEqual(macro.heat_cap(HEAT_CAP_R, m), HEAT_CAP_R)
            self.assertIs(macro.scaled_buckets(FACTOR_BUCKETS, m), FACTOR_BUCKETS)
            self.assertTrue(m["notice"])


class HedgeSizingTests(unittest.TestCase):
    def test_over_scaled_cap_reports_proxy_hedge(self):
        hist = cluster_history(XLF=prices(1, initial=50))
        positions = [position(sym="XLF", qty=2000)]
        exposure = analyze_exposure(positions, hist, 100000)
        m = {"status": "ok", "bucket_scale": 0.5, "heat_cap_r": 4}
        g = by_factor(factor_clusters(exposure, hist, 100000, CLUSTER_POLICY,
                                      buckets=macro.scaled_buckets(FACTOR_BUCKETS, m)))["非科技"]
        self.assertEqual(g["cap_r"], 2); self.assertEqual(g["cap_base_r"], 4)
        shock = abs(FACTOR_CLUSTERS["金融"]["shock"])
        self.assertAlmostEqual(g["excess_r"], g["stress_loss_r"] - 2, places=2)
        expected = (-g["stress_pnl"] / 2000 - 2) * 2000 / shock  # 超出 2R 的部分换成代理 ETF 美元
        self.assertAlmostEqual(g["sleeves"][0]["hedge_proxy_dollars"], expected, delta=1)
        self.assertEqual(g["status"], "over")

    def test_under_cap_has_no_hedge(self):
        hist = cluster_history(XLF=prices(1, initial=50))
        exposure = analyze_exposure([position(sym="XLF", qty=10)], hist, 100000)
        g = by_factor(factor_clusters(exposure, hist, 100000, CLUSTER_POLICY))["非科技"]
        self.assertIsNone(g["excess_r"]); self.assertIsNone(g["sleeves"][0]["hedge_proxy_dollars"])


if __name__ == "__main__":
    unittest.main()

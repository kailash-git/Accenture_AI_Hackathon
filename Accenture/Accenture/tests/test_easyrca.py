"""
Tests for the causal RCA layer (EasyRCA reimplementation) -- correctness of
the port, independent of the experiment that motivated adding it.
"""
import os
import sys
import unittest
import warnings

import numpy as np
import pandas as pd
import networkx as nx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, "src"))

from analytics.causal_graph import (
    SUMMARY_CAUSAL_GRAPH, VARIABLES, variable_of, backdoor_adjustment_set,
)
from analytics.easy_rca import find_root_causes, derive_windows_for_period
from analytics.rca_series import build_weekly_panel, clear_cache

DB_PATH = os.path.join(BASE_DIR, "data", "business_bi.db")


def _linear_panel(n, coefs, noise_sd, rng, base=8.0):
    order = list(nx.topological_sort(SUMMARY_CAUSAL_GRAPH))
    data = {}
    for v in order:
        x = base + rng.normal(0, noise_sd, n)
        for u in SUMMARY_CAUSAL_GRAPH.predecessors(v):
            x = x + coefs.get((u, v), 0.5) * data[u]
        data[v] = x
    return pd.DataFrame({v: data[v] for v in VARIABLES})


class TestCausalRCA(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")

    def test_summary_causal_graph_is_dag(self):
        self.assertTrue(nx.is_directed_acyclic_graph(SUMMARY_CAUSAL_GRAPH))
        self.assertEqual(set(SUMMARY_CAUSAL_GRAPH.nodes), set(VARIABLES))
        g = type("G", (), {"has_node": lambda self, n: True,
                           "nodes": {"x": {"kind": "sales_anomaly", "column": "units"}}})()
        self.assertIn(variable_of(g, "x"), SUMMARY_CAUSAL_GRAPH.nodes)
        self.assertTrue(backdoor_adjustment_set(SUMMARY_CAUSAL_GRAPH, "units", "revenue")
                        .issubset(set(VARIABLES)))

    def test_recovers_structural_root_not_the_mediator(self):
        rng = np.random.default_rng(1)
        coefs = {}
        normal = _linear_panel(40, coefs, 0.3, rng)
        anom = _linear_panel(8, coefs, 0.3, rng)
        anom["marketing_spend"] = anom["marketing_spend"] + 12.0
        for v in nx.topological_sort(SUMMARY_CAUSAL_GRAPH):
            if v == "marketing_spend":
                continue
            if "marketing_spend" in nx.ancestors(SUMMARY_CAUSAL_GRAPH, v):
                parents = list(SUMMARY_CAUSAL_GRAPH.predecessors(v))
                anom[v] = 8.0 + sum(coefs.get((u, v), 0.5) * anom[u] for u in parents)
        r = find_root_causes(normal, anom, anomalous_vars=["marketing_spend", "units", "revenue"])
        roots = {rc["variable"] for rc in r["root_causes"]}
        self.assertIn("marketing_spend", roots)
        self.assertNotIn("revenue", roots)
        self.assertEqual(r["root_causes"][0]["variable"], "marketing_spend")

    def test_detects_mechanism_shift(self):
        rng = np.random.default_rng(2)
        n = 40
        price_n = 8.0 + rng.normal(0, 1.0, n)
        price_a = 12.0 + rng.normal(0, 1.0, 10)
        normal = pd.DataFrame({v: 8.0 + rng.normal(0, 0.3, n) for v in VARIABLES})
        normal["sell_price"] = price_n
        normal["units"] = 5.0 + 1.0 * price_n + rng.normal(0, 0.3, n)
        anom = pd.DataFrame({v: 8.0 + rng.normal(0, 0.3, 10) for v in VARIABLES})
        anom["sell_price"] = price_a
        anom["units"] = 5.0 + 4.0 * price_a + rng.normal(0, 0.3, 10)
        r = find_root_causes(normal, anom, anomalous_vars=["sell_price", "units"])
        mechs = {rc["variable"]: rc["mechanism"] for rc in r["root_causes"]}
        self.assertEqual(mechs.get("units"), "mechanism_shift")
        self.assertEqual(mechs.get("sell_price"), "structural_root")

    def test_abstains_on_short_window(self):
        rng = np.random.default_rng(3)
        normal = _linear_panel(40, {}, 0.3, rng)
        anom = _linear_panel(2, {}, 0.3, rng)
        anom["units"] = anom["units"] + 20.0
        anom["revenue"] = anom["revenue"] + 40.0
        r = find_root_causes(normal, anom, anomalous_vars=["units", "revenue"])
        self.assertIn(r["status"], ("insufficient_data", "ok"))
        self.assertNotIn("revenue", {rc["variable"] for rc in r["root_causes"]
                                     if rc["mechanism"] == "mechanism_shift"})

    @unittest.skipUnless(os.path.exists(DB_PATH), "seeded DB required")
    def test_weekly_panel_and_real_scenarios(self):
        clear_cache()
        p = build_weekly_panel(DB_PATH, "FOODS_3_090", "CA")
        self.assertListEqual(list(p.columns), list(VARIABLES))
        self.assertTrue(isinstance(p.index, pd.DatetimeIndex))
        self.assertEqual(int(p.isna().sum().sum()), 0)

        def roots(item, state, tv, ps, pe):
            panel = build_weekly_panel(DB_PATH, item, state)
            w = derive_windows_for_period(panel, tv, ps, pe)
            r = find_root_causes(w["normal_df"], w["anom_df"],
                                 anomalous_vars=w["anomalous_vars"], onsets=w["onsets"] or None)
            return [rc["variable"] for rc in r["root_causes"]]

        # 25% price cut -> sell_price is the (only) root cause
        self.assertEqual(roots("FOODS_3_090", "CA", "revenue", "2013-08-01", "2013-08-31"), ["sell_price"])
        # Port-of-Seattle stockout -> stockout_days is the top root cause
        self.assertEqual(roots("FOODS_3_090", "CA", "revenue", "2012-11-01", "2012-11-30")[0], "stockout_days")


if __name__ == "__main__":
    unittest.main()

"""
tests/test_graph.py

Covers the anomaly-centric evidence graph that replaced the old feedback-BFS
knowledge_graph:
  * analytics.graph_builder.build_graph  -- node kinds, PVM reconciliation
  * analytics.graph_query               -- entity_relevant, explain_revenue_drop
  * analytics.graph_subgraph            -- per-anomaly subgraph the API/panel use
"""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from analytics.graph_builder import build_graph  # noqa: E402
from analytics import graph_query as GQ  # noqa: E402
from analytics.graph_subgraph import anomaly_subgraph  # noqa: E402
from analytics.graph_narrative_adapter import legacy_graph_results  # noqa: E402


class TestEvidenceGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(BASE_DIR, 'data', 'business_bi.db')
        assert os.path.exists(cls.db_path), f"Seeded database missing at {cls.db_path}"
        cls.graph = build_graph(cls.db_path)

    # ---- build_graph -----------------------------------------------------
    def test_entity_and_anomaly_layers_present(self):
        kinds = {}
        for _, a in self.graph.nodes(data=True):
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        # entity layer
        for k in ("item_entity", "state_entity", "warehouse_entity", "channel_entity"):
            self.assertGreater(kinds.get(k, 0), 0, f"missing entity kind {k}")
        # anomaly layer -- the loose z=1.1 threshold makes these plentiful
        self.assertGreater(kinds.get("sales_anomaly", 0), 100)
        self.assertGreater(kinds.get("inventory_anomaly", 0), 0)
        self.assertTrue(self.graph.has_node("item_FOODS_3_090"))

    def test_pvm_decomposition_reconciles_exactly(self):
        # add_explains_edges returns 0 mismatches -- pure algebra, any drift is a bug
        self.assertEqual(self.graph.graph.get("pvm_mismatches"), 0)

    def test_injected_supply_constraint_became_a_node(self):
        # inject_scenario_impacts drops fill_rate for WH-1000/CA in 2012-11
        self.assertTrue(self.graph.has_node("supply_anom_WH-1000_CA_2012-11"))
        self.assertLess(self.graph.nodes["supply_anom_WH-1000_CA_2012-11"]["fill_rate"], 0.90)

    # ---- graph_query ---------------------------------------------------------
    def test_entity_relevant_rejects_cross_item(self):
        # entity_relevant compares two anomaly/event nodes by shared item entity.
        def a_sales(item, column):
            return next(n for n, at in self.graph.nodes(data=True)
                        if at["kind"] == "sales_anomaly" and at.get("column") == column
                        and at.get("item") == item)
        rev_090 = a_sales("FOODS_3_090", "revenue")
        units_090 = a_sales("FOODS_3_090", "units")
        rev_586 = a_sales("FOODS_3_586", "revenue")
        self.assertTrue(GQ.entity_relevant(self.graph, rev_090, units_090))
        self.assertFalse(GQ.entity_relevant(self.graph, rev_090, rev_586))

    def test_explain_revenue_drop_shape(self):
        rev = next(at for _, at in self.graph.nodes(data=True)
                   if at["kind"] == "sales_anomaly" and at.get("column") == "revenue")
        r = GQ.explain_revenue_drop(self.graph, rev["item"], rev["state"], rev["date"])
        self.assertIsNotNone(r)
        for key in ("drivers", "supply_evidence", "marketing_evidence", "review_sentiment", "same_day_event"):
            self.assertIn(key, r)

    # ---- graph_subgraph ----------------------------------------------------
    def test_subgraph_resolves_focal_for_supply_scenario(self):
        sub = anomaly_subgraph(self.graph, "Revenue", "FOODS_3_090", "CA",
                               "2012-11-01", "2012-11-30")
        self.assertEqual(sub["focal"], "revenue_anom_FOODS_3_090_CA_2012-11-18")
        layers = {n["layer"] for n in sub["nodes"]}
        self.assertLessEqual(max(layers), 2)
        kinds = {n["kind"] for n in sub["nodes"]}
        self.assertIn("supply_anomaly", kinds)   # injected constraint corroborates
        self.assertIn("item_entity", kinds)

    def test_subgraph_edges_reference_only_present_nodes(self):
        sub = anomaly_subgraph(self.graph, "Revenue", "FOODS_3_090", "CA",
                               "2013-08-01", "2013-08-31")
        ids = {n["id"] for n in sub["nodes"]}
        for e in sub["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)
        # the Aug-2013 price cut should surface a price/volume explains driver
        self.assertTrue(any(e["relation"] == "explains" for e in sub["edges"]))

    def test_subgraph_edges_carry_day_diff_and_recency(self):
        sub = anomaly_subgraph(self.graph, "Revenue", "FOODS_3_090", "CA",
                               "2012-11-01", "2012-11-30")
        focal = sub["focal"]
        anom_kinds = {"sales_anomaly", "inventory_anomaly", "marketing_anomaly",
                      "supply_anomaly", "review_shift", "event"}
        by_id = {n["id"]: n for n in sub["nodes"]}
        temporal = [e for e in sub["edges"]
                    if by_id.get(e["source"], {}).get("kind") in anom_kinds
                    and by_id.get(e["target"], {}).get("kind") in anom_kinds]
        self.assertTrue(temporal, "expected at least one anomaly<->anomaly edge")
        for e in temporal:
            self.assertIn("day_diff", e)
            self.assertIsInstance(e["day_diff"], int)
            if focal in (e["source"], e["target"]):
                self.assertIn("recency_weight", e)
                self.assertGreater(e["recency_weight"], 0.0)
                self.assertLessEqual(e["recency_weight"], 1.0)
        # a same-day (explains) edge weights 1.0; an older corroborator weights less
        same_day = [e for e in temporal if e.get("days_from_focal") == 0]
        older = [e for e in temporal if (e.get("days_from_focal") or 0) < 0]
        if same_day and older:
            self.assertGreater(max(e["recency_weight"] for e in same_day),
                               max(e["recency_weight"] for e in older))
        # layer-2 nodes carry the recency fields too
        for n in sub["nodes"]:
            if n["layer"] == 2 and n["kind"] in anom_kinds:
                self.assertIn("recency_weight", n)
                self.assertIn("days_from_focal", n)

    def test_subgraph_capped_for_legibility(self):
        sub = anomaly_subgraph(self.graph, "GrossMarginPercent", "FOODS_3_090", "CA",
                               "2012-10-01", "2012-10-31")
        layer2 = [n for n in sub["nodes"] if n["layer"] == 2]
        self.assertLessEqual(len(layer2), 8)

    def test_subgraph_unknown_kpi_period_returns_entity_anchor(self):
        sub = anomaly_subgraph(self.graph, "Revenue", "FOODS_3_090", "CA",
                               "1990-01-01", "1990-01-31")
        self.assertIsNone(sub["focal"])
        self.assertTrue(any(n["kind"] == "item_entity" for n in sub["nodes"]))

    # ---- narrative adapter -----------------------------------------------
    def test_legacy_adapter_shape(self):
        lg = legacy_graph_results(self.graph, "Revenue", "FOODS_3_090", "CA",
                                  "2012-11-01", "2012-11-30")
        self.assertIn("hops", lg)
        for h in lg["hops"]:
            self.assertIn(h["temporal_role"], ("preceding_cause", "concurrent_or_aftermath"))
            self.assertIn("text", h)
            self.assertEqual(h["hops"], 2)


if __name__ == "__main__":
    unittest.main()

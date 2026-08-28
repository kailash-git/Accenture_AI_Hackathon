import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from retrieval.knowledge_graph import build_graph, get_related_context  # noqa: E402


class TestKnowledgeGraph(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(BASE_DIR, 'data', 'business_bi.db')
        self.assertTrue(os.path.exists(self.db_path), f"Seeded database missing at {self.db_path}")
        self.graph = build_graph(self.db_path)

    def test_graph_has_nodes(self):
        self.assertGreater(self.graph.number_of_nodes(), 0)
        self.assertTrue(self.graph.has_node("item:FOODS_3_090"))

    def test_multi_hop_traversal_finds_supply_feedback(self):
        result = get_related_context(
            self.graph, item_id="FOODS_3_090", state_id="CA",
            period_start="2012-11-01", period_end="2012-11-30", max_hops=3,
        )
        self.assertGreater(len(result["hops"]), 0)
        sources = [h["source"] for h in result["hops"]]
        self.assertTrue(any("support ticket" in s or "customer review" in s for s in sources))

    def test_temporal_filter_excludes_unrelated_month(self):
        # The Aug 2013 pricecut review must NOT leak into the Nov 2012 supply anomaly's context,
        # even under a deliberately wide window (Aug 2013 is ~9 months away either way).
        result = get_related_context(
            self.graph, item_id="FOODS_3_090", state_id="CA",
            period_start="2012-11-01", period_end="2012-11-30", max_hops=3,
            window_days_before=45, window_days_after=45,
        )
        dates = [h["date"] for h in result["hops"]]
        self.assertFalse(any(d.startswith("2013-08") for d in dates))

    def test_default_window_excludes_adjacent_month_event(self):
        # Regression: the Nov 20-22, 2012 supply-stockout feedback must not leak into
        # the *previous* month's (Oct 2012) FOODS_3_090/CA graph context under the
        # default window -- it did when this traversal used a +/-45-day window while
        # evidence_reconciler used -5/+10 days, producing a graph node/hop that looked
        # like corroborating evidence for an anomaly it has nothing to do with.
        result = get_related_context(
            self.graph, item_id="FOODS_3_090", state_id="CA",
            period_start="2012-10-01", period_end="2012-10-31", max_hops=3,
        )
        dates = [h["date"] for h in result["hops"]]
        self.assertFalse(any(d.startswith("2012-11") for d in dates))

    def test_unknown_item_returns_empty(self):
        result = get_related_context(self.graph, item_id="NOPE", state_id="ZZ", period_start="2020-01-01")
        self.assertEqual(result["hops"], [])

    def test_graph_export_has_real_nodes_and_edges(self):
        result = get_related_context(
            self.graph, item_id="FOODS_3_090", state_id="CA",
            period_start="2012-11-01", period_end="2012-11-30", max_hops=3,
        )
        graph = result["graph"]
        self.assertGreater(len(graph["nodes"]), 0)
        self.assertGreater(len(graph["edges"]), 0)
        node_ids = {n["id"] for n in graph["nodes"]}
        # Every edge endpoint must be a node actually present in the export.
        for e in graph["edges"]:
            self.assertIn(e["source"], node_ids)
            self.assertIn(e["target"], node_ids)
        self.assertIn("item:FOODS_3_090", node_ids)

    def test_graph_export_excludes_out_of_window_feedback(self):
        # The Aug 2013 price-cut review must not appear as a node in the Nov 2012
        # supply anomaly's graph, mirroring the same temporal filter applied to hops.
        result = get_related_context(
            self.graph, item_id="FOODS_3_090", state_id="CA",
            period_start="2012-11-01", period_end="2012-11-30", max_hops=3,
            window_days_before=45, window_days_after=45,
        )
        feedback_nodes = [n for n in result["graph"]["nodes"] if n["type"] == "feedback"]
        self.assertFalse(any("2013-08" in n["label"] for n in feedback_nodes))


if __name__ == "__main__":
    unittest.main()

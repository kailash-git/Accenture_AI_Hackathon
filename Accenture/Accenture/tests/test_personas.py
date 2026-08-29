import importlib.util
import json
import os
import sqlite3
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from llm.schema_parser import validate_narrative_bundle  # noqa: E402

FORBIDDEN_FOR_PLANNER = ("gross margin", "cost of goods sold", "marketing spend", "revenue")
FORBIDDEN_FOR_VP = ("warehouse",)


def _load_api_server_module():
    spec = importlib.util.spec_from_file_location("api_server", os.path.join(ROOT_DIR, "api_server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPersonaNarratives(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(BASE_DIR, 'data', 'business_bi.db')
        self.assertTrue(os.path.exists(self.db_path), f"Seeded database missing at {self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_every_anomaly_has_a_valid_dual_persona_bundle(self):
        cur = self.conn.cursor()
        cur.execute("SELECT scenario_key, narratives_json FROM anomalies")
        rows = cur.fetchall()
        self.assertGreater(len(rows), 0, "No anomalies found -- run scripts/generate_mock_data.py first")

        for r in rows:
            narratives = json.loads(r["narratives_json"])
            self.assertIn("vp_sales", narratives, f"{r['scenario_key']} missing vp_sales narrative")
            self.assertIn("supply_planner", narratives, f"{r['scenario_key']} missing supply_planner narrative")
            # Raises if either persona's payload doesn't match the Driver/Lever/... schema
            validate_narrative_bundle(narratives)

    def test_supply_planner_never_leaks_financial_figures(self):
        cur = self.conn.cursor()
        cur.execute("SELECT scenario_key, narratives_json FROM anomalies WHERE abstained = 0")
        for r in cur.fetchall():
            n = json.loads(r["narratives_json"])
            planner_text = " ".join([
                n["supply_planner"]["headline"], n["supply_planner"]["summary"],
                n["supply_planner"]["synthesis_body"],
            ]).lower()
            for term in FORBIDDEN_FOR_PLANNER:
                self.assertNotIn(term, planner_text, f"{r['scenario_key']}: planner narrative leaked '{term}'")

    def test_vp_sales_never_leaks_warehouse_detail(self):
        cur = self.conn.cursor()
        cur.execute("SELECT scenario_key, narratives_json FROM anomalies WHERE abstained = 0")
        for r in cur.fetchall():
            n = json.loads(r["narratives_json"])
            vp_text = " ".join([
                n["vp_sales"]["headline"], n["vp_sales"]["summary"], n["vp_sales"]["synthesis_body"],
            ]).lower()
            for term in FORBIDDEN_FOR_VP:
                self.assertNotIn(term, vp_text, f"{r['scenario_key']}: vp_sales narrative leaked '{term}'")

    def test_billing_scenario_abstains_with_reason(self):
        cur = self.conn.cursor()
        cur.execute("SELECT abstained, abstention_reason FROM anomalies WHERE scenario_key = 'billing'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["abstained"], 1)
        self.assertIsNotNone(row["abstention_reason"])

    def test_sparse_scenario_does_not_abstain(self):
        cur = self.conn.cursor()
        cur.execute("SELECT abstained FROM anomalies WHERE scenario_key = 'sparse'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["abstained"], 0)


class TestServerSideEntitlements(unittest.TestCase):
    """Verifies api_server.py's masking is real -- restricted fields are actually
    removed from the payload per role, not just hidden client-side."""

    @classmethod
    def setUpClass(cls):
        cls.api_server = _load_api_server_module()

    def _sample_anomaly(self):
        return {
            "id": "ANOM-TEST", "item_id": "FOODS_3_090", "state_id": "CA",
            "actual_value": 1000.0, "baseline_value": 900.0,
            "pvm": {
                "volume": {"val": 100.0, "pct": "50%", "expl": "x"},
                "price": {"val": 0.0, "pct": "0%", "expl": "x"},
                "mix": {"val": 0.0, "pct": "0%", "expl": "x"},
                "other": {"val": 0.0, "pct": "0%", "expl": "x"},
            },
            "products": [{"sku": "FOODS_3_090", "revenueImpact": "+$100", "volumeDelta": "+5%", "status": "x"}],
            "evidence": [
                {"source": "source_marketing_weekly", "title": "t", "preview": "p", "fullText": "f"},
                {"source": "source_supply_monthly", "title": "t2", "preview": "p2", "fullText": "f2"},
            ],
            "logistics": {"title": "Warehouse stuff", "status": "s", "statusClass": "c", "desc": "d", "metrics": []},
        }

    def test_supply_planner_masking_removes_financials(self):
        masked = self.api_server._apply_entitlements(self._sample_anomaly(), "supply_planner")
        self.assertIsNone(masked["actual_value"])
        self.assertIsNone(masked["baseline_value"])
        self.assertIsNone(masked["pvm"]["volume"]["val"])
        self.assertEqual(masked["products"][0]["revenueImpact"], "RESTRICTED")
        marketing_evidence = [e for e in masked["evidence"] if e["source"] == "source_marketing_weekly"][0]
        self.assertEqual(marketing_evidence["fullText"], "RESTRICTED")
        # Supply-side evidence must remain visible for this role
        supply_evidence = [e for e in masked["evidence"] if e["source"] == "source_supply_monthly"][0]
        self.assertEqual(supply_evidence["fullText"], "f2")

    def test_vp_sales_masking_removes_logistics(self):
        masked = self.api_server._apply_entitlements(self._sample_anomaly(), "vp_sales")
        self.assertEqual(masked["item_id"], "RESTRICTED")
        self.assertEqual(masked["logistics"]["title"], "RESTRICTED")
        self.assertEqual(masked["products"][0]["sku"], "RESTRICTED")
        supply_evidence = [e for e in masked["evidence"] if e["source"] == "source_supply_monthly"][0]
        self.assertEqual(supply_evidence["fullText"], "RESTRICTED")
        # Actual financial values remain visible for this role
        self.assertEqual(masked["actual_value"], 1000.0)

    def test_vp_sales_masking_redacts_item_id_embedded_in_anomaly_id(self):
        # Regression: anomaly_id is built server-side as
        # f"ANOM-{period}-{state_id}-{item_id}" (generate_mock_data.py), so the same
        # item_id that d["item_id"] = "RESTRICTED" is masking a few lines below was
        # still leaking straight back out through the unmasked "id" field sitting next
        # to it in this same payload -- a real gap in the "actual server-side masking,
        # not client-side hiding" guarantee this module claims.
        anomaly = self._sample_anomaly()
        anomaly["id"] = "ANOM-2012-11-CA-FOODS_3_090"
        masked = self.api_server._apply_entitlements(anomaly, "vp_sales")
        self.assertNotIn("FOODS_3_090", masked["id"])
        self.assertEqual(masked["id"], "ANOM-2012-11-CA-ITEM")

    def test_supply_planner_masking_redacts_freetext_revenue_disclosure(self):
        # A support ticket is not a structured financial column, but it can still
        # narrate one (e.g. "It shows high dollar revenue in our logs") -- this must
        # be caught too, not just structured source_marketing_weekly rows.
        anomaly = self._sample_anomaly()
        anomaly["evidence"].append({
            "source": "unstructured_feedback (support ticket)",
            "title": "Overcharge complaint",
            "preview": "It shows high dollar revenue in our logs but customers want refunds.",
            "fullText": "It shows high dollar revenue in our logs but customers want refunds.",
        })
        masked = self.api_server._apply_entitlements(anomaly, "supply_planner")
        fb_evidence = [e for e in masked["evidence"] if "unstructured_feedback" in e["source"]][0]
        self.assertNotIn("revenue", fb_evidence["fullText"].lower())
        self.assertIn("redacted", fb_evidence["fullText"].lower())

    def test_graph_endpoint_masking_redacts_revenue_and_identity(self):
        # New evidence-graph subgraph shape: {nodes:[{id,kind,label,layer,...}], edges, node_count, focal}
        subgraph = {
            "focal": "revenue_anom_FOODS_3_586_TX_2013-05-16",
            "node_count": 5,
            "nodes": [
                {"id": "revenue_anom_FOODS_3_586_TX_2013-05-16", "kind": "sales_anomaly",
                 "label": "revenue · 2013-05-16", "layer": 0, "column": "revenue",
                 "value": 5100.1, "baseline_mean": 10200.0, "volume_effect": -4900.0},
                {"id": "item_FOODS_3_586", "kind": "item_entity", "label": "FOODS_3_586", "layer": 1},
                {"id": "state_TX", "kind": "state_entity", "label": "TX", "layer": 1},
                {"id": "warehouse_WH-1001", "kind": "warehouse_entity", "label": "WH-1001", "layer": 1},
                {"id": "mkt_anom_TV_TX_2013-05-13", "kind": "marketing_anomaly",
                 "label": "TV spend · 2013-05-13", "layer": 2, "value": 812.0, "region": "South"},
            ],
            "edges": [
                {"source": "revenue_anom_FOODS_3_586_TX_2013-05-16", "target": "units_anom_FOODS_3_586_TX_2013-05-16",
                 "relation": "explains", "driver": "volume", "dollar_effect": -4900.0, "weight": 1.0},
            ],
        }

        planner_masked = self.api_server._mask_graph_for_role(subgraph, "supply_planner")
        sales_node = [n for n in planner_masked["nodes"] if n["kind"] == "sales_anomaly"][0]
        self.assertIsNone(sales_node["value"])
        self.assertIsNone(sales_node["baseline_mean"])
        self.assertIsNone(sales_node["volume_effect"])
        self.assertTrue(sales_node["restricted"])
        mkt_node = [n for n in planner_masked["nodes"] if n["kind"] == "marketing_anomaly"][0]
        self.assertEqual(mkt_node["label"], "RESTRICTED")
        self.assertIsNone(planner_masked["edges"][0]["dollar_effect"])

        vp_masked = self.api_server._mask_graph_for_role(subgraph, "vp_sales")
        item_node = [n for n in vp_masked["nodes"] if n["kind"] == "item_entity"][0]
        wh_node = [n for n in vp_masked["nodes"] if n["kind"] == "warehouse_entity"][0]
        region_node = [n for n in vp_masked["nodes"] if n["kind"] == "state_entity"][0]
        self.assertEqual(item_node["label"], "RESTRICTED")
        self.assertEqual(wh_node["label"], "RESTRICTED")
        self.assertEqual(region_node["label"], "TX")  # region is not restricted for this role


if __name__ == "__main__":
    unittest.main()

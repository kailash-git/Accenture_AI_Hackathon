import unittest
import sqlite3
import os
import sys
import json

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from analytics.anomaly_detector import AnomalyDetector
from analytics.pvm_analyzer import PvmAnalyzer
from retrieval.evidence_reconciler import EvidenceReconciler

class TestAnalytics(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(BASE_DIR, 'data', 'business_bi.db')
        self.assertTrue(os.path.exists(self.db_path), f"Seeded database missing at {self.db_path}")
        
    def test_anomaly_detection_revenue(self):
        """Test that the anomaly detector runs and outputs structured lists."""
        detector = AnomalyDetector(self.db_path)
        anoms = detector.run_detection(kpi_name="Revenue", time_grain="monthly")
        self.assertIsInstance(anoms, list)
        if len(anoms) > 0:
            a = anoms[0]
            self.assertIn("kpi_name", a)
            self.assertIn("item_id", a)
            self.assertIn("state_id", a)
            self.assertIn("z_score", a)
            self.assertEqual(a["kpi_name"], "Revenue")
            
    def test_pvm_reconciliation(self):
        """Assert that price_effect + volume_effect + mix_effect + other_effect equals delta_revenue exactly."""
        pvm_analyzer = PvmAnalyzer(self.db_path)
        
        # Test case: CA, Nov 2012
        pvm_res = pvm_analyzer.analyze_variance(
            state_id="CA",
            period_start="2012-11-01",
            period_end="2012-11-30",
            time_grain="monthly"
        )
        
        volume_val = pvm_res["volume"]["val"]
        price_val = pvm_res["price"]["val"]
        mix_val = pvm_res["mix"]["val"]
        other_val = pvm_res["other"]["val"]
        
        actual_rev = pvm_res["actual_revenue"]
        baseline_rev = pvm_res["baseline_revenue"]
        
        # Reconciliation check
        sum_effects = volume_val + price_val + mix_val + other_val
        delta_rev = actual_rev - baseline_rev
        
        self.assertAlmostEqual(sum_effects, delta_rev, places=4)
        print(f"PVM reconciled exactly: sum_effects={sum_effects:.2f}, delta_rev={delta_rev:.2f} (diff={sum_effects-delta_rev})")
        
    def test_pvm_reconciliation_scoped_to_item_matches_that_items_own_delta(self):
        # Regression: analyze_variance() without item_id blends in every other SKU
        # sold in the state that month, so its price/volume/mix effects can sum to
        # a completely different number than the specific SKU's own actual-minus-
        # baseline delta -- e.g. TX/May 2013 region-wide delta is -$1,055.89 (mixes
        # in FOODS_3_090) while FOODS_3_586's own delta that month is only -$131.73.
        # Passing item_id must scope the whole decomposition to that one SKU so the
        # numbers it's narrated alongside (the anomaly's own actual/baseline) agree.
        pvm_analyzer = PvmAnalyzer(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT SUM(revenue) FROM fact_sales_daily
            WHERE item_id = 'FOODS_3_586' AND state_id = 'TX' AND strftime('%Y-%m', date) = '2013-05'
        """)
        item_actual = cur.fetchone()[0]
        conn.close()

        pvm_res = pvm_analyzer.analyze_variance(
            state_id="TX", period_start="2013-05-01", period_end="2013-05-31",
            time_grain="monthly", item_id="FOODS_3_586",
        )
        sum_effects = sum(pvm_res[k]["val"] for k in ("volume", "price", "mix", "other"))
        item_delta = pvm_res["actual_revenue"] - pvm_res["baseline_revenue"]

        self.assertAlmostEqual(sum_effects, item_delta, places=4)
        self.assertAlmostEqual(pvm_res["actual_revenue"], item_actual, places=2)
        # A single-SKU scope has nothing to shift mix share against -- mix must be ~0.
        self.assertAlmostEqual(pvm_res["mix"]["val"], 0.0, places=4)

    def test_evidence_reconciliation(self):
        """Test that evidence reconciler pulls correct unstructured and structured evidence."""
        reconciler = EvidenceReconciler(self.db_path)
        
        # Query for CA November 2012 Supply Anomaly
        res = reconciler.reconcile_evidence(
            item_id="FOODS_3_090",
            state_id="CA",
            period_start="2012-11-01",
            period_end="2012-11-30",
            anomaly_type_key="supply"
        )
        
        evidence = res["evidence"]
        self.assertGreater(len(evidence), 0)
        
        # Verify that structured supply signal is present
        supply_sig = [e for e in evidence if e["source"] == "source_supply_monthly"]
        self.assertGreater(len(supply_sig), 0)
        self.assertEqual(res["supply_indicators"]["fill_rate"], 0.78)
        self.assertEqual(res["supply_indicators"]["stockout_days"], 4)

if __name__ == "__main__":
    unittest.main()

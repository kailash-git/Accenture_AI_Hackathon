"""
tests/test_hybrid_detection.py
Regression coverage for the two-signal (STATISTICAL / EVIDENCE_DRIVEN / HYBRID)
anomaly detection pipeline: src/analytics/anomaly_detector.py,
src/analytics/evidence_signal.py, src/retrieval/evidence_reconciler.py, and the
merge logic in scripts/generate_mock_data.py:run_and_seed_anomalies().

Like tests/test_analytics.py, this runs against the real seeded database rather
than a synthetic fixture DB -- the seven scenarios below are all naturally
present in data/business_bi.db after `python scripts/generate_mock_data.py`,
so exercising them here is exercising the real production discovery pipeline,
not a mock of it.
"""

import unittest
import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from analytics.anomaly_detector import AnomalyDetector
from analytics.evidence_signal import discover_evidence_candidates, EVIDENCE_CONFIG
from retrieval.evidence_reconciler import EvidenceReconciler


class TestHybridDetection(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(BASE_DIR, 'data', 'business_bi.db')
        self.assertTrue(os.path.exists(self.db_path), f"Seeded database missing at {self.db_path}")
        self.detector = AnomalyDetector(self.db_path)
        self.reconciler = EvidenceReconciler(self.db_path)

    def _row(self, scenario_key, kpi_name="Revenue"):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM anomalies WHERE scenario_key = ? AND kpi_name = ?",
            (scenario_key, kpi_name),
        )
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row, f"Expected a seeded anomaly at scenario_key={scenario_key!r}")
        return row

    # -- Scenario 1: z >= 2 AND strong evidence -> HYBRID -----------------------
    def test_strong_statistical_plus_strong_evidence_is_hybrid(self):
        row = self._row("supply")
        self.assertGreaterEqual(abs(row["z_score"]), 2.0)
        self.assertEqual(row["detection_type"], "HYBRID")
        self.assertEqual(row["evidence_classification"], "strong")
        self.assertIsNotNone(row["evidence_score"])

    # -- Scenario 2: z >= 2, no corroborating evidence -> STATISTICAL only ------
    def test_strong_statistical_no_evidence_is_statistical_only(self):
        # Sept 2011 predates every unstructured_feedback record in the dataset,
        # so this movement can only ever be discovered by the z-score engine.
        row = self._row("gen-FOODS_3_090-2011-09-CA")
        self.assertGreaterEqual(abs(row["z_score"]), 2.0)
        self.assertEqual(row["detection_type"], "STATISTICAL")
        self.assertIsNone(row["evidence_score"])
        self.assertIsNone(row["evidence_classification"])

    # -- Scenario 3: z < 2, strong evidence -> EVIDENCE_DRIVEN (the case the ---
    # -- Round 2 spec explicitly asked to see discovered organically) -----------
    def test_sub_threshold_z_with_strong_evidence_is_evidence_driven(self):
        row = self._row("pricecut")
        self.assertLess(abs(row["z_score"]), 2.0, "pricecut must stay a real sub-threshold z-score, not be inflated to pass")
        self.assertEqual(row["detection_type"], "EVIDENCE_DRIVEN")
        self.assertEqual(row["evidence_classification"], "strong")

    # -- Scenario 4: weak/unrelated evidence never becomes a candidate ----------
    def test_unrelated_text_scores_below_the_candidate_threshold(self):
        unrelated_text = (
            "Our team had a great time at the regional trivia night on Tuesday; "
            "prizes included a gift basket and two movie tickets."
        )
        _, similarity = self.reconciler._best_category_similarity(unrelated_text)
        self.assertLess(similarity, EVIDENCE_CONFIG["min_category_similarity"],
                         "Unrelated text must not clear the relevance bar that lets a record become evidence")

    # -- Scenario 5: evidence outside the temporal window doesn't support -------
    def test_far_future_period_has_no_supporting_unstructured_evidence(self):
        # No unstructured_feedback record in the dataset falls anywhere near
        # Jan 2016 for FOODS_3_090/CA -- reconcile_evidence's +-window filter
        # must therefore return zero unstructured_feedback rows for it.
        res = self.reconciler.reconcile_evidence(
            item_id="FOODS_3_090", state_id="CA",
            period_start="2016-01-01", period_end="2016-01-31",
            anomaly_type_key="evidence-FOODS_3_090-CA-2016-01",
        )
        unstructured_hits = [e for e in res["evidence"] if str(e["source"]).startswith("unstructured_feedback")]
        self.assertEqual(unstructured_hits, [])

    # -- Scenario 6: evidence for one SKU doesn't support a different SKU -------
    def test_evidence_does_not_cross_contaminate_between_skus(self):
        # FOODS_3_090's Aug-2013 price-cut feedback is strong enough to be a
        # discovered candidate on its own SKU/state/month...
        candidates = discover_evidence_candidates(self.db_path)
        own_candidate = [c for c in candidates if c["item_id"] == "FOODS_3_090" and c["state_id"] == "CA" and c["period"] == "2013-08"]
        self.assertEqual(len(own_candidate), 1)
        self.assertEqual(own_candidate[0]["classification"], "strong")

        # ...but must not also manufacture a candidate for a different SKU in
        # the same state and month that has no feedback of its own.
        cross_sku_candidate = [c for c in candidates if c["item_id"] == "FOODS_3_586" and c["state_id"] == "CA" and c["period"] == "2013-08"]
        self.assertEqual(cross_sku_candidate, [])

    # -- Scenario 7: more independent supporting records raises the score -------
    def test_more_supporting_records_raises_evidence_score(self):
        candidates = {
            (c["item_id"], c["state_id"], c["period"]): c
            for c in discover_evidence_candidates(self.db_path)
        }
        one_record = candidates[("FOODS_3_090", "CA", "2012-10")]
        two_records = candidates[("FOODS_3_090", "CA", "2012-11")]
        self.assertEqual(len(one_record["supporting_records"]), 1)
        self.assertEqual(len(two_records["supporting_records"]), 2)
        self.assertGreater(two_records["evidence_score"], one_record["evidence_score"],
                            "A second independent corroborating record should raise, not ignore, the evidence score")

    # -- Cross-cutting: the statistical detector's own threshold is untouched ---
    def test_statistical_threshold_is_never_lowered_to_admit_evidence_cases(self):
        # run_detection's own admission threshold must still be exactly 2.0 --
        # evidence-driven discovery is an independent second signal, not a
        # backdoor that quietly loosens the z-score engine itself.
        anoms = self.detector.run_detection(kpi_name="Revenue", time_grain="monthly", threshold=2.0)
        for a in anoms:
            self.assertGreaterEqual(abs(a["z_score"]), 2.0)


if __name__ == "__main__":
    unittest.main()

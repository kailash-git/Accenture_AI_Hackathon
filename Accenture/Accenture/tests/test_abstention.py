import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from llm.abstention import evaluate_abstention  # noqa: E402


class TestAbstention(unittest.TestCase):
    def test_low_confidence_triggers_abstention(self):
        result = evaluate_abstention(confidence=30.0, evidence_list=[], deviation_pct=0.0, direction="UP")
        self.assertTrue(result["should_abstain"])

    def test_clean_high_confidence_case_does_not_abstain(self):
        evidence = [{
            "source": "source_supply_monthly", "similarityTier": "high",
            "fullText": "Fill rate dropped due to a warehouse delay.",
        }]
        result = evaluate_abstention(confidence=95.0, evidence_list=evidence, deviation_pct=-0.3, direction="DOWN")
        self.assertFalse(result["should_abstain"])

    def test_conflicting_evidence_triggers_abstention(self):
        evidence = [
            {"source": "unstructured_feedback (customer review)", "similarityTier": "high",
             "fullText": "Billing bug overcharged customers, please refund."},
            {"source": "unstructured_feedback (support ticket)", "similarityTier": "medium",
             "fullText": "Multiple complaints about pricing errors and overcharge."},
        ]
        result = evaluate_abstention(confidence=90.0, evidence_list=evidence, deviation_pct=0.05, direction="UP")
        self.assertTrue(result["should_abstain"])
        self.assertIn("Contradictory", result["reason"])

    def test_price_effect_positive_overrides_negative_overall_direction(self):
        # Overall revenue is down (unrelated volume noise), but the price effect
        # specifically is positive (e.g. a billing overcharge) while customers
        # report the opposite experience -- must still be flagged as contradictory.
        evidence = [{
            "source": "unstructured_feedback (customer review)", "similarityTier": "high",
            "fullText": "Billing bug overcharge complaint, error in checkout.",
        }]
        result = evaluate_abstention(
            confidence=90.0, evidence_list=evidence, deviation_pct=-0.01, direction="DOWN", price_effect=500.0,
        )
        self.assertTrue(result["should_abstain"])

    def test_insufficient_evidence_for_material_movement_abstains(self):
        result = evaluate_abstention(confidence=55.0, evidence_list=[], deviation_pct=0.25, direction="UP")
        self.assertTrue(result["should_abstain"])
        self.assertIn("Insufficient evidence", result["reason"])

    def test_low_z_score_movement_with_no_evidence_does_not_force_abstention_via_insufficiency(self):
        # A movement that is not itself statistically material (|z| < 2, i.e. it would
        # not have cleared the detector's own anomaly threshold) should NOT trip the
        # insufficient-evidence path just because evidence happens to be absent.
        result = evaluate_abstention(
            confidence=90.0, evidence_list=[], deviation_pct=0.01, direction="UP", z_score=0.5,
        )
        self.assertFalse(result["should_abstain"])

    def test_small_percent_move_with_extreme_z_score_and_no_evidence_abstains(self):
        # Regression for a real bug: gating materiality on raw deviation_pct (instead of
        # z_score) let a -3.8% revenue move slip through non-abstained, because 3.8% was
        # below the old 5% materiality gate -- even though it was a z=-7.5 statistical
        # outlier (an extreme deviation against a near-flat, low-variance baseline) with
        # only "low"-tier boilerplate evidence attached. Materiality must be judged by
        # how anomalous the movement is relative to its own baseline, not by its raw
        # percent size, since those two disagree exactly in low-variance regimes.
        evidence = [
            {"source": "source_supply_monthly", "similarityTier": "low", "similarity": 0.2,
             "fullText": "Fill rate dropped to 0.9826 at WH-1000"},
            {"source": "source_marketing_weekly", "similarityTier": "low", "similarity": 0.2,
             "fullText": "Total marketing spend: $13,332.93 in West Region"},
        ]
        result = evaluate_abstention(
            confidence=95.0, evidence_list=evidence, deviation_pct=-0.0378, direction="DOWN", z_score=-7.51,
        )
        self.assertTrue(result["should_abstain"])
        self.assertIn("Insufficient evidence", result["reason"])

    def test_high_confidence_material_movement_with_only_boilerplate_evidence_abstains(self):
        # Regression for a real bug: evidence_reconciler always appends low-relevance
        # structured rows (whatever that month's marketing spend/fill rate happened to
        # be), so `evidence_list` is essentially never empty in practice -- and a huge
        # swing produces a huge z-score, which drove `confidence` well above the old
        # 60% gate. Together those meant the worst case (large, high-confidence, totally
        # unexplained movement) could never trigger insufficient-evidence abstention.
        evidence = [
            {"source": "source_marketing_weekly", "similarityTier": "low", "similarity": 0.3,
             "fullText": "Total marketing spend: $11,402.50 in South Region"},
            {"source": "source_supply_monthly", "similarityTier": "low", "similarity": 0.2,
             "fullText": "Fill rate dropped to 0.9902 at WH-1000"},
        ]
        result = evaluate_abstention(confidence=95.0, evidence_list=evidence, deviation_pct=-0.9998, direction="DOWN")
        self.assertTrue(result["should_abstain"])
        self.assertIn("Insufficient evidence", result["reason"])


if __name__ == "__main__":
    unittest.main()

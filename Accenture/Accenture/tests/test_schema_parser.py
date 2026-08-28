import os
import sys
import unittest

from pydantic import ValidationError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'src'))

from llm.schema_parser import (  # noqa: E402
    ActionRecommendation, PersonaNarrative, NarrativeBundle, validate_action,
)


class TestSchemaParser(unittest.TestCase):
    VALID_ACTION = {
        "driver": "Supply-constrained volume contraction",
        "controllable_lever": "Inventory replenishment prioritization",
        "action": "Authorize emergency replenishment allocation.",
        "expected_impact": "Recover approximately $9,000 in run-rate revenue.",
        "owner": "VP of Retail Sales",
        "confidence": 95.0,
        "monitoring_plan": "Track weekly revenue against baseline for 4 weeks.",
    }

    def test_valid_action_recommendation_parses(self):
        action = validate_action(self.VALID_ACTION)
        self.assertEqual(action.owner, "VP of Retail Sales")
        self.assertEqual(action.confidence, 95.0)

    def test_missing_field_raises(self):
        bad = dict(self.VALID_ACTION)
        del bad["monitoring_plan"]
        with self.assertRaises(ValidationError):
            ActionRecommendation(**bad)

    def test_blank_field_raises(self):
        bad = dict(self.VALID_ACTION)
        bad["owner"] = "   "
        with self.assertRaises(ValidationError):
            ActionRecommendation(**bad)

    def test_confidence_out_of_range_raises(self):
        bad = dict(self.VALID_ACTION)
        bad["confidence"] = 150.0
        with self.assertRaises(ValidationError):
            ActionRecommendation(**bad)

    def test_persona_narrative_requires_exactly_one_outcome(self):
        base = {
            "persona": "vp_sales", "headline": "headline text", "summary": "summary text",
            "synthesis_title": "title text", "synthesis_body": "body text",
        }
        # Neither action nor abstention set -> invalid
        with self.assertRaises(ValidationError):
            PersonaNarrative(**base)

        # Both set -> invalid
        with self.assertRaises(ValidationError):
            PersonaNarrative(
                **base,
                recommended_action=self.VALID_ACTION,
                abstention={"reason": "x", "confidence": 10.0},
            )

        # Exactly one set -> valid
        pn = PersonaNarrative(**base, recommended_action=self.VALID_ACTION)
        self.assertIsNotNone(pn.recommended_action)
        self.assertIsNone(pn.abstention)

    def test_narrative_bundle_requires_both_personas(self):
        with self.assertRaises(ValidationError):
            NarrativeBundle(vp_sales={
                "persona": "vp_sales", "headline": "h", "summary": "s",
                "synthesis_title": "t", "synthesis_body": "b",
                "recommended_action": self.VALID_ACTION,
            })


if __name__ == "__main__":
    unittest.main()

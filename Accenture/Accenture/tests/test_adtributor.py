"""
Tests for the multi-dimensional slice attribution layer (Adtributor
reimplementation) -- correctness of the port, independent of the experiment.
"""
import os
import sys
import unittest
import warnings

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(BASE_DIR, "src"))

from analytics.adtributor import (
    attribute, is_material, run_attribution, _js_surprise, _MEASURE_COLS,
)

DB_PATH = os.path.join(BASE_DIR, "data", "business_bi.db")


def _frame(rows):
    """rows: list of (item, region, cat, f_rev, a_rev). cogs/units proportional."""
    df = pd.DataFrame(rows, columns=["item_id", "state_id", "cat_id", "f_revenue", "a_revenue"])
    df["f_cost_of_goods_sold"] = df["f_revenue"] * 0.7
    df["a_cost_of_goods_sold"] = df["a_revenue"] * 0.7
    df["f_units"] = df["f_revenue"] / 5.0
    df["a_units"] = df["a_revenue"] / 5.0
    return df


class TestAdtributor(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")

    def test_js_surprise_zero_when_unchanged(self):
        self.assertAlmostEqual(_js_surprise(0.3, 0.3), 0.0, places=9)
        self.assertGreater(_js_surprise(0.3, 0.05), 0.0)

    def test_picks_the_dimension_that_shifted(self):
        # region R0 revenue collapses; item/cat distributions unchanged.
        rows = []
        for it in ("I0", "I1", "I2"):
            for rg in ("R0", "R1"):
                base = 100.0
                a = base * (0.3 if rg == "R0" else 1.0)
                rows.append((it, rg, "C0", base, a))
        cands = attribute(_frame(rows), ["item_id", "state_id", "cat_id"], "Revenue")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["dimension"], "state_id")
        self.assertEqual(set(cands[0]["elements"]), {"R0"})

    def test_not_fooled_by_a_large_but_proportional_slice(self):
        # I_BIG is 5x everyone else and drops with the uniform 20% portfolio
        # drift (huge raw |delta|), but a small item I_S drops disproportionately.
        rows = [("I_BIG", "R0", "C0", 500.0, 400.0),   # -100, share ~unchanged
                ("I_S",  "R0", "C0", 50.0, 15.0),      # -35, share collapses
                ("I_A",  "R0", "C0", 60.0, 48.0),
                ("I_B",  "R0", "C0", 60.0, 48.0)]
        cands = attribute(_frame(rows), ["item_id"], "Revenue")
        self.assertTrue(cands)
        self.assertIn("I_S", cands[0]["elements"])
        self.assertNotIn("I_BIG", cands[0]["elements"])

    def test_abstains_when_not_material(self):
        rows = [(f"I{i}", "R0", "C0", 100.0, 100.5 + (i % 3)) for i in range(6)]
        self.assertFalse(is_material(_frame(rows), "Revenue"))
        self.assertEqual(attribute(_frame(rows), ["item_id"], "Revenue"), [])

    @unittest.skipUnless(os.path.exists(DB_PATH), "seeded DB required")
    def test_run_attribution_on_real_anomaly(self):
        r = run_attribution(DB_PATH, "Revenue", "2012-11-01", "2012-11-30",
                            item_id="FOODS_3_090", state_id="CA")
        self.assertIn("available", r)
        self.assertEqual(r["method"].split(" ")[0], "Adtributor")
        # InventoryTurnover is not a supported measure -> graceful decline
        r2 = run_attribution(DB_PATH, "InventoryTurnover", "2013-05-01", "2013-05-31")
        self.assertFalse(r2["available"])


if __name__ == "__main__":
    unittest.main()

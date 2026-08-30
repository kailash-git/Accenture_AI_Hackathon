"""
Regression tests for the deterministic role masking of chat replies
(api_server._mask_chat_reply). The chat system prompt asks the model to
withhold restricted fields; this filter enforces it on the way out.
"""
import importlib.util
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))


def _load_api_server():
    spec = importlib.util.spec_from_file_location("api_server", os.path.join(ROOT_DIR, "api_server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


API = _load_api_server()
mask = API._mask_chat_reply
MARKER = API._CHAT_REDACTION_MARKER


class TestChatReplyMasking(unittest.TestCase):

    def test_vp_sales_fill_rate_is_redacted(self):
        txt = ("Volume fell 28% versus baseline. The West's fill rate fell to 78%, "
               "leaving shelves empty. A smaller price effect also hurt.")
        out, hits = mask(txt, "vp_sales")
        self.assertIn("fill rate", hits)
        self.assertNotIn("fill rate", out.lower())
        self.assertIn("price effect", out)          # entitled content survives

    def test_vp_sales_warehouse_and_carrier_redacted(self):
        txt = ("The supply issue was tied to the Seattle warehouse, with carrier "
               "LogiTrans as the bottleneck.")
        out, hits = mask(txt, "vp_sales")
        self.assertTrue({"warehouse", "carrier"} & set(hits))
        self.assertNotIn("warehouse", out.lower())
        self.assertNotIn("logitrans", out.lower())

    def test_supply_planner_revenue_and_margin_redacted(self):
        txt = ("Revenue in California fell 33.7%. Gross-margin percent dropped 16.3%. "
               "Fill rate fell to 0.78 with four stock-out days.")
        out, hits = mask(txt, "supply_planner")
        self.assertTrue({"revenue", "gross-margin"} & set(hits))
        self.assertNotIn("revenue", out.lower())
        self.assertNotIn("margin", out.lower())
        self.assertIn("stock-out days", out)        # entitled content survives

    def test_entitled_content_is_not_touched(self):
        # vp_sales IS entitled to revenue / volume / price / mix
        txt = "Revenue fell 33.7%, from $14.4K to $9.6K, driven by a 28% volume drop."
        out, hits = mask(txt, "vp_sales")
        self.assertEqual(hits, [])
        self.assertEqual(out, txt)
        # supply_planner IS entitled to fill rate / stock-outs / units
        txt2 = "Fill rate fell to 0.78 at the warehouse with four stock-out days."
        out2, hits2 = mask(txt2, "supply_planner")
        self.assertEqual(hits2, [])

    def test_refusal_sentence_is_kept_even_if_it_names_the_term(self):
        txt = "The specific SKU and warehouse are restricted for your role, so I can't share them."
        out, hits = mask(txt, "vp_sales")
        self.assertEqual(hits, [])
        self.assertEqual(out, txt)

    def test_admin_is_unmasked(self):
        txt = "Fill rate fell to 0.78; revenue dropped 33.7%; gross margin fell."
        out, hits = mask(txt, "admin")
        self.assertEqual(hits, [])
        self.assertEqual(out, txt)

    def test_fully_gutted_reply_falls_back_to_one_line(self):
        txt = "The warehouse fill rate was 0.78. Stockout lasted four days at that warehouse."
        out, hits = mask(txt, "vp_sales")
        self.assertTrue(hits)
        self.assertNotIn(MARKER, out)               # not a wall of markers
        self.assertIn("restricted for your role", out)


if __name__ == "__main__":
    unittest.main()

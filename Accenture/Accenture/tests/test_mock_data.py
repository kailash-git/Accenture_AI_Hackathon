import unittest
import sqlite3
import os

class TestMockDataSeeding(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(self.base_dir, 'data', 'business_bi.db')
        self.assertTrue(os.path.exists(self.db_path), f"Seeded database missing at {self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_tables_exist_and_populated(self):
        """Assert that all standard tables exist and contain records."""
        tables = ['sku_lookup', 'fact_sales_daily', 'source_marketing_weekly', 'source_supply_monthly', 'unstructured_feedback']
        for table in tables:
            self.cursor.execute(f"SELECT COUNT(*) FROM {table};")
            count = self.cursor.fetchone()[0]
            self.assertGreater(count, 0, f"Table {table} is empty")
            print(f"Table {table} has {count} rows - Passed")

    def test_cogs_margin_math(self):
        """Assert that cost_of_goods_sold and gross_margin_percent are calculated correctly in fact_sales_daily."""
        # Join sales and sku_lookup to verify COGS math
        query = """
        SELECT s.units, sl.supplier_raw_cost, s.revenue, s.cost_of_goods_sold, s.gross_margin_percent
        FROM fact_sales_daily s
        JOIN sku_lookup sl ON s.item_id = sl.item_id
        WHERE s.units > 0
        LIMIT 20;
        """
        self.cursor.execute(query)
        rows = self.cursor.fetchall()
        self.assertGreater(len(rows), 0, "No transactions found with units > 0")

        for row in rows:
            units, cost, rev, cogs, margin = row
            # Assert COGS = units * supplier_raw_cost
            self.assertAlmostEqual(cogs, units * cost, places=4)
            # Assert Margin = (Revenue - COGS) / Revenue
            if rev > 0:
                expected_margin = (rev - cogs) / rev
                self.assertAlmostEqual(margin, expected_margin, places=4)
            else:
                self.assertEqual(margin, 0.0)

    def test_injected_november_supply_anomaly(self):
        """Assert that the CA/FOODS_3_090 November 2012 supply fill_rate constraint is present."""
        query = """
        SELECT fill_rate, stockout_days 
        FROM source_supply_monthly 
        WHERE warehouse_sku = 'WH-1000' AND state_id = 'CA' AND month = '2012-11';
        """
        self.cursor.execute(query)
        row = self.cursor.fetchone()
        self.assertIsNotNone(row, "November 2012 CA supply anomaly record missing")
        fill_rate, stockout_days = row
        self.assertEqual(fill_rate, 0.78, "Injected fill rate is incorrect")
        self.assertEqual(stockout_days, 4, "Injected stockout days is incorrect")

    def test_all_three_kpis_have_their_own_undisturbed_anomaly_rows(self):
        """
        Regression: anomalies.anomaly_id (PRIMARY KEY) used to be built from just
        (period, state_id, item_id) with no KPI in it. Once GrossMarginPercent/
        InventoryTurnover detection was added alongside Revenue, any KPI pair sharing
        an (item, state, period) combination collided on that key -- "INSERT OR
        REPLACE" then silently overwrote one KPI's anomaly row with another's,
        dropping the real Revenue anomaly count from 20 to 6 the first time this ran.
        """
        self.cursor.execute("SELECT kpi_name, COUNT(*) FROM anomalies GROUP BY kpi_name")
        counts = dict(self.cursor.fetchall())
        self.assertIn("Revenue", counts)
        self.assertIn("GrossMarginPercent", counts)
        self.assertIn("InventoryTurnover", counts)
        # All three KPIs were run over the same 3-item/2-state history, so none
        # should be starved down to a handful of rows by a collision with another KPI.
        for kpi, count in counts.items():
            self.assertGreater(count, 3, f"{kpi} has suspiciously few anomalies ({count}) -- possible anomaly_id collision")

        self.cursor.execute("SELECT COUNT(*), COUNT(DISTINCT anomaly_id) FROM anomalies")
        total, distinct = self.cursor.fetchone()
        self.assertEqual(total, distinct, "Duplicate anomaly_id values found -- PRIMARY KEY collisions occurred")

    def test_unstructured_feedback_seeding(self):
        """Assert that unstructured customer support reviews and tickets are loaded."""
        self.cursor.execute("SELECT COUNT(*) FROM unstructured_feedback WHERE source = 'customer review';")
        review_count = self.cursor.fetchone()[0]
        self.assertGreaterEqual(review_count, 2, "Customer reviews not seeded correctly")

        self.cursor.execute("SELECT COUNT(*) FROM unstructured_feedback WHERE source = 'support ticket';")
        ticket_count = self.cursor.fetchone()[0]
        self.assertGreaterEqual(ticket_count, 2, "Support tickets not seeded correctly")

if __name__ == "__main__":
    unittest.main()

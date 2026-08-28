import unittest
import json
import sqlite3
import os

class TestSchemas(unittest.TestCase):
    def setUp(self):
        # Paths
        self.schema_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schemas')
        self.db_init_path = os.path.join(self.schema_dir, 'db_init.sql')
        self.semantic_contract_path = os.path.join(self.schema_dir, 'semantic_contract.json')

    def test_semantic_contract_valid_json(self):
        """Assert that semantic_contract.json is a valid JSON file and contains required keys."""
        self.assertTrue(os.path.exists(self.semantic_contract_path), "semantic_contract.json is missing")
        with open(self.semantic_contract_path, 'r') as f:
            data = json.load(f)
        
        self.assertIn("project", data)
        self.assertIn("semantic_layer", data)
        self.assertIn("kpis", data["semantic_layer"])
        self.assertIn("mappings", data["semantic_layer"])
        self.assertIn("entitlements", data["semantic_layer"])

    def test_db_init_sql_compiles(self):
        """Assert that db_init.sql executes successfully in SQLite to build tables."""
        self.assertTrue(os.path.exists(self.db_init_path), "db_init.sql is missing")
        
        with open(self.db_init_path, 'r') as f:
            sql_script = f.read()

        # Connect to a transient in-memory SQLite database
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()

        try:
            cursor.executescript(sql_script)
            conn.commit()
        except sqlite3.Error as e:
            self.fail(f"SQLite execution failed: {e}")
        finally:
            conn.close()

if __name__ == "__main__":
    unittest.main()

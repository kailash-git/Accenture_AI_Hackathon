# Implementation Plan - Day 1 & Day 2: Semantic Layer, Database Setup & Mock Data Seeding

Establish the relational database schema, semantic layer contract, database seed pipeline, and verification tests.

## User Review Required

> [!IMPORTANT]
> - **Seeding Logic**: `scripts/generate_mock_data.py` reads the 4 `.parquet` data files you copied to `data/` and populates the SQLite database (`data/business_bi.db`).
> - **Cost & Margin Derivation**: Standard unit costs (`supplier_raw_cost`) are mapped programmatically (`FOODS_3_090 = 0.88`, `FOODS_3_586 = 1.18`, `HOUSEHOLD_1_020 = 3.49`) to calculate `cost_of_goods_sold` and `gross_margin_percent` for every transaction.
> - **Unstructured Context**: We inject text-based support tickets and customer reviews directly into `unstructured_feedback` mapping to the supply and pricing anomaly dates.

## Proposed Changes

We have created/populated the following files in the repository:

### Schemas & Contracts
*   [`schemas/db_init.sql`](file:///C:/remember/Accenture/Accenture/schemas/db_init.sql): SQLite schema containing structured sales, weekly marketing, monthly supply tables, unstructured feedback, and a user rating feedback table.
*   [`schemas/semantic_contract.json`](file:///C:/remember/Accenture/Accenture/schemas/semantic_contract.json): The semantic layer containing definitions for `Revenue`, `GrossMarginPercent`, and `InventoryTurnover`, plus role column masking specifications for `vp_sales` and `supply_planner`.

### Data Seeding Scripts
*   [`scripts/generate_mock_data.py`](file:///C:/remember/Accenture/Accenture/scripts/generate_mock_data.py): The main seed pipeline that reads parquet data files, executes the reconciliation logic, derives margins, and inserts structured and unstructured data into SQLite.

### Automated Tests
*   [`tests/test_schemas.py`](file:///C:/remember/Accenture/Accenture/tests/test_schemas.py): Asserts JSON validity of the contract and schema compilation in an in-memory database.
*   [`tests/test_mock_data.py`](file:///C:/remember/Accenture/Accenture/tests/test_mock_data.py): Verifies seeded row counts, confirms mathematical calculations of COGS and margins, and checks the presence of the November 2012 supply anomaly.

---

## Verification Plan

Once you copy the 4 parquet files to the local `data/` folder, run the following steps:

### 1. Run the Seeding Script
Populate your SQLite database:
```powershell
python scripts/generate_mock_data.py
```

### 2. Run the Verification Tests
Run the test suites to verify that the database math, structural schemas, and anomalies are 100% correct:
```powershell
python -m unittest tests/test_schemas.py
python -m unittest tests/test_mock_data.py
```

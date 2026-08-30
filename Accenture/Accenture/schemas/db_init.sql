-- db_init.sql
-- Relational database structure for BusinessIntelligence.ai KPI Engine
PRAGMA foreign_keys = ON;

-- 1. Product SKU Lookup & Supplier Cost Table
CREATE TABLE IF NOT EXISTS sku_lookup (
    item_id TEXT PRIMARY KEY,
    warehouse_sku TEXT NOT NULL,
    supplier_raw_cost REAL NOT NULL
);

-- 2. Daily Sales Fact Table
CREATE TABLE IF NOT EXISTS fact_sales_daily (
    date TEXT NOT NULL,
    item_id TEXT NOT NULL,
    dept_id TEXT NOT NULL,
    cat_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    d TEXT NOT NULL,
    units INTEGER NOT NULL,
    wm_yr_wk INTEGER NOT NULL,
    event_name_1 TEXT,
    event_type_1 TEXT,
    snap_CA INTEGER,
    snap_TX INTEGER,
    snap_WI INTEGER,
    sell_price REAL NOT NULL,
    price_source_grain TEXT NOT NULL,
    price_is_imputed INTEGER NOT NULL,
    revenue REAL NOT NULL,
    cost_of_goods_sold REAL NOT NULL,
    gross_margin_percent REAL NOT NULL,
    PRIMARY KEY (date, item_id, store_id),
    FOREIGN KEY(item_id) REFERENCES sku_lookup(item_id)
);

-- 3. Weekly Marketing Spend Table
CREATE TABLE IF NOT EXISTS source_marketing_weekly (
    week_start_monday TEXT NOT NULL,
    region_name TEXT NOT NULL,
    channel TEXT NOT NULL,
    marketing_spend REAL NOT NULL,
    PRIMARY KEY (week_start_monday, region_name, channel)
);

-- 4. Monthly Supply Metrics Table
CREATE TABLE IF NOT EXISTS source_supply_monthly (
    warehouse_sku TEXT NOT NULL,
    state_id TEXT NOT NULL,
    month TEXT NOT NULL, -- Format: YYYY-MM
    fill_rate REAL NOT NULL,
    stockout_days INTEGER NOT NULL,
    PRIMARY KEY (warehouse_sku, state_id, month),
    FOREIGN KEY(warehouse_sku) REFERENCES sku_lookup(warehouse_sku)
);

-- 5. Unstructured Customer Feedback & Support Tickets Table
CREATE TABLE IF NOT EXISTS unstructured_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    source TEXT NOT NULL, -- 'customer review', 'support ticket', 'carrier email'
    text_content TEXT NOT NULL,
    date TEXT NOT NULL, -- YYYY-MM-DD
    FOREIGN KEY(item_id) REFERENCES sku_lookup(item_id)
);

-- 6. User Feedback Loop Table
CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_id TEXT NOT NULL,
    rating INTEGER NOT NULL, -- 1 for Thumbs Up, -1 for Thumbs Down
    user_comments TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 6b. Expert Action Corrections -- when a user judges the recommended action
-- wrong and types what to do instead. Matched onto future similar anomalies
-- (same scenario_key, or same kpi_name + cat_id + direction) so the engine
-- surfaces the corrected action next time. This is the "learning loop".
CREATE TABLE IF NOT EXISTS action_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_id TEXT NOT NULL,
    scenario_key TEXT,
    kpi_name TEXT,
    cat_id TEXT,
    direction TEXT,
    detection_type TEXT,
    original_action TEXT,
    corrected_action TEXT NOT NULL,
    rationale TEXT,
    corrected_by TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- 'active' | 'dismissed'
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 7. Daily Inventory Logs Table
CREATE TABLE IF NOT EXISTS inventory_logs (
    date TEXT NOT NULL,
    item_id TEXT NOT NULL,
    warehouse_name TEXT NOT NULL,
    state_id TEXT NOT NULL,
    inventory_on_hand INTEGER NOT NULL,
    PRIMARY KEY (date, item_id, warehouse_name),
    FOREIGN KEY(item_id) REFERENCES sku_lookup(item_id)
);

-- 8. Detected Anomalies Table (Backend Output Store)
CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id TEXT PRIMARY KEY,
    detected_at TEXT NOT NULL,
    kpi_name TEXT NOT NULL,
    item_id TEXT,
    state_id TEXT,
    cat_id TEXT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    actual_value REAL NOT NULL,
    baseline_value REAL NOT NULL,
    deviation_pct REAL NOT NULL,
    z_score REAL NOT NULL,
    direction TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    pvm_json TEXT NOT NULL,         -- JSON serialized PVM details
    products_json TEXT NOT NULL,    -- JSON serialized product breakdown
    evidence_json TEXT NOT NULL,    -- JSON serialized supporting evidence list
    recommended_action_json TEXT NOT NULL, -- JSON serialized recommendation details (vp_sales persona, back-compat default)
    synthesis_json TEXT NOT NULL,   -- JSON serialized business explanation (vp_sales persona, back-compat default)
    logistics_json TEXT NOT NULL,    -- JSON serialized warehouse/logistics cards
    scenario_key TEXT NOT NULL,      -- Unique scenario key matching frontend router (supply, billing, pricecut, sparse, or gen-*)
    narratives_json TEXT NOT NULL DEFAULT '{}',   -- JSON: {"vp_sales": PersonaNarrative, "supply_planner": PersonaNarrative}
    abstained INTEGER NOT NULL DEFAULT 0,          -- 1 if the engine abstained from a recommendation for this anomaly
    abstention_reason TEXT,                        -- Structured abstention reason, if abstained
    graph_context_json TEXT NOT NULL DEFAULT '{}', -- JSON: knowledge-graph traversal result backing this anomaly
    generation_telemetry_json TEXT NOT NULL DEFAULT '{}', -- JSON: {tokens_in, tokens_out, cost_usd, latency_s, model, calls, generation_method}
    detection_type TEXT NOT NULL DEFAULT 'STATISTICAL', -- STATISTICAL | EVIDENCE_DRIVEN | HYBRID | SPARSE_HISTORY (src/analytics/evidence_signal.py)
    evidence_score REAL,             -- 0-1 evidence-signal score (NULL for pure STATISTICAL/SPARSE_HISTORY anomalies)
    evidence_classification TEXT     -- 'strong' | 'moderate' | NULL (src/analytics/evidence_signal.py EVIDENCE_CONFIG thresholds)
);

-- 9. Aggregate Run Telemetry (real, measured -- not fabricated)
CREATE TABLE IF NOT EXISTS telemetry_summary (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    anomalies_processed INTEGER NOT NULL,
    abstained_count INTEGER NOT NULL,
    llm_calls INTEGER NOT NULL,
    llm_generated_count INTEGER NOT NULL,
    deterministic_generated_count INTEGER NOT NULL,
    total_tokens_in INTEGER NOT NULL,
    total_tokens_out INTEGER NOT NULL,
    total_cost_usd REAL NOT NULL,
    total_pipeline_seconds REAL NOT NULL,
    avg_sql_query_ms REAL NOT NULL
);


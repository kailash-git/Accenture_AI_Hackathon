"""
Step 2b: Synthetic supply/inventory source. MONTHLY grain (the slowest
cadence of the three sources, forward-filled with a staleness flag when
joined downstream).

METHOD LABEL: pure simulation / rule-based generation, no LLM, no stats yet.

Deliberate real-world mismatches baked in (per REQ-02):
  - Keyed by an internal SKU code, not item_id -> forces a real lookup join
    via a separate mapping table, exactly like a real warehouse system would
    key differently from a POS system
  - Monthly grain vs sales' daily and marketing's weekly -> the coarsest
    cadence, tests forward-fill + staleness tagging harder than marketing did
  - One deliberate real supply constraint injected (fill rate drop, stockout
    days) as one leg of the multi-factor scripted scenario built later
"""
import pandas as pd
import numpy as np

DATA = "/home/claude/kpi_engine/data"
fact = pd.read_parquet(f"{DATA}/fact_sales_daily.parquet")

rng = np.random.default_rng(7)

# --- SKU lookup table: item_id -> internal warehouse SKU code (mismatch) ---
items = sorted(fact.item_id.unique())
sku_lookup = pd.DataFrame({
    "item_id": items,
    "warehouse_sku": [f"WH-{1000+i}" for i in range(len(items))],
})
sku_lookup.to_parquet(f"{DATA}/lookup_sku_to_item.parquet", index=False)
print("SKU lookup table:")
print(sku_lookup)

states = sorted(fact.state_id.unique())
months = pd.period_range(fact.date.min(), fact.date.max(), freq="M")

rows = []
for _, r in sku_lookup.iterrows():
    for state in states:
        for m in months:
            fill_rate = np.clip(rng.normal(0.985, 0.01), 0.90, 1.0)
            stockout_days = 0
            rows.append({
                "warehouse_sku": r.warehouse_sku,
                "state_id": state,
                "month": m,
                "fill_rate": round(fill_rate, 4),
                "stockout_days": stockout_days,
            })

supply = pd.DataFrame(rows)

# --- inject a real supply constraint for the multi-factor scenario ---
# FOODS_3_090 in CA, in a specific month, fill rate drops and stockouts occur
target_sku = sku_lookup.loc[sku_lookup.item_id == "FOODS_3_090", "warehouse_sku"].iloc[0]
target_month = pd.Period("2012-11", freq="M")  # anchors on the real Nov 2012 flagged week
mask = (supply.warehouse_sku == target_sku) & (supply.state_id == "CA") & (supply.month == target_month)
supply.loc[mask, "fill_rate"] = 0.78
supply.loc[mask, "stockout_days"] = 4
print(f"\nInjected supply constraint: {target_sku} (FOODS_3_090), CA, {target_month} "
      f"-> fill_rate=0.78, stockout_days=4")

supply.to_parquet(f"{DATA}/source_supply_monthly.parquet", index=False)
print(f"\nSaved source_supply_monthly.parquet: {len(supply)} rows")

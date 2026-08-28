# Dataset: KPI Intelligence-to-Action Engine

## What this is

A hybrid dataset: one real source (Walmart sales and price, from the M5
forecasting competition) reconciled with two synthetic companion sources
(marketing spend, supply and fill rate), built to satisfy the prototype's
requirement for 3 heterogeneous sources at different grains, cadences, and
naming conventions.

Real data is used where it matters most: authentic noise, seasonality, and
one genuine grain mismatch (daily sales vs. weekly price) that didn't need
to be invented. Synthetic data fills in what M5 doesn't contain at all,
marketing spend and supply/inventory, sized against the real backbone's
measured volatility so the two feel consistent with each other.

## The 3 source files (map directly to REQ-02's heterogeneous sources)

| File | Grain | Real or synthetic | Key |
|---|---|---|---|
| `fact_sales_daily.parquet` | Daily | **Real** (M5/Walmart) | item_id, store_id, state_id, date |
| `source_marketing_weekly.parquet` | Weekly (Mon start) | Synthetic | region_name, channel, week_start_monday |
| `source_supply_monthly.parquet` | Monthly | Synthetic | warehouse_sku, state_id, month |

## Supporting files (not sources, generated to join or analyze the sources above)

| File | What it is | Why it exists |
|---|---|---|
| `lookup_sku_to_item.parquet` | item_id <-> warehouse_sku mapping | Supply is deliberately keyed by an internal SKU code, not item_id, forcing a real lookup join instead of a naive merge |
| `materiality_FOODS_3_090_CA.parquet` | Full weekly anomaly scores | Output of the REQ-01 detector run on the sales source, not an input |
| `final_flagged_weeks_FOODS_3_090_CA.parquet` | Final filtered alert list (25 weeks) | Same, after the business materiality gate is applied |

## How each source was generated

### 1. `fact_sales_daily.parquet` (real, reconciled)

Source: M5 Forecasting Accuracy dataset (Walmart), pulled from a public
GitHub mirror of the Kaggle competition files (`sales_train_validation.csv`,
`calendar.csv`, `sell_prices.csv`).

Scope: 3 items x 2 states, chosen deliberately:
- **FOODS_3_090** (CA/TX): established, high-volume, has a real ~25% price
  cut in Aug 2013, used as a non-scripted decomposition test case
- **FOODS_3_586** (CA/TX): same department as FOODS_3_090, added so a real
  **mix effect** is computable (mix requires >=2 items in a category, one
  item alone can't demonstrate a mix shift)
- **HOUSEHOLD_1_020** (CA/TX): genuinely short real history, as little as
  198 days in TX_1 vs. 1,913 for the mature items, used as the sparse-history
  scenario

Build steps (all deterministic, no LLM, no stats):
1. Melt sales from wide (1 column per day) to long (1 row per item-store-day)
2. Join through `calendar.csv` to attach real dates and the `wm_yr_wk` week
   code (the bridge table between daily sales and weekly price)
3. Join to `sell_prices.csv` on (store_id, item_id, wm_yr_wk) - this is the
   real grain mismatch, sales is daily, price is weekly
4. Forward-fill missing price within each item-store series (handles the
   slower-cadence source correctly)
5. Drop genuine **pre-launch** rows, where price is still missing after
   forward-fill because the item hadn't launched in that store yet (there is
   nothing to forward-fill from, these rows don't belong in the table)
6. Derive `revenue = units * sell_price` (M5 has no revenue column; this is
   the deterministic identity the whole PVM decomposition later depends on)

Verified: revenue identity holds exactly (0 violations) across all rows.

### 2. `source_marketing_weekly.parquet` (synthetic)

Generated per state/channel/week. Base spend by channel (Digital, InStore_
Promo, TV) with a mild, noisy positive relationship to that state's real
weekly revenue (not deterministic, correlation ~0.15 factor + gaussian
noise), so it's a plausible companion signal, not a copy of demand.

Deliberate mismatches baked in on purpose (per REQ-02's real-world messiness):
- **Region naming**: "West"/"South" instead of "CA"/"TX" - requires an
  explicit mapping, a naive merge on matching values will fail
- **Week convention**: Monday-start weeks, computed via explicit date
  arithmetic (`date - weekday_offset`), not sales' Sunday-start (W-SAT)
  weeks - a naive join without reconciling this misaligns by up to 6 days
  (a real bug caught during integrity testing, see note below)

Deliberate injection for the abstention scenario:
- The last 2 weeks of **South region, Digital channel** spend are dropped
  entirely, simulating a stale/missing feed. This is the trigger for the
  "insufficient evidence, abstain" scenario, real absence, not a
  contradiction dressed up as one.

### 3. `source_supply_monthly.parquet` (synthetic)

Generated per warehouse_sku/state/month. Fill rate normally distributed
around 98.5% (clipped 90-100%), stockout_days normally 0.

Deliberate mismatch: keyed by `warehouse_sku` (e.g. `WH-1000`), not
`item_id`, forcing every downstream join to go through
`lookup_sku_to_item.parquet` first, exactly like a real warehouse system
keying differently from a POS system.

Deliberate injection for the multi-factor scenario:
- **FOODS_3_090, CA, Nov 2012**: fill_rate forced to 0.78, stockout_days
  set to 4. This month was chosen because it's a real, already-flagged
  materially anomalous week in the sales data (Thanksgiving 2012), so the
  injected supply constraint sits alongside real price/demand behavior
  rather than on an arbitrary, unremarkable week.

## A bug worth knowing about (and why it's left documented, not hidden)

The first build of the marketing source used pandas' `freq="W-MON"`,
assuming it meant "weeks starting Monday." It actually anchors weeks to
**end** on Monday, so periods ran Tuesday-through-Monday. This silently
broke the 3-source join, 0 marketing rows matched sales rows on the first
integrity test. Fixed by computing Monday-of-week explicitly via date
arithmetic instead of relying on the frequency code, then re-verified.
Kept here as a real, caught example of the calendar-mismatch problem the
reconciliation layer (REQ-02) is meant to demonstrate handling.

## Integrity checks passed (all six, verified, not assumed)

1. Revenue identity (`revenue == units * sell_price`) holds for every row
2. Marketing region names genuinely don't match sales state codes (mapping required)
3. Supply SKU codes genuinely don't match sales item_ids (lookup required)
4. A full 3-source join on a real slice (FOODS_3_090, CA) succeeds: all
   rows matched to both supply and marketing after correct joins
5. The injected Nov 2012 supply constraint (fill_rate=0.78) is visible
   after the join
6. The dropped South/Digital marketing weeks are genuinely absent, not
   zero-filled, confirming the abstention trigger is real

## Regenerating from scratch

```
python 01_get_and_build_dataset.py   # downloads M5, builds fact_sales_daily.parquet
python 02_gen_marketing_source.py    # builds source_marketing_weekly.parquet
python 03_gen_supply_source.py       # builds source_supply_monthly.parquet + lookup table
```

Detection outputs (`materiality_*.parquet`, `final_flagged_weeks_*.parquet`)
are regenerated by the REQ-01 detection script, run separately, against
`fact_sales_daily.parquet`.

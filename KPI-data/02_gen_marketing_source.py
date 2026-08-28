"""
Step 2a: Synthetic marketing spend source. WEEKLY grain (real sources don't
speak daily-vs-weekly by accident, this mismatches the sales backbone on
purpose, same as sell_prices.csv already does).

METHOD LABEL: pure simulation / rule-based generation, no LLM, no stats yet.
This is a companion source, not a claim about real Walmart marketing spend.

Deliberate real-world mismatches baked in here (per REQ-02):
  - Region naming: "West"/"South" instead of "CA"/"TX" (needs an explicit
    lookup/join, not a naive merge on matching column values)
  - Week start convention: marketing weeks start Monday, sales/price weeks
    (W-SAT) start Sunday -> a naive resample would misalign by a day
  - One deliberately corrupted week (missing spend record) for the
    abstention/contradictory-evidence scenario in TX / "South"
"""
import pandas as pd
import numpy as np

DATA = "/home/claude/kpi_engine/data"
fact = pd.read_parquet(f"{DATA}/fact_sales_daily.parquet")

STATE_TO_REGION_NAME = {"CA": "West", "TX": "South"}   # deliberate naming mismatch
CHANNELS = ["Digital", "InStore_Promo", "TV"]

rng = np.random.default_rng(42)

date_min, date_max = fact.date.min(), fact.date.max()
# Marketing weeks are explicitly Monday-start, Sunday-end (mismatched vs
# sales/price's W-SAT convention). Computed directly, NOT via pandas "W-MON"
# period frequency, which actually anchors weeks to END on Monday, not start
# on it - a real bug caught during integrity testing, worth noting: this is
# exactly the kind of silent calendar mismatch REQ-02 is meant to catch.
all_dates = pd.date_range(date_min, date_max, freq="D")
monday_starts = all_dates - pd.to_timedelta(all_dates.weekday, unit="D")
weeks = sorted(monday_starts.unique())

rows = []
for state, region_name in STATE_TO_REGION_NAME.items():
    # base weekly revenue for this state (used only to give marketing a
    # plausible, noisy, non-deterministic relationship to real demand)
    state_rev = (
        fact[fact.state_id == state]
        .assign(week_start_monday=lambda d: d.date - pd.to_timedelta(d.date.dt.weekday, unit="D"))
        .groupby("week_start_monday")
        .revenue.sum()
    )
    for wk in weeks:
        base_demand = state_rev.get(wk, state_rev.mean())
        for ch in CHANNELS:
            base_spend = {"Digital": 800, "InStore_Promo": 500, "TV": 1200}[ch]
            # mild, noisy positive relationship to demand - not deterministic
            demand_factor = 1 + 0.15 * (base_demand / max(state_rev.mean(), 1) - 1)
            noise = rng.normal(1.0, 0.12)
            spend = max(0, base_spend * demand_factor * noise)
            rows.append({
                "week_start_monday": wk,
                "region_name": region_name,   # mismatched naming vs state_id
                "channel": ch,
                "marketing_spend": round(spend, 2),
            })

mkt = pd.DataFrame(rows)

# --- inject: real missing-evidence week for the abstention scenario ---
# South/TX, most recent 2 weeks of Digital spend simply absent (stale/missing feed)
last_two_weeks = sorted(mkt.week_start_monday.unique())[-2:]
mask_drop = (
    (mkt.region_name == "South") &
    (mkt.channel == "Digital") &
    (mkt.week_start_monday.isin(last_two_weeks))
)
print(f"Dropping {mask_drop.sum()} rows to simulate a missing/stale marketing feed "
      f"(South region, Digital channel, last 2 weeks) - this is the abstention trigger")
mkt = mkt[~mask_drop].copy()

mkt.to_parquet(f"{DATA}/source_marketing_weekly.parquet", index=False)
print(f"\nSaved source_marketing_weekly.parquet: {len(mkt)} rows")
print(mkt.groupby(["region_name", "channel"]).marketing_spend.agg(["mean", "count"]).round(2))

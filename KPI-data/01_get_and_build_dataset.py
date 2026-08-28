"""
KPI Intelligence-to-Action Engine
Step 0 + Step 1: Get the dataset, then reconcile it into a clean fact table.

WHAT THIS DOES
  1. Downloads the M5 (Walmart) forecasting dataset from a public GitHub mirror
     (Kaggle requires an account + API token, this mirror does not).
  2. Extracts sales_train_validation.csv, sell_prices.csv, calendar.csv.
  3. Reshapes sales from wide (1 col/day) to long (1 row per item-store-day).
  4. Joins sales -> calendar -> prices (this is the real grain mismatch:
     sales is daily, price is weekly, calendar is the bridge table).
  5. Derives revenue = units * price (never trusted from source, since M5
     doesn't even provide a revenue column).
  6. Drops genuine pre-launch rows (leading gaps where price is NaN because
     the item didn't exist yet in that store - NOT a stale-data problem,
     there is nothing to forward-fill from).

METHOD LABELS (per the "LLM vs non-LLM" requirement):
  Everything in this script is deterministic / SQL-style joins and pandas
  transforms. No LLM, no statistics, no ML. That's intentional, this is the
  reconciliation layer, its whole job is to be exactly correct and auditable.

USAGE
  pip install pandas pyarrow --break-system-packages
  python 01_get_and_build_dataset.py
"""
import os
import zipfile
import urllib.request
import pandas as pd

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
RAW_DIR = "./m5_raw"
DATA_DIR = "./data"
M5_MIRROR_URL = (
    "https://raw.githubusercontent.com/KunalArora/kaggle-m5-forecasting/"
    "master/m5-forecasting-accuracy.zip"
)

# Deliberately narrow scope: 2 states, 3 items chosen from real inspection:
#   - FOODS_3_090: established, high-volume, has a REAL ~25% price cut in
#     Aug 2013 (a real, non-scripted decomposition test case)
#   - FOODS_3_586: same department as FOODS_3_090 (FOODS_3), added specifically
#     so a real MIX effect is computable within the FOODS_3 category (one item
#     alone cannot demonstrate a mix shift, mix requires >=2 items in a category)
#   - HOUSEHOLD_1_020: genuinely short real history (as little as 198 days
#     in TX_1 vs 1,913 for the mature item) - a real sparse-history case
FOCUS_ITEMS = ["FOODS_3_090", "FOODS_3_586", "HOUSEHOLD_1_020"]
FOCUS_STATES = ["CA", "TX"]


def download_and_extract():
    os.makedirs(RAW_DIR, exist_ok=True)
    zip_path = os.path.join(RAW_DIR, "m5.zip")
    if not os.path.exists(zip_path):
        print(f"Downloading M5 dataset from mirror...")
        urllib.request.urlretrieve(M5_MIRROR_URL, zip_path)
        print(f"Downloaded {os.path.getsize(zip_path)/1e6:.1f} MB")
    else:
        print("Zip already present, skipping download.")

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(RAW_DIR)
    print("Extracted.")

    extracted_dir = os.path.join(RAW_DIR, "m5-forecasting-accuracy")
    for f in ["sales_train_validation.csv", "calendar.csv", "sell_prices.csv"]:
        path = os.path.join(extracted_dir, f)
        assert os.path.exists(path), f"Missing expected file: {path}"
    return extracted_dir


def build_fact_table(raw_dir):
    print("\nLoading raw sources...")
    sales = pd.read_csv(f"{raw_dir}/sales_train_validation.csv")
    cal = pd.read_csv(f"{raw_dir}/calendar.csv")
    prices = pd.read_csv(f"{raw_dir}/sell_prices.csv")

    sales = sales[sales.item_id.isin(FOCUS_ITEMS) & sales.state_id.isin(FOCUS_STATES)].copy()

    # HOUSEHOLD_1_020 was found to launch at wildly different times per store
    # (198 days of real history in TX_1, but 940+ days in TX_2/TX_3, 429 in
    # CA_4 but 786 in CA_1). Aggregating all stores to state grain, the same
    # grain used everywhere else in the pipeline, would let the mature stores
    # swamp the genuinely sparse ones and hide the sparse-history scenario
    # entirely. Restrict this item to its two genuinely sparse stores only,
    # so state-level aggregation reflects real sparsity instead of masking it.
    SPARSE_ITEM = "HOUSEHOLD_1_020"
    SPARSE_ITEM_STORES = ["TX_1", "CA_4"]
    drop_mask = (sales.item_id == SPARSE_ITEM) & (~sales.store_id.isin(SPARSE_ITEM_STORES))
    print(f"Restricting {SPARSE_ITEM} to stores {SPARSE_ITEM_STORES} "
          f"(dropping {drop_mask.sum()} rows from mature stores that would mask its sparsity)")
    sales = sales[~drop_mask].copy()

    print(f"Scoped sales rows (item-store combinations): {len(sales)}")

    d_cols = [c for c in sales.columns if c.startswith("d_")]
    id_cols = ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]

    # reshape: wide (1 col per day) -> long (1 row per item-store-day)
    long_sales = sales.melt(id_vars=id_cols, value_vars=d_cols, var_name="d", value_name="units")

    # join through calendar bridge to get real date + wm_yr_wk + event/SNAP context
    cal_small = cal[["d", "date", "wm_yr_wk", "event_name_1", "event_type_1",
                      "snap_CA", "snap_TX", "snap_WI"]]
    long_sales = long_sales.merge(cal_small, on="d", how="left")
    long_sales["date"] = pd.to_datetime(long_sales["date"])

    # join to weekly price via (store_id, item_id, wm_yr_wk) - the real grain mismatch
    fact = long_sales.merge(prices, on=["store_id", "item_id", "wm_yr_wk"], how="left")
    fact["price_source_grain"] = "weekly"

    # forward-fill missing price within each item-store series (documented rule:
    # simulates handling a slower-cadence source correctly)
    fact = fact.sort_values(["item_id", "store_id", "date"])
    fact["price_is_imputed"] = fact["sell_price"].isna()
    fact["sell_price"] = fact.groupby(["item_id", "store_id"])["sell_price"].ffill()

    # anything STILL missing after ffill is a leading gap = item not yet launched
    # in that store. Drop it, and log it, don't fabricate a forward-fill from nothing.
    pre_launch = fact["sell_price"].isna()
    pre_launch_summary = (
        fact[pre_launch].groupby(["item_id", "store_id"]).size()
        .rename("pre_launch_days_dropped")
    )
    fact = fact[~pre_launch].copy()

    # revenue is DERIVED, never trusted from source - the deterministic identity
    fact["revenue"] = fact["units"].astype(float) * fact["sell_price"].astype(float)

    fact = fact.drop(columns=["id"]).sort_values(["item_id", "store_id", "date"]).reset_index(drop=True)

    print(f"\nFinal reconciled fact table: {len(fact)} rows")
    print(f"Date range: {fact.date.min().date()} to {fact.date.max().date()}")
    print(f"\nPre-launch rows dropped (item not yet sold in that store):")
    print(pre_launch_summary)
    print(f"\nPer item-store history span after reconciliation:")
    print(fact.groupby(["item_id", "store_id"]).date.agg(["min", "max", "count"]))

    return fact


if __name__ == "__main__":
    raw_dir = download_and_extract()
    fact = build_fact_table(raw_dir)

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = f"{DATA_DIR}/fact_sales_daily.parquet"
    fact.to_parquet(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print("\nSample rows:")
    print(fact[["date", "item_id", "store_id", "units", "sell_price",
                "revenue", "price_is_imputed"]].tail(8).to_string(index=False))

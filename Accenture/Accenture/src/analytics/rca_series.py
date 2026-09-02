"""Weekly multivariate panel for EasyRCA -- one column per causal-graph
variable, all source tables (daily / weekly / monthly) resampled onto a common
Monday-start index. Monthly series are forward-filled.
"""
import numpy as np
import pandas as pd

from analytics.aggregation import (
    get_item_state_daily, get_marketing_weekly, get_supply_monthly,
    get_inventory_turnover_daily, get_events,
)
from analytics.sentiment import get_feedback_monthly_sentiment
from analytics.causal_graph import VARIABLES

_PANEL_CACHE = {}

# zero-filled when a week has no rows; the rest are levels, so forward/back-filled
_ZERO_FILL = {"units", "revenue", "marketing_spend", "event"}


def _to_monday(s):
    s = pd.to_datetime(s)
    return s - pd.to_timedelta(s.dt.weekday, unit="D")


def build_weekly_panel(db_path, item_id, state_id, use_cache=True):
    key = (db_path, item_id, state_id)
    if use_cache and key in _PANEL_CACHE:
        return _PANEL_CACHE[key].copy()

    daily = get_item_state_daily(db_path)
    daily = daily[(daily["item_id"] == item_id) & (daily["state_id"] == state_id)].copy()
    if daily.empty:
        raise ValueError(f"no sales rows for {item_id}/{state_id} -- cannot build panel")
    daily["week"] = _to_monday(daily["date"])
    wk = daily.groupby("week").agg(
        units=("units", "sum"), revenue=("revenue", "sum"),
        cogs=("cost_of_goods_sold", "sum"),
    )
    wk["sell_price"] = np.where(wk["units"] > 0, wk["revenue"] / wk["units"], np.nan)
    wk["gross_margin_percent"] = np.where(
        wk["revenue"] > 0, (wk["revenue"] - wk["cogs"]) / wk["revenue"], np.nan)
    wk = wk.drop(columns=["cogs"])

    weeks = pd.date_range(wk.index.min(), wk.index.max(), freq="W-MON")
    panel = wk.reindex(weeks)
    panel.index.name = "week"

    mkt = get_marketing_weekly(db_path)
    mkt = mkt[mkt["state_id"] == state_id].copy()
    if not mkt.empty:
        mkt["week"] = _to_monday(mkt["week_start_monday"])
        panel["marketing_spend"] = mkt.groupby("week")["marketing_spend"].sum().reindex(weeks)
    else:
        panel["marketing_spend"] = np.nan

    sup = get_supply_monthly(db_path)
    sup = sup[(sup["state_id"] == state_id) & (sup["item_id"] == item_id)].copy()
    if not sup.empty:
        sup["mp"] = pd.PeriodIndex(pd.to_datetime(sup["month"]), freq="M")
        sup = sup.groupby("mp").agg(fill_rate=("fill_rate", "mean"),
                                    stockout_days=("stockout_days", "max"))
        wk_month = panel.index.to_period("M")
        panel["fill_rate"] = sup["fill_rate"].reindex(wk_month).to_numpy()
        panel["stockout_days"] = sup["stockout_days"].reindex(wk_month).to_numpy()
    else:
        panel["fill_rate"] = np.nan
        panel["stockout_days"] = np.nan

    inv = get_inventory_turnover_daily(db_path)
    inv = inv[(inv["item_id"] == item_id) & (inv["state_id"] == state_id)].copy()
    if not inv.empty:
        inv["week"] = _to_monday(inv["date"])
        panel["inventory_turnover"] = inv.groupby("week")["turnover_ratio"].mean().reindex(weeks)
    else:
        panel["inventory_turnover"] = np.nan

    sent = get_feedback_monthly_sentiment(db_path)
    sent = sent[(sent["item_id"] == item_id) & (sent["state_id"] == state_id)].copy()
    if not sent.empty:
        sp = sent.set_index(pd.PeriodIndex(sent["month_date"], freq="M"))["mean_sentiment"]
        sp = sp[~sp.index.duplicated(keep="last")]
        panel["sentiment"] = sp.reindex(panel.index.to_period("M")).to_numpy()
    else:
        panel["sentiment"] = np.nan

    ev = get_events(db_path)
    if not ev.empty:
        ev = ev.copy()
        ev["week"] = _to_monday(ev["date"])
        panel["event"] = ev.groupby("week").size().reindex(weeks).fillna(0.0)
    else:
        panel["event"] = 0.0

    panel = panel[VARIABLES]
    for col in VARIABLES:
        panel[col] = panel[col].fillna(0.0) if col in _ZERO_FILL else panel[col].ffill().bfill()
    panel = panel.fillna(0.0)

    if use_cache:
        _PANEL_CACHE[key] = panel.copy()
    return panel


def clear_cache():
    _PANEL_CACHE.clear()

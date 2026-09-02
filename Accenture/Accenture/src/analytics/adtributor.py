"""Multi-dimensional slice attribution -- a dependency-light reimplementation of
Adtributor (Bhagwan, Kumar, Ramjee, Varghese, Mohapatra, Manoharan & Shah,
"Adtributor: Revenue Debugging in Advertising Systems", NSDI 2014, pp. 43-55).

Given a measure's forecast vs actual for an anomaly window, broken down by
categorical dimensions (item / state / store / category), find the dimension +
element set that best explain the deviation, ranked by *surprise* (how much the
element-share distribution shifted, via Jensen-Shannon divergence) rather than
raw magnitude. Succinctness is applied via a per-element explanatory-power gate.
"""
import sqlite3

import numpy as np
import pandas as pd

# Paper parameters (Section 3.4), plus a materiality gate.
T_EP = 0.67           # a candidate element set must explain this fraction of the change
T_EEP = 0.10          # per-element minimum explanatory power to be admitted
TOP_K = 3
MIN_DEVIATION = 0.08  # |A - F| / |F| must clear this before any slice is attributed
_EPS = 1e-9

_DIM_COLS = ["item_id", "state_id", "store_id", "cat_id"]
_MEASURE_COLS = ["revenue", "cost_of_goods_sold", "units"]
_METHOD = "Adtributor (Bhagwan et al., NSDI 2014) - reimplemented"

# KPI name (as stored in the `anomalies` table) -> (mode, spec). "fundamental":
# EP = the slice's share of the total delta. "derived": EP for a ratio num/den
# via the finite-difference partial derivative (paper eq. 8); `sign` flips it
# (gross margin % = 1 - cogs/revenue).
_KPI_SPEC = {
    "Revenue": ("fundamental", "revenue"),
    "GrossMarginPercent": ("derived", ("cost_of_goods_sold", "revenue", -1.0)),
}


def _js_surprise(p, q):
    """Jensen-Shannon divergence term for forecast share `p` and actual share
    `q` (paper eq. 7) -- symmetric and finite with zero shares, unlike KL."""
    p, q = max(float(p), _EPS), max(float(q), _EPS)
    m = 0.5 * (p + q)
    return 0.5 * (p * np.log(p / m) + q * np.log(q / m))


def _attribute_dimension(g, dim, mode, spec):
    """`g`: one row per element of `dim` with f_<m>/a_<m> columns. Returns a
    candidate dict (its `elements` may be empty)."""
    g = g.copy()
    if mode == "fundamental":
        m = spec
        F_tot, A_tot = g[f"f_{m}"].sum(), g[f"a_{m}"].sum()
        denom = A_tot - F_tot
        g["ep"] = (g[f"a_{m}"] - g[f"f_{m}"]) / (denom if abs(denom) > _EPS else np.nan)
        g["p"] = g[f"f_{m}"].abs() / (g[f"f_{m}"].abs().sum() or _EPS)
        g["q"] = g[f"a_{m}"].abs() / (g[f"a_{m}"].abs().sum() or _EPS)
    else:
        num, den, sign = spec
        F1, F2 = g[f"f_{num}"].sum(), g[f"f_{den}"].sum()
        d2 = g[f"a_{den}"] - g[f"f_{den}"]
        raw = ((((g[f"a_{num}"] - g[f"f_{num}"]) * F2) - (d2 * F1)) / (F2 * (F2 + d2)))
        raw = raw.replace([np.inf, -np.inf], np.nan) * sign
        s = raw.sum()
        g["ep"] = raw / (s if abs(s) > _EPS else np.nan)
        rf = sign * g[f"f_{num}"] / g[f"f_{den}"].replace(0, np.nan)
        ra = sign * g[f"a_{num}"] / g[f"a_{den}"].replace(0, np.nan)
        g["p"] = rf.abs() / (rf.abs().sum() or _EPS)
        g["q"] = ra.abs() / (ra.abs().sum() or _EPS)

    g[["ep", "p", "q"]] = g[["ep", "p", "q"]].fillna(0.0)
    g["surprise"] = [_js_surprise(p, q) for p, q in zip(g["p"], g["q"])]
    g = g.sort_values("surprise", ascending=False).reset_index(drop=True)

    # Greedy: add elements (most surprising first) that clear T_EEP, until the
    # set explains a majority (T_EP). Not padded with barely-surprising elements
    # just to reach T_EP -- under a large uniform background move every big slice
    # has high EP and near-zero surprise, which is the magnitude trap.
    max_surp = float(g["surprise"].iloc[0]) if len(g) else 0.0
    picked, ep_cum, surp_cum = [], 0.0, 0.0
    for _, row in g.iterrows():
        if row["ep"] <= T_EEP:
            continue
        if picked and float(row["surprise"]) < 0.15 * max_surp:
            break
        picked.append(str(row[dim]))
        ep_cum += float(row["ep"])
        surp_cum += float(row["surprise"])
        if ep_cum >= T_EP:
            break

    return {
        "dimension": dim, "elements": picked,
        "explanatory_power": round(ep_cum, 3), "surprise": round(surp_cum, 4),
        "top_elements": [
            {"element": str(r[dim]), "ep": round(float(r["ep"]), 3),
             "surprise": round(float(r["surprise"]), 4)}
            for _, r in g.head(5).iterrows()
        ],
    }


def is_material(frame, kpi_name):
    """True if the overall measure moved enough to be worth attributing."""
    if "f_revenue" not in frame.columns:
        return False
    if kpi_name == "GrossMarginPercent":
        fr, ar = frame["f_revenue"].sum(), frame["a_revenue"].sum()
        gm_f = (fr - frame["f_cost_of_goods_sold"].sum()) / fr if abs(fr) > _EPS else 0.0
        gm_a = (ar - frame["a_cost_of_goods_sold"].sum()) / ar if abs(ar) > _EPS else 0.0
        return abs(gm_a - gm_f) >= 0.02
    ft, at = frame["f_revenue"].sum(), frame["a_revenue"].sum()
    return abs(at - ft) >= MIN_DEVIATION * max(abs(ft), _EPS)


def attribute(frame, dimensions, kpi_name, top_k=TOP_K):
    """`frame`: one row per slice with f_<m>/a_<m> columns. Returns the `top_k`
    most surprising candidate sets across `dimensions`."""
    spec = _KPI_SPEC.get(kpi_name)
    if spec is None or not is_material(frame, kpi_name):
        return []
    mode, payload = spec
    cols = [f"{p}_{m}" for m in _MEASURE_COLS for p in ("f", "a")]
    cands = []
    for d in dimensions:
        if d not in frame.columns:
            continue
        c = _attribute_dimension(frame.groupby(d, as_index=False)[cols].sum(), d, mode, payload)
        if c["elements"]:
            cands.append(c)
    cands.sort(key=lambda c: -c["surprise"])
    return cands[:top_k]


def build_slice_frame(db_path, period_start, period_end, dims=None, baseline_periods=8,
                      item_id=None, state_id=None):
    """Per-slice forecast vs actual for [period_start, period_end]. Forecast is
    the mean of the `baseline_periods` preceding same-length windows. `item_id` /
    `state_id` scope the frame so a single-SKU anomaly is attributed within its
    own scope rather than portfolio-wide."""
    dims = dims or _DIM_COLS
    where, params = [], []
    if item_id:
        where.append("item_id = ?"); params.append(item_id)
    if state_id:
        where.append("state_id = ?"); params.append(state_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        f"SELECT date, {', '.join(dims)}, revenue, cost_of_goods_sold, units "
        f"FROM fact_sales_daily{clause}", conn, params=params)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])

    ps, pe = pd.Timestamp(period_start), pd.Timestamp(period_end)
    win_days = (pe - ps).days + 1
    bl_start = ps - pd.Timedelta(days=win_days * baseline_periods)

    actual = df[(df["date"] >= ps) & (df["date"] <= pe)]
    baseline = df[(df["date"] >= bl_start) & (df["date"] < ps)]
    if actual.empty:
        return pd.DataFrame(columns=dims + [f"{p}_{m}" for m in _MEASURE_COLS for p in ("f", "a")])

    a = actual.groupby(dims, as_index=False)[_MEASURE_COLS].sum()
    f = baseline.groupby(dims, as_index=False)[_MEASURE_COLS].sum()
    for m in _MEASURE_COLS:
        f[m] = f[m] / max(baseline_periods, 1)

    merged = pd.merge(f, a, on=dims, how="outer", suffixes=("_f", "_a")).fillna(0.0)
    out = merged[dims].copy()
    for m in _MEASURE_COLS:
        out[f"f_{m}"], out[f"a_{m}"] = merged[f"{m}_f"], merged[f"{m}_a"]
    return out


def run_attribution(db_path, kpi_name, period_start, period_end, dims=None,
                    item_id=None, state_id=None):
    """Top-level entry point -- always returns a safe dict. `item_id`/`state_id`
    scope the attribution to the anomaly's own slice."""
    base = {"available": False, "candidates": [], "reason": "", "method": _METHOD}
    if kpi_name not in _KPI_SPEC:
        base["reason"] = f"slice attribution not defined for KPI '{kpi_name}'"
        return base
    scoped = [d for d in (dims or _DIM_COLS)
              if not (d == "item_id" and item_id) and not (d == "state_id" and state_id)]
    try:
        frame = build_slice_frame(db_path, period_start, period_end, dims=scoped or _DIM_COLS,
                                  item_id=item_id, state_id=state_id)
        if frame.empty or frame["a_revenue"].abs().sum() < _EPS:
            base["reason"] = "no sales rows in the anomaly window"
            return base
        if not is_material(frame, kpi_name):
            base["available"] = True
            base["reason"] = "overall measure did not move enough to attribute to a slice"
            return base
        cands = attribute(frame, scoped or _DIM_COLS, kpi_name)
        if not cands:
            base["available"] = True
            base["reason"] = "no slice clears the explanatory-power threshold"
            return base
        return {
            "available": True, "method": _METHOD, "measure": kpi_name,
            "params": {"T_EP": T_EP, "T_EEP": T_EEP, "top_k": TOP_K},
            "candidates": cands,
            "reason": "ranked by distribution surprise (Jensen-Shannon divergence)",
        }
    except Exception as e:  # noqa: BLE001
        base["reason"] = f"{type(e).__name__}: {e}"
        return base

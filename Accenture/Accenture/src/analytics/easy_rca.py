"""Dependency-light reimplementation of EasyRCA (Assaad, Ez-Zejjari & Zan,
"Root Cause Identification for Collective Anomalies in Time Series given an
Acyclic Summary Causal Graph with Loops", AISTATS 2023; PMLR 206:8395-8404).

Given a normal-regime window, an anomalous-regime window, and the set of
anomalous variables:
  1. decompose the anomalous variables into independent subproblems by
     d-separation on the summary causal graph;
  2. a variable with no anomalous parent (or an earlier onset) is a root cause;
  3. for the rest, refit `var ~ parents` on the normal window and test whether
     the residuals shift in the anomalous window -- a shift means the mechanism
     itself changed (root cause), otherwise the variable only propagates.

Graph-with-loops handling from the paper is out of scope; the summary graph is
a DAG. Abstains (status "insufficient_data") when the anomalous window is too
short to fit a regression.
"""
import numpy as np
import pandas as pd
import networkx as nx
from scipy import stats as _stats

from analytics.causal_graph import (
    SUMMARY_CAUSAL_GRAPH, is_d_separated, backdoor_adjustment_set, variable_of,
)

Z_THRESHOLD = 2.0
ALPHA = 0.05
MIN_EXTRA = 3              # normal-window rows required beyond the covariate count
MIN_RESIDUAL_SHIFT = 1.0   # min |mean standardized residual| to call a mechanism shift
EFFECT_CAP = 25.0

# Absolute floor on the standardizing scale, per variable. Near-zero-mean series
# (sentiment ~[-3, 3], a flat baseline) otherwise read as tens of sigma for any
# wobble and tie every root cause at EFFECT_CAP.
_SCALE_FLOOR = {"sentiment": 0.6, "gross_margin_percent": 0.02,
                "fill_rate": 0.01, "stockout_days": 0.5}

# A variable is anomalous only if the regime shift clears both a z-score bar and
# a materiality bar (rel = fraction of the normal mean, abs_ = raw units).
_MATERIALITY = {
    "marketing_spend":      {"rel": 0.20, "abs_": 0.0},
    "sell_price":           {"rel": 0.05, "abs_": 0.0},
    "units":                {"rel": 0.20, "abs_": 0.0},
    "revenue":              {"rel": 0.20, "abs_": 0.0},
    "gross_margin_percent": {"rel": 0.10, "abs_": 0.02},
    "fill_rate":            {"rel": 0.05, "abs_": 0.03},
    "stockout_days":        {"rel": 0.00, "abs_": 1.0},
    "inventory_turnover":   {"rel": 0.30, "abs_": 0.0},
    "sentiment":            {"rel": 0.00, "abs_": 1.0},
    "event":                {"rel": 1e9,  "abs_": 1e9},
}

_METHOD = "EasyRCA (Assaad et al., 2023) - reimplemented, no dowhy/tigramite"


def _safe_std(x, ddof=0):
    s = float(np.std(np.asarray(x, dtype=float), ddof=ddof))
    return s if np.isfinite(s) and s > 1e-12 else 0.0


def _robust_scale(normal, var=None):
    normal = np.asarray(normal, dtype=float)
    mu = abs(normal.mean()) if len(normal) else 0.0
    return max(_safe_std(normal), 0.05 * mu, _SCALE_FLOOR.get(var, 1e-9))


def _mean_z(normal, other, var=None):
    normal = np.asarray(normal, dtype=float)
    other = np.asarray(other, dtype=float)
    if not len(other):
        return 0.0
    mu = normal.mean() if len(normal) else 0.0
    return float(np.clip((other.mean() - mu) / _robust_scale(normal, var),
                         -EFFECT_CAP, EFFECT_CAP))


def _is_material(var, normal, anom):
    normal = np.asarray(normal, dtype=float)
    anom = np.asarray(anom, dtype=float)
    if not len(normal) or not len(anom):
        return False
    if abs(_mean_z(normal, anom, var)) < Z_THRESHOLD:
        return False
    delta = abs(anom.mean() - normal.mean())
    cfg = _MATERIALITY.get(var, {"rel": 0.15, "abs_": 0.0})
    checks = []
    if cfg["rel"] > 0:
        checks.append(delta >= cfg["rel"] * max(abs(normal.mean()), 1e-9))
    if cfg["abs_"] > 0:
        checks.append(delta >= cfg["abs_"])
    return bool(checks) and any(checks)


def _decompose(causal_graph, anomalous_vars, observed):
    """Step 1: partition the anomalous variables into d-separated subgroups."""
    avars = list(anomalous_vars)
    if len(avars) <= 1:
        return [avars] if avars else []
    sub = causal_graph.subgraph([v for v in causal_graph.nodes if v in observed])
    ug = nx.Graph()
    ug.add_nodes_from(avars)
    for i in range(len(avars)):
        for j in range(i + 1, len(avars)):
            a, b = avars[i], avars[j]
            try:
                dsep = is_d_separated(sub, a, b, set(observed) - {a, b})
            except Exception:
                dsep = False
            if not dsep:
                ug.add_edge(a, b)
    return [sorted(c) for c in nx.connected_components(ug)]


def _regime_test(normal_df, anom_df, v, parents, causal_graph, alpha):
    """Step 3: refit v ~ parents on the normal window, test the residual shift
    in the anomalous window."""
    cov = list(parents)
    for p in parents:
        cov += list(backdoor_adjustment_set(causal_graph, p, v))
    cov = [c for c in dict.fromkeys(cov)
           if c != v and c in normal_df.columns and c in anom_df.columns]

    y_n = normal_df[v].to_numpy(dtype=float)
    y_a = anom_df[v].to_numpy(dtype=float)
    if len(y_n) < len(cov) + 1 + MIN_EXTRA or len(y_a) < 2:
        return {"status": "insufficient"}

    # A covariate that barely varies in the normal window is unidentifiable, so
    # a "mechanism shift" can't be told from the linear model failing to
    # extrapolate. Abstain instead.
    for c in cov:
        cn = normal_df[c].to_numpy(dtype=float)
        if _safe_std(cn) < 0.02 * max(abs(cn.mean()), 1e-9):
            return {"status": "insufficient"}

    A_n = (np.column_stack([np.ones(len(y_n))] + [normal_df[c].to_numpy(float) for c in cov])
           if cov else np.ones((len(y_n), 1)))
    A_a = (np.column_stack([np.ones(len(y_a))] + [anom_df[c].to_numpy(float) for c in cov])
           if cov else np.ones((len(y_a), 1)))

    coef, _, rank, _ = np.linalg.lstsq(A_n, y_n, rcond=None)
    if rank < A_n.shape[1]:
        return {"status": "insufficient"}

    resid_n = y_n - A_n @ coef
    # floor the residual scale so a near-perfect normal-window fit doesn't turn
    # any anom-window wobble into a millions-of-sigma "shift".
    s = max(_safe_std(resid_n, ddof=1), 0.05 * _robust_scale(y_n, v))
    resid_a = np.clip((y_a - A_a @ coef) / s, -EFFECT_CAP, EFFECT_CAP)
    mean_shift = float(np.mean(resid_a))
    _, pval = _stats.ttest_1samp(resid_a, 0.0)

    shifted = (float(pval) < alpha) and (abs(mean_shift) >= MIN_RESIDUAL_SHIFT)
    return {"status": "ok", "shifted": shifted, "effect": abs(mean_shift), "pval": float(pval)}


def find_root_causes(normal_df, anom_df, causal_graph=None, anomalous_vars=None,
                     onsets=None, alpha=ALPHA, z_threshold=Z_THRESHOLD):
    """normal_df / anom_df are weekly panels for the two regimes. `anomalous_vars`
    defaults to the variables with a material regime shift. `onsets` is an
    optional {var: comparable-onset} for the earlier-onset rule."""
    causal_graph = causal_graph or SUMMARY_CAUSAL_GRAPH
    observed = [v for v in causal_graph.nodes if v in normal_df.columns]

    if anomalous_vars is None:
        anomalous_vars = [v for v in observed if _is_material(v, normal_df[v], anom_df[v])]
    anomalous_vars = [v for v in dict.fromkeys(anomalous_vars) if v in observed]

    if not anomalous_vars:
        return {"status": "no_causal_variables", "root_causes": [], "subgroups": [],
                "reason": "no observed causal variable is anomalous", "method": _METHOD}

    subgroups = _decompose(causal_graph, anomalous_vars, observed)
    root_causes, any_insufficient = [], False

    for sg in subgroups:
        for v in sg:
            parents = set(causal_graph.predecessors(v)) & set(observed)
            anom_parents = parents & set(anomalous_vars)

            if not anom_parents:
                root_causes.append({
                    "variable": v, "mechanism": "structural_root",
                    "effect": abs(_mean_z(normal_df[v], anom_df[v], v)),
                    "label": f"{v}: anomalous with no anomalous cause upstream"})
                continue

            if onsets and v in onsets and all(
                p in onsets and onsets[v] + 2 <= onsets[p] for p in anom_parents
            ):
                root_causes.append({
                    "variable": v, "mechanism": "earlier_onset",
                    "effect": abs(_mean_z(normal_df[v], anom_df[v], v)),
                    "label": f"{v}: anomaly began before its upstream causes"})
                continue

            res = _regime_test(normal_df, anom_df, v, parents, causal_graph, alpha)
            if res["status"] == "insufficient":
                any_insufficient = True
            elif res["shifted"]:
                root_causes.append({
                    "variable": v, "mechanism": "mechanism_shift", "effect": res["effect"],
                    "label": (f"{v}: its own price/demand relationship changed "
                              f"(residual shift {res['effect']:.1f}sigma, p={res['pval']:.3f})")})

    root_causes.sort(key=lambda r: -r["effect"])

    if root_causes:
        status = "ok"
        reason = "root cause(s) identified from the summary causal graph"
        if any_insufficient:
            reason += "; some downstream mechanisms could not be tested (short window)"
    elif any_insufficient:
        status = "insufficient_data"
        reason = "anomalous window too short to fit/test the structural equations"
    else:
        status = "ok"
        reason = "all anomalous variables explained as propagation; no mechanism changed"

    return {"status": status, "root_causes": root_causes, "subgroups": subgroups,
            "reason": reason, "method": _METHOD}


def _finalize_windows(panel, idx, end, target_var, gap, normal_len, z_threshold):
    """Shared tail: build the normal/anomalous frames + anomalous-var set +
    onsets for an anomalous window [idx, end] on `panel`."""
    n_end = max(0, idx - gap)
    n0 = max(0, n_end - normal_len)
    if n_end - n0 < 4:                       # not enough clean baseline -> abut it
        n0, n_end = max(0, idx - normal_len), idx
    normal_df = panel.iloc[n0:n_end]
    anom_df = panel.iloc[idx:end + 1]
    if len(normal_df) < 4 or len(anom_df) < 1:
        return None

    base_mu = normal_df[target_var].mean()
    base_sd = _robust_scale(normal_df[target_var], target_var)
    target_visible = abs((anom_df[target_var].mean() - base_mu) / base_sd) >= max(1.5, z_threshold * 0.6)

    anomalous = {target_var} if target_visible else set()
    for v in panel.columns:
        if _is_material(v, normal_df[v], anom_df[v]):
            anomalous.add(v)

    onsets = {}
    for v in anomalous:
        mu, sd = normal_df[v].mean(), _robust_scale(normal_df[v], v)
        for w in range(idx, end + 1):
            if abs((panel[v].iloc[w] - mu) / sd) >= z_threshold:
                onsets[v] = w
                break

    return {"normal_df": normal_df, "anom_df": anom_df,
            "anomalous_vars": sorted(anomalous), "onsets": onsets,
            "target_var": target_var, "target_visible": bool(target_visible),
            "window": (panel.index[idx], panel.index[end])}


def derive_windows_for_period(panel, target_var, period_start, period_end,
                              normal_len=8, gap=4, z_threshold=Z_THRESHOLD):
    """Normal/anomalous windows for an explicit anomalous period. `gap` weeks are
    left before the anomalous window so a monthly shock whose month overlaps the
    lookback does not contaminate the baseline."""
    if target_var is None or target_var not in panel.columns:
        return None
    ps, pe = pd.Timestamp(period_start), pd.Timestamp(period_end)
    in_win = (panel.index >= ps - pd.Timedelta(days=6)) & (panel.index <= pe)
    if not in_win.any():
        return None
    pos = np.where(in_win)[0]
    idx, end = int(pos[0]), int(pos[-1])
    if idx <= 1:
        return None
    return _finalize_windows(panel, idx, end, target_var, gap, normal_len, z_threshold)

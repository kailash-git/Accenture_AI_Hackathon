"""
experiments/slice_predictors.py

Two slice-attribution systems behind one predict(case) -> Prediction interface:

* magnitude  -- the CURRENT approach: rank each dimension's elements by raw
                |actual - forecast| contribution, greedily take them until the
                set covers a majority of the total |delta|, then pick the
                dimension whose set has the largest absolute contribution. This
                is what the drawer's per-product PVM breakdown does today
                (`pvm.products`, ranked by revenueImpact).
* adtributor -- analytics.adtributor: rank by distribution *surprise*
                (Jensen-Shannon), Occam via a per-element EP threshold.

Prediction = {"dimension": str|None, "elements": [ranked], "abstained": bool,
              "confidence": float}
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "Accenture", "Accenture", "src"))

from analytics.adtributor import (  # noqa: E402
    attribute, build_slice_frame, run_attribution, is_material,
    T_EP, T_EEP, _MEASURE_COLS,
)


def _pred(dimension, elements, abstained, confidence):
    return {"dimension": dimension, "elements": list(elements),
            "abstained": bool(abstained), "confidence": float(confidence)}


def _frame_for(case):
    if case.frame is not None:
        return case.frame
    return build_slice_frame(_DB(), case.period_start, case.period_end, dims=case.dims)


def _DB():
    from experiments.slice_benchmark import DB_PATH
    return DB_PATH


# --------------------------------------------------------------------------- #
# baseline: magnitude ranking
# --------------------------------------------------------------------------- #
def magnitude_predict(case):
    frame = _frame_for(case)
    if frame is None or len(frame) == 0:
        return _pred(None, [], True, 0.0)
    if not is_material(frame, case.kpi or "Revenue"):
        return _pred(None, [], True, 10.0)

    best = None
    for dim in case.dims:
        if dim not in frame.columns:
            continue
        g = frame.groupby(dim, as_index=False)[["f_revenue", "a_revenue"]].sum()
        g["delta"] = g["a_revenue"] - g["f_revenue"]
        total = g["delta"].abs().sum() or 1.0
        g = g.reindex(g["delta"].abs().sort_values(ascending=False).index)
        picked, cum = [], 0.0
        for _, row in g.iterrows():
            picked.append(str(row[dim]))
            cum += abs(row["delta"])
            if cum / total >= T_EP:
                break
        contrib = g[g[dim].astype(str).isin(picked)]["delta"].abs().sum()
        cand = {"dimension": dim, "elements": picked, "contrib": float(contrib),
                "share": float(contrib / total)}
        if best is None or cand["contrib"] > best["contrib"]:
            best = cand

    if best is None:
        return _pred(None, [], True, 0.0)
    confidence = round(min(95.0, 40.0 + 55.0 * best["share"]), 1)
    return _pred(best["dimension"], best["elements"], False, confidence)


# --------------------------------------------------------------------------- #
# adtributor
# --------------------------------------------------------------------------- #
def adtributor_predict(case):
    if case.frame is not None:
        cands = attribute(case.frame, case.dims, case.kpi or "Revenue")
    else:
        r = run_attribution(_DB(), case.kpi, case.period_start, case.period_end, dims=case.dims)
        cands = r.get("candidates", [])
    if not cands:
        return _pred(None, [], True, 15.0)
    top = cands[0]
    # confidence: top surprise vs the next candidate's, scaled
    gap = top["surprise"] - (cands[1]["surprise"] if len(cands) > 1 else 0.0)
    confidence = round(min(95.0, 45.0 + 4000.0 * max(gap, 0.0) + 10.0 * min(top["explanatory_power"], 2.0)), 1)
    return _pred(top["dimension"], top["elements"], False, confidence)


PREDICTORS = {"magnitude": magnitude_predict, "adtributor": adtributor_predict}

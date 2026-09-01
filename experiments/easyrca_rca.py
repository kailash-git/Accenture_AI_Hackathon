"""EasyRCA behind the same predict(case) -> Prediction interface as
baseline_rca. Windows and the anomalous-variable set are attached to each Case
by benchmark.py, so this is a thin adapter."""
from __future__ import annotations

from analytics.easy_rca import find_root_causes


def _pred(predicted, abstained, confidence):
    return {"predicted": list(predicted), "abstained": bool(abstained),
            "confidence": float(confidence)}


def predict(case, graph_path=None):
    if case.normal_df is None or case.anom_df is None or len(case.anom_df) == 0:
        return _pred([], True, 15.0)               # no panel (e.g. cold start)

    r = find_root_causes(
        case.normal_df, case.anom_df,
        anomalous_vars=case.anomalous_vars,        # None for synthetic -> auto-detect
        onsets=case.onsets or None,
    )
    roots = r["root_causes"]
    predicted = [rc["variable"] for rc in roots]

    abstained = (not predicted) and r["status"] in ("insufficient_data", "no_causal_variables")

    # confidence: scale the top effect size onto ~[30, 95]; abstention -> low.
    if not predicted:
        confidence = 15.0 if abstained else 35.0
    else:
        top = roots[0]["effect"]
        confidence = float(min(95.0, 45.0 + 10.0 * min(top, 5.0)))

    return _pred(predicted, abstained, confidence)

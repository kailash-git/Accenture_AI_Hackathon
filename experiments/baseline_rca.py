"""
experiments/baseline_rca.py  (B layout)

The *current* attribution as a predict(case) -> Prediction, scored on the same
benchmark as EasyRCA.

Real cases:  reads the anomaly row's stored PVM (`pvm_json.dominant_driver`)
             and evidence sources -- the same signals the drawer shows today --
             and maps them to a causal variable the way the synthesis does
             (supply evidence -> fill_rate; else the dominant PVM driver).
Synthetic:   the same decision spine (price-vs-volume PVM split + material-move
             evidence flags) computed directly on the two windows.

Prediction = {"predicted": [vars ranked], "abstained": bool, "confidence": float}
"""
from __future__ import annotations

import json
import os
import sqlite3

_ABSTAIN_CONF = 40.0


def _pred(predicted, abstained, confidence):
    return {"predicted": list(predicted), "abstained": bool(abstained),
            "confidence": float(confidence)}


# --------------------------------------------------------------------------- #
# synthetic
# --------------------------------------------------------------------------- #
def _material(normal_df, anom_df, v, rel=0.15, abs_=0.0):
    n = normal_df[v]
    delta = abs(anom_df[v].mean() - n.mean())
    sd = n.std(ddof=0) or 1.0
    return (delta / sd) >= 2.0 and (delta >= rel * max(abs(n.mean()), 1e-9)
                                    or (abs_ > 0 and delta >= abs_))


def _predict_synthetic(case):
    n = case.normal_df.mean()
    a = case.anom_df.mean()
    price_effect = (a["sell_price"] - n["sell_price"]) * n["units"]
    volume_effect = (a["units"] - n["units"]) * n["sell_price"]
    driver = "sell_price" if abs(price_effect) >= abs(volume_effect) else "units"

    supply = _material(case.normal_df, case.anom_df, "fill_rate", rel=0.05, abs_=0.03) or \
        (a["stockout_days"] - n["stockout_days"] >= 1.0)
    marketing = _material(case.normal_df, case.anom_df, "marketing_spend", rel=0.20)
    revenue_moved = _material(case.normal_df, case.anom_df, "revenue", rel=0.15)
    neg_sent = (a["sentiment"] < n["sentiment"]) and abs(a["sentiment"] - n["sentiment"]) >= 0.5

    confidence = min(95.0, 30.0 + 13.0 * sum([supply, marketing, neg_sent, True]))
    if not (revenue_moved or supply or marketing):
        return _pred([], True, confidence)
    if supply:
        predicted = ["fill_rate"]
    elif marketing and not revenue_moved:
        predicted = ["marketing_spend"]
    else:
        predicted = [driver]
    return _pred(predicted, confidence < _ABSTAIN_CONF, confidence)


# --------------------------------------------------------------------------- #
# real -- from the stored anomaly row
# --------------------------------------------------------------------------- #
def _predict_real(case, db_path):
    if not case.node_id or not os.path.exists(db_path):
        return _pred([], True, 20.0)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM anomalies WHERE anomaly_id = ? OR scenario_key = ? LIMIT 1",
                     (case.node_id, case.case_id)).fetchone()
    conn.close()
    if r is None:
        return _pred([], True, 20.0)

    confidence = float(r["confidence"])
    if r["abstained"]:
        return _pred([], True, confidence)

    try:
        pvm = json.loads(r["pvm_json"]) if r["pvm_json"] else {}
    except Exception:
        pvm = {}
    try:
        evidence = json.loads(r["evidence_json"]) if r["evidence_json"] else []
    except Exception:
        evidence = []

    sources = {e.get("source", "") for e in evidence}
    supply = any("supply" in s for s in sources)
    marketing = any("marketing" in s for s in sources)
    driver = (pvm.get("dominant_driver") or "").lower()

    if supply:
        predicted = ["fill_rate"]
    elif driver in ("price", "sell_price"):
        predicted = ["sell_price"]
    elif driver in ("volume", "units"):
        predicted = ["units"]
    elif marketing:
        predicted = ["marketing_spend"]
    else:
        predicted = []
    return _pred(predicted, not predicted or confidence < _ABSTAIN_CONF, confidence)


def predict(case, db_path=None):
    from experiments.benchmark import DB_PATH
    if case.kind == "synthetic":
        return _predict_synthetic(case)
    return _predict_real(case, db_path or DB_PATH)

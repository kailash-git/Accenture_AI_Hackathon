"""
experiments/slice_metrics.py

Scoring for the slice-attribution comparison. Given [(SliceCase, Prediction)]:

  dimension_accuracy   -- picked the right dimension
  element_f1           -- F1 of predicted vs gold elements (0 if dimension wrong)
  exact_set_accuracy   -- dimension right AND element set == gold set
  top1_element         -- predicted elements[0] in gold
  distractor_rate      -- of the cases carrying a magnitude distractor, the
                          fraction where the predictor's set includes it
  abstain_correct_rate -- of the "none" cases, fraction correctly abstained
  mean_conf correct/wrong -- confidence calibration
"""
from __future__ import annotations

import numpy as np


def _f1(pred, gold):
    pred, gold = set(pred), set(gold)
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    tp = len(pred & gold)
    if tp == 0:
        return 0.0
    prec, rec = tp / len(pred), tp / len(gold)
    return 2 * prec * rec / (prec + rec)


def classify(case, pred):
    abstained = pred["abstained"] or not pred["elements"]
    if case.gold_dim is None:                     # "none" case
        return "abstain-correct" if abstained else "abstain-miss"
    if abstained:
        return "miss-abstained"
    dim_ok = pred["dimension"] == case.gold_dim
    f1 = _f1(pred["elements"], case.gold_elements) if dim_ok else 0.0
    if dim_ok and set(pred["elements"]) == set(case.gold_elements):
        return "exact"
    if f1 > 0:
        return "partial"
    return "wrong"


def aggregate(pairs):
    rows = []
    for case, pred in pairs:
        outcome = classify(case, pred)
        dim_ok = (pred["dimension"] == case.gold_dim) if case.gold_dim else None
        f1 = _f1(pred["elements"], case.gold_elements) if dim_ok else (
            0.0 if case.gold_dim else None)
        top1 = (bool(pred["elements"]) and pred["elements"][0] in case.gold_elements
                ) if case.gold_dim else None
        rows.append({
            "case_id": case.case_id, "kind": case.kind, "intervention": case.intervention,
            "gold_dim": case.gold_dim, "gold_elements": sorted(case.gold_elements),
            "pred_dim": pred["dimension"], "pred_elements": pred["elements"],
            "abstained": bool(pred["abstained"] or not pred["elements"]),
            "confidence": pred["confidence"], "outcome": outcome,
            "dim_ok": dim_ok, "element_f1": f1, "top1_element": top1,
            "has_distractor": case.has_distractor,
            "hit_distractor": bool(case.has_distractor and case.distractor_element
                                   and case.distractor_element in pred["elements"]),
        })

    def _metrics(sub):
        n = len(sub)
        if n == 0:
            return {"n": 0}
        attributable = [r for r in sub if r["gold_dim"] is not None]
        na = len(attributable) or 1
        dim_hits = sum(bool(r["dim_ok"]) for r in attributable)
        exact = sum(r["outcome"] == "exact" for r in attributable)
        partial_or_exact = sum(r["outcome"] in ("exact", "partial") for r in attributable)
        top1 = sum(bool(r["top1_element"]) for r in attributable)
        miss = sum(r["outcome"] == "miss-abstained" for r in attributable)
        f1s = [r["element_f1"] for r in attributable if r["element_f1"] is not None]
        none_cases = [r for r in sub if r["gold_dim"] is None]
        abst_ok = sum(r["outcome"] == "abstain-correct" for r in none_cases)
        distractor_cases = [r for r in sub if r["has_distractor"]]
        correct = [r for r in sub if r["outcome"] in ("exact", "partial", "abstain-correct")]
        wrong = [r for r in sub if r["outcome"] in ("wrong", "abstain-miss", "miss-abstained")]
        return {
            "n": n,
            "dimension_accuracy": round(dim_hits / na, 3),
            "exact_set_accuracy": round(exact / na, 3),
            "hit_or_partial": round(partial_or_exact / na, 3),
            "top1_element_accuracy": round(top1 / na, 3),
            "mean_element_f1": round(float(np.mean(f1s)) if f1s else 0.0, 3),
            "miss_abstained_rate": round(miss / na, 3),
            "abstain_correct_rate": round(abst_ok / len(none_cases), 3) if none_cases else None,
            "distractor_pick_rate": (round(sum(r["hit_distractor"] for r in distractor_cases)
                                     / len(distractor_cases), 3) if distractor_cases else None),
            "mean_conf_correct": round(float(np.mean([r["confidence"] for r in correct])), 1) if correct else None,
            "mean_conf_wrong": round(float(np.mean([r["confidence"] for r in wrong])), 1) if wrong else None,
        }

    breakdown = {
        "overall": _metrics(rows),
        "real": _metrics([r for r in rows if r["kind"] == "real"]),
        "synthetic": _metrics([r for r in rows if r["kind"] == "synthetic"]),
        "synthetic_with_distractor": _metrics([r for r in rows if r["has_distractor"]]),
        "synthetic_no_distractor": _metrics([r for r in rows if r["kind"] == "synthetic"
                                             and not r["has_distractor"] and r["gold_dim"]]),
        "synthetic_none": _metrics([r for r in rows if r["intervention"] == "none"]),
    }
    return {"breakdown": breakdown, "rows": rows}

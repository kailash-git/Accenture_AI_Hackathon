"""
experiments/metrics.py

Scoring for the RCA experiment. Given [(Case, Prediction)] pairs, compute the
side-by-side numbers the report is built from. No single pass/fail gate --
every dimension is reported.

Per-case outcome classification:
  * abstain-correct : gold is empty (or behaviour "abstain") and predictor abstained
  * abstain-miss    : should have abstained but attributed something
  * hit             : top prediction is in gold
  * partial         : some prediction (not top) is in gold
  * false-attr      : attributed a specific cause, none of it in gold
  * miss-abstained  : should have attributed, predictor abstained
For behaviour "signal": a hit is "did not false-attribute AND surfaced >=1 var".
"""
from __future__ import annotations

import numpy as np


def _rank_of_gold(predicted, gold):
    for i, v in enumerate(predicted):
        if v in gold:
            return i + 1
    return None


def classify(case, pred):
    gold = set(case.gold)
    predicted = pred["predicted"]
    abstained = pred["abstained"] or not predicted

    if case.gold_behaviour == "abstain" or not gold:
        return "abstain-correct" if abstained else "abstain-miss"

    if case.gold_behaviour == "signal":
        if abstained:
            return "miss-abstained"
        return "hit" if _rank_of_gold(predicted, gold) else "false-attr"

    # behaviour == "attribute"
    if abstained:
        return "miss-abstained"
    rank = _rank_of_gold(predicted, gold)
    if rank == 1:
        return "hit"
    if rank:
        return "partial"
    return "false-attr"


def aggregate(pairs):
    """pairs: list of (Case, Prediction). Returns a dict of metrics."""
    rows = []
    for case, pred in pairs:
        outcome = classify(case, pred)
        rank = _rank_of_gold(pred["predicted"], set(case.gold)) if case.gold else None
        rows.append({
            "case_id": case.case_id, "kind": case.kind,
            "intervention": case.intervention, "behaviour": case.gold_behaviour,
            "gold": sorted(case.gold), "predicted": pred["predicted"],
            "abstained": bool(pred["abstained"] or not pred["predicted"]),
            "confidence": pred["confidence"], "outcome": outcome, "gold_rank": rank,
        })

    def _subset(pred_fn):
        return [r for r in rows if pred_fn(r)]

    def _metrics(subrows):
        n = len(subrows)
        if n == 0:
            return {"n": 0}
        attributable = [r for r in subrows if r["behaviour"] in ("attribute", "signal")]
        na = len(attributable) or 1
        hits = sum(r["outcome"] == "hit" for r in attributable)
        partial = sum(r["outcome"] == "partial" for r in attributable)
        false_attr = sum(r["outcome"] == "false-attr" for r in attributable)
        miss_abst = sum(r["outcome"] == "miss-abstained" for r in attributable)
        attributed = sum(not r["abstained"] for r in attributable)
        # MRR over every attributable case (rank None -> 0), so it is
        # comparable between a single-guess system and a ranked-list system.
        mrr_terms = [(1.0 / r["gold_rank"] if r["gold_rank"] else 0.0) for r in attributable]
        abst_cases = [r for r in subrows if r["behaviour"] == "abstain"]
        abst_ok = sum(r["outcome"] == "abstain-correct" for r in abst_cases)
        correct = [r for r in subrows if r["outcome"] in ("hit", "abstain-correct")]
        wrong = [r for r in subrows if r["outcome"] in ("false-attr", "abstain-miss", "miss-abstained")]
        return {
            "n": n,
            "top1_accuracy": round(hits / na, 3),
            "hit_or_partial": round((hits + partial) / na, 3),
            "false_attribution_rate": round(false_attr / na, 3),
            "miss_abstained_rate": round(miss_abst / na, 3),
            "attribution_rate": round(attributed / na, 3),
            "mrr": round(float(np.mean(mrr_terms)) if mrr_terms else 0.0, 3),
            "abstain_cases": len(abst_cases),
            "abstain_correct_rate": round(abst_ok / len(abst_cases), 3) if abst_cases else None,
            "mean_conf_correct": round(float(np.mean([r["confidence"] for r in correct])), 1) if correct else None,
            "mean_conf_wrong": round(float(np.mean([r["confidence"] for r in wrong])), 1) if wrong else None,
        }

    breakdown = {
        "overall": _metrics(rows),
        "real": _metrics(_subset(lambda r: r["kind"] == "real")),
        "synthetic": _metrics(_subset(lambda r: r["kind"] == "synthetic")),
        "synthetic_structural_shock": _metrics(_subset(lambda r: r["intervention"] == "structural_shock")),
        "synthetic_mechanism_shift": _metrics(_subset(lambda r: r["intervention"] == "mechanism_shift")),
        "synthetic_none": _metrics(_subset(lambda r: r["intervention"] == "none")),
    }
    return {"breakdown": breakdown, "rows": rows}

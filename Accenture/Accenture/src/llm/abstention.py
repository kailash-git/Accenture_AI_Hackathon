"""
src/llm/abstention.py
Confidence- and conflict-driven abstention gate (REQ-05).

The engine must refuse to issue a confident recommendation when:
  1. Statistical confidence is below the 40% threshold, OR
  2. Structured and unstructured evidence directly contradict each other
     (e.g. the KPI looks positive but customer complaints describe a
     billing/quality defect), OR
  3. The KPI movement is material but there is effectively no corroborating
     evidence at all (insufficient evidence, distinct from contradictory
     evidence).

This is evaluated purely from the already-computed statistics/evidence --
no LLM call is needed or used here, by design.
"""

CONFIDENCE_THRESHOLD = 40.0

_NEGATIVE_KEYWORDS = {
    "bug", "overcharge", "complaint", "complaints", "error", "refund",
    "billing", "double", "wrong", "fraud", "delay", "delayed", "stockout",
    "empty", "frustrat", "disappoint", "warning", "issue", "broken",
}


def _has_negative_unstructured_signal(evidence_list):
    hits = []
    for e in evidence_list:
        if not str(e.get("source", "")).startswith("unstructured_feedback"):
            continue
        if e.get("similarityTier") not in ("high", "medium"):
            continue
        text = str(e.get("fullText", "")).lower()
        if any(kw in text for kw in _NEGATIVE_KEYWORDS):
            hits.append(e)
    return hits


def _has_relevant_evidence(evidence_list):
    """
    evidence_reconciler always appends boilerplate structured rows (whatever that
    month's marketing spend / fill rate happened to be) even when they have nothing
    to do with the anomaly -- so "evidence_list is non-empty" is not the same claim
    as "we found something that explains this." Only evidence the reconciler itself
    scored as actually relevant (medium/high cosine similarity to the anomaly type)
    counts toward "this movement is explained."
    """
    return any(e.get("similarityTier") in ("high", "medium") for e in evidence_list)


def evaluate_abstention(confidence, evidence_list, deviation_pct, direction, price_effect=0.0, z_score=None):
    """
    Returns a dict:
        {
            "should_abstain": bool,
            "reason": str,
            "conflicting_signals": list[str],
        }

    `price_effect` (optional) is the PVM price-effect dollar value for the same
    window: a net revenue decline can still be internally contradictory if the
    price component specifically is inflating revenue (e.g. a billing
    overcharge) while customers report the exact opposite experience.

    `z_score` (optional) is the same z-score the statistical detector used to
    flag this movement as an anomaly in the first place (see
    analytics/anomaly_detector.py, threshold=2.0). It is the materiality
    signal for the insufficient-evidence check below -- NOT `deviation_pct`.
    A raw percent move is a poor proxy for "how anomalous is this": in a
    low-variance baseline (near-flat historical sales), even a tiny percent
    swing can be many standard deviations out, which is exactly the loud,
    high-confidence, evidence-free case this check exists to catch. Using
    deviation_pct >= 5% as the gate let one such case (a -3.8% move that was
    still a z=-7.5 statistical outlier) slip through non-abstained with only
    boilerplate "low"-tier evidence attached. If z_score isn't supplied,
    fall back to treating the movement as material (conservative: prefer an
    abstention banner over an unsupported recommendation).
    """
    conflicting_signals = []

    low_confidence = confidence < CONFIDENCE_THRESHOLD

    price_signal_positive = price_effect > 0
    positive_signal = direction == "UP" or deviation_pct > 0 or price_signal_positive
    negative_hits = _has_negative_unstructured_signal(evidence_list)

    if positive_signal and negative_hits:
        # Naming *which* structured number looks positive (price effect specifically,
        # vs. overall revenue) is the difference between a reader having to take the
        # contradiction on faith and being able to see it: a billing-overcharge bug
        # makes the price-effect dollar figure look like a legitimate gain -- e.g. this
        # is the exact case that produces "price effect +$628 (26%), looks like a win"
        # while 2 customers are simultaneously reporting they were charged double.
        if price_signal_positive:
            structured_desc = f"the price effect specifically is positive (+${price_effect:,.0f})"
        else:
            structured_desc = "the overall structured KPI movement is positive"
        conflicting_signals.append(
            f"Structured data says {structured_desc}, but "
            f"{len(negative_hits)} unstructured record(s) in the same window explicitly describe a "
            "billing/overcharge or service defect -- a metric that looks positive because of a pricing "
            "bug is not a legitimate win, so these two signals contradict each other."
        )

    # Deliberately independent of `confidence`: that score measures how statistically
    # unusual the movement is (from the z-score), not how well we can explain it -- a
    # huge, high-confidence, totally unexplained swing is exactly the case this branch
    # exists to catch, not one a high-confidence gate should be excluding.
    is_material = True if z_score is None else abs(z_score) >= 2.0
    insufficient_evidence = (
        not conflicting_signals
        and is_material
        and not _has_relevant_evidence(evidence_list)
    )

    should_abstain = low_confidence or bool(conflicting_signals) or insufficient_evidence

    if not should_abstain:
        return {"should_abstain": False, "reason": "", "conflicting_signals": [], "category": None}

    # Priority order also controls which message the caller sees when more than one
    # condition fires: contradiction is the most specific/actionable diagnosis, then a
    # genuinely low statistical confidence, then generic insufficient evidence -- so a
    # low-confidence case is never mislabeled "insufficient evidence" just because it
    # also happens to lack relevant evidence.
    if conflicting_signals:
        category = "contradictory_evidence"
        reason = "Abstain: Contradictory evidence. " + " ".join(conflicting_signals)
    elif low_confidence:
        category = "low_confidence"
        reason = (
            f"Abstain: Statistical confidence ({confidence:.0f}%) is below the "
            f"{CONFIDENCE_THRESHOLD:.0f}% threshold required to issue an automated recommendation."
        )
    else:
        category = "insufficient_evidence"
        reason = (
            f"Abstain: Insufficient evidence. A {abs(deviation_pct) * 100:.1f}% KPI movement "
            f"(z={z_score:.2f}) was detected but no corroborating structured or unstructured records "
            "were found to explain it." if z_score is not None else
            f"Abstain: Insufficient evidence. A {abs(deviation_pct) * 100:.1f}% KPI movement was "
            "detected but no corroborating structured or unstructured records were found to explain it."
        )

    return {
        "should_abstain": True,
        "reason": reason,
        "conflicting_signals": conflicting_signals,
        "category": category,
    }

"""
src/analytics/evidence_signal.py
Evidence-driven anomaly discovery -- the second, independent detection signal alongside
AnomalyDetector's statistical z-score scan (REQ: "unstructured data must be capable of
independently triggering an anomaly").

Concretely: instead of only ever *explaining* a candidate that the z-score scan (or a
hardcoded scenario list) already admitted, this module lets real customer/support
records with no predetermined KPI or anomaly type *create* a candidate on their own --
"a pricing complaint mentions FOODS_3_586 in TX around May 2013" is enough to warrant
checking what actually happened to that item/region/month's numbers, independent of
whether that period would ever have cleared the statistical threshold.

This module deliberately knows nothing about Revenue/GrossMarginPercent/InventoryTurnover
specifically -- it only clusters and scores unstructured evidence into candidate
(item_id, state_id, period) windows. Checking what each of the three KPIs' real numbers
look like in that window, and deciding STATISTICAL/EVIDENCE_DRIVEN/HYBRID per KPI, is the
caller's job (scripts/generate_mock_data.py) using AnomalyDetector.compute_period_stats.
"""

import sqlite3

import pandas as pd

# All evidence-signal thresholds/weights live here, in one place, rather than scattered
# as inline magic numbers through the scoring logic below.
EVIDENCE_CONFIG = {
    # Per-factor weights (need not sum to 1.0 -- normalized against their own max below).
    "weight_item_match": 1.0,
    "weight_region_match": 1.0,
    "weight_temporal_proximity": 1.5,
    "weight_category_relevance": 2.0,
    "weight_record_count": 1.5,
    "weight_source_reliability": 1.0,
    # A window needs at least this many days of cushion around the candidate month to
    # count a record as "temporally aligned" at all (mirrors evidence_reconciler.py's
    # own -5/+10 day window so the two modules agree on what "aligned" means).
    "temporal_window_days_before": 5,
    "temporal_window_days_after": 10,
    # Records need this cosine-similarity to their best-matching category to count as
    # relevant evidence at all (same "medium" bar evidence_reconciler.py itself uses).
    "min_category_similarity": 0.3,
    # A single very strong record needs at least this many supporting records to reach
    # "full credit" on the record-count factor -- more independent records raises
    # confidence, but one strong record shouldn't be starved just for being alone.
    "record_count_full_credit_at": 2,
    "source_reliability": {
        "support ticket": 1.0,   # an internal operational report
        "customer review": 0.8,  # a single external voice
    },
    # Final classification cutoffs on the normalized (0-1) evidence_score.
    "strong_threshold": 0.65,
    "moderate_threshold": 0.40,
}


def _classify_score(score, config):
    if score >= config["strong_threshold"]:
        return "strong"
    if score >= config["moderate_threshold"]:
        return "moderate"
    return "weak"


def discover_evidence_candidates(db_path, config=None):
    """
    Scans unstructured_feedback for every real (item_id, state_id) pair present, groups
    each pair's records by calendar month, and scores each (item, state, month) window
    as a candidate anomaly window purely from the evidence itself -- no predetermined
    KPI, anomaly type, or (item, region, month) list is assumed.

    Returns a list of candidate dicts (weak-scoring windows are dropped, not returned --
    "weak or unrelated evidence -> ignore" per the detection spec):
        {
            "item_id", "state_id", "period" (YYYY-MM),
            "evidence_score" (0-1), "classification" ("strong"|"moderate"),
            "category" (best-matching vocab category, e.g. "supply"/"billing"/"pricecut"),
            "supporting_records": [{"source", "date", "text", "similarity", "tier"}, ...],
        }
    """
    cfg = config or EVIDENCE_CONFIG
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT feedback_id, item_id, state_id, source, text_content, date FROM unstructured_feedback",
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M")

    from retrieval.evidence_reconciler import EvidenceReconciler
    reconciler = EvidenceReconciler(db_path)

    candidates = []
    for (item_id, state_id, month), group in df.groupby(["item_id", "state_id", "month"]):
        candidate = _score_candidate_window(reconciler, item_id, state_id, month, group, cfg)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def _score_candidate_window(reconciler, item_id, state_id, month, group, cfg):
    period_start = month.to_timestamp(how="start").strftime("%Y-%m-%d")
    period_end = month.to_timestamp(how="end").strftime("%Y-%m-%d")

    # anomaly_type_key intentionally not one of the 4 known core keys, so
    # reconcile_evidence's evidence_reconciler._best_category_similarity() infers the
    # category from the text itself instead of assuming one.
    evidence_res = reconciler.reconcile_evidence(
        item_id=item_id, state_id=state_id,
        period_start=period_start, period_end=period_end,
        anomaly_type_key=f"evidence-{item_id}-{state_id}-{month}",
    )

    relevant = [
        e for e in evidence_res["evidence"]
        if str(e.get("source", "")).startswith("unstructured_feedback")
        and e.get("similarity", 0.0) >= cfg["min_category_similarity"]
    ]
    if not relevant:
        return None

    # -- Factor scores, each normalized to [0, 1] --
    item_match_score = 1.0    # candidate was generated from this exact item's own records
    region_match_score = 1.0  # ...and this exact region's own records
    category_relevance_score = min(1.0, sum(e["similarity"] for e in relevant) / len(relevant))
    record_count_score = min(1.0, len(relevant) / cfg["record_count_full_credit_at"])

    source_scores = []
    for e in relevant:
        src = str(e.get("source", "")).replace("unstructured_feedback (", "").rstrip(")")
        source_scores.append(cfg["source_reliability"].get(src, 0.7))
    source_reliability_score = sum(source_scores) / len(source_scores)

    # Temporal proximity: how close the supporting records sit to the window's own
    # calendar month (0 days -> 1.0, decaying to 0 at the edge of the shared window).
    window_span_days = max(1, cfg["temporal_window_days_before"] + cfg["temporal_window_days_after"])
    month_mid = month.to_timestamp(how="start") + pd.Timedelta(days=15)
    proximities = []
    for _, row in group.iterrows():
        days_off = abs((row["date"] - month_mid).days)
        proximities.append(max(0.0, 1.0 - days_off / window_span_days))
    temporal_score = sum(proximities) / len(proximities) if proximities else 0.0

    weighted_sum = (
        cfg["weight_item_match"] * item_match_score
        + cfg["weight_region_match"] * region_match_score
        + cfg["weight_temporal_proximity"] * temporal_score
        + cfg["weight_category_relevance"] * category_relevance_score
        + cfg["weight_record_count"] * record_count_score
        + cfg["weight_source_reliability"] * source_reliability_score
    )
    total_weight = (
        cfg["weight_item_match"] + cfg["weight_region_match"] + cfg["weight_temporal_proximity"]
        + cfg["weight_category_relevance"] + cfg["weight_record_count"] + cfg["weight_source_reliability"]
    )
    evidence_score = weighted_sum / total_weight
    classification = _classify_score(evidence_score, cfg)
    if classification == "weak":
        return None

    best_category = max(
        relevant, key=lambda e: e["similarity"]
    )  # representative record for category labeling below
    inferred_category, _ = reconciler._best_category_similarity(best_category["fullText"])

    return {
        "item_id": item_id,
        "state_id": state_id,
        "period": str(month),
        "evidence_score": round(evidence_score, 4),
        "classification": classification,
        "category": inferred_category,
        "supporting_records": [
            {"source": e["source"], "date": e["date"], "text": e["fullText"], "similarity": e["similarity"], "tier": e["similarityTier"]}
            for e in relevant
        ],
    }

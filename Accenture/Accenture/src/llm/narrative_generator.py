"""
src/llm/narrative_generator.py
Persona-aware narrative + structured-action synthesis (REQ-04, REQ-05, REQ-06).

Every figure in the output is read directly from the already-computed
pvm_results / evidence_results / graph_results -- nothing is hardcoded.
Two personas are always produced from a single call: vp_sales (executive,
PVM/financial framing, <=250 words) and supply_planner (analyst,
SKU/warehouse/logistics framing, <=400 words), per docs/persona_profiles.md.

Generation is deterministic by construction: the template engine below
computes every headline/summary/action field straight from the numbers
passed in, and that deterministic output is ALWAYS the source of truth for
the structured recommended_action (dollar figures, owner, monitoring plan).
If OPENAI_API_KEY is available, a single cached call (one call covers BOTH
personas) is used only to polish the prose fields (headline/summary/
synthesis); the recommended_action/abstention payload is never touched by
the LLM. If the call fails, is unavailable, or its output fails schema/
entitlement validation, the deterministic prose is served unchanged --
the pipeline never depends on the LLM being reachable.
"""

import json
import os
import re

from pydantic import ValidationError

from . import llm_client
from .abstention import evaluate_abstention
from .schema_parser import PersonaNarrative

_CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "schemas",
    "semantic_contract.json",
)

_VP_FORBIDDEN_PATTERNS = [r"\bwarehouse\b", r"\bbay\s*\d+\b", r"\bcarrier\b", r"\blead time\b", r"\bSKU\b"]
_PLANNER_FORBIDDEN_PATTERNS = [
    r"\bgross margin\b", r"\bmargin\b", r"\bCOGS\b", r"\bcost of goods sold\b",
    r"\bmarketing spend\b", r"\bcampaign roi\b", r"\brevenue\b",
]


def _load_contract():
    try:
        with open(_CONTRACT_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _region_name(state_id, contract):
    return contract.get("semantic_layer", {}).get("mappings", {}).get("state_to_region", {}).get(state_id, state_id)


def _fmt_money(v):
    sign = "-" if v < 0 else "+"
    return f"{sign}${abs(v):,.0f}"


def _fmt_money_abs(v):
    return f"${abs(v):,.0f}"


_KPI_DISPLAY_NAME = {"Revenue": "Revenue", "GrossMarginPercent": "Gross Margin %", "InventoryTurnover": "Inventory Turnover"}


def _kpi_display_name(kpi_name):
    return _KPI_DISPLAY_NAME.get(kpi_name, kpi_name)


def _fmt_kpi_value(kpi_name, v):
    """actual_value/baseline_value are stored in the units AnomalyDetector computed them
    in: a revenue dollar figure, a margin fraction (0.296, not 29.6), or a turnover ratio
    -- each needs its own display format, not the dollar formatter Revenue uses."""
    if kpi_name == "GrossMarginPercent":
        return f"{v * 100:.1f}%"
    if kpi_name == "InventoryTurnover":
        return f"{v:.2f}x"
    return _fmt_money_abs(v)


def _redact(text, persona, item_id=None):
    if not text:
        return text
    patterns = list(_VP_FORBIDDEN_PATTERNS) if persona == "vp_sales" else list(_PLANNER_FORBIDDEN_PATTERNS)
    if persona == "vp_sales" and item_id:
        patterns.append(re.escape(item_id))
    redacted = text
    for p in patterns:
        redacted = re.sub(p, "[restricted]", redacted, flags=re.IGNORECASE)
    return redacted


class NarrativeGenerator:
    def __init__(self, use_llm: bool = True):
        self.contract = _load_contract()
        self.use_llm = bool(use_llm) and llm_client.is_available()

    # ------------------------------------------------------------------ #
    # Public entrypoint
    # ------------------------------------------------------------------ #
    def generate_bundle(self, anomaly, pvm_results, evidence_results, graph_results):
        """
        Returns:
            {
              "vp_sales": {...validated PersonaNarrative dict...},
              "supply_planner": {...},
              "logistics": {...card for the dashboard...},
              "telemetry": {tokens_in, tokens_out, cost_usd, latency_s, model, calls, generation_method, llm_error}
            }
        """
        if anomaly.get("sparse_history"):
            # Insufficient rolling history is a distinct case from "no evidence" or
            # "low confidence" -- the engine still has something useful to say
            # (establish a baseline) so it should not abstain.
            abst = {"should_abstain": False, "reason": "", "conflicting_signals": []}
        else:
            abst = evaluate_abstention(
                confidence=anomaly["confidence"],
                evidence_list=evidence_results.get("evidence", []),
                deviation_pct=anomaly["deviation_pct"],
                direction=anomaly["direction"],
                price_effect=pvm_results.get("price", {}).get("val", 0.0),
                z_score=anomaly.get("z_score"),
            )

        if anomaly.get("sparse_history"):
            deterministic_bundle = self._sparse_history_bundle(anomaly, evidence_results, graph_results)
        else:
            deterministic_bundle = self._deterministic_bundle(anomaly, pvm_results, evidence_results, graph_results, abst)
        telemetry = {
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "latency_s": 0.0,
            "model": None, "calls": 0, "generation_method": "deterministic", "llm_error": None,
        }

        bundle = deterministic_bundle
        if self.use_llm and not abst["should_abstain"]:
            polished, llm_telemetry = self._try_llm_polish(anomaly, pvm_results, evidence_results, deterministic_bundle)
            telemetry.update(llm_telemetry)
            if polished is not None:
                bundle = polished

        # Final validation pass -- guarantees every anomaly served always matches the schema.
        validated = {
            persona: PersonaNarrative(**bundle[persona]).model_dump()
            for persona in ("vp_sales", "supply_planner")
        }

        return {
            "vp_sales": validated["vp_sales"],
            "supply_planner": validated["supply_planner"],
            "logistics": self._logistics_card(evidence_results, graph_results),
            "telemetry": telemetry,
        }

    # ------------------------------------------------------------------ #
    # Deterministic template engine (always the source of quantitative truth)
    # ------------------------------------------------------------------ #
    def _deterministic_bundle(self, anomaly, pvm_results, evidence_results, graph_results, abst):
        if abst["should_abstain"]:
            return {
                "vp_sales": self._abstention_narrative("vp_sales", anomaly, abst),
                "supply_planner": self._abstention_narrative("supply_planner", anomaly, abst),
            }

        vp_headline, vp_summary, vp_title, vp_body = self._vp_prose(anomaly, pvm_results, evidence_results)
        pl_headline, pl_summary, pl_title, pl_body = self._planner_prose(anomaly, evidence_results, graph_results)

        return {
            "vp_sales": {
                "persona": "vp_sales",
                "headline": vp_headline,
                "summary": vp_summary,
                "synthesis_title": vp_title,
                "synthesis_body": vp_body,
                "recommended_action": self._vp_action(anomaly, pvm_results, evidence_results),
                "abstention": None,
                "generation_method": "deterministic",
            },
            "supply_planner": {
                "persona": "supply_planner",
                "headline": pl_headline,
                "summary": pl_summary,
                "synthesis_title": pl_title,
                "synthesis_body": pl_body,
                "recommended_action": self._planner_action(anomaly, evidence_results, graph_results),
                "abstention": None,
                "generation_method": "deterministic",
            },
        }

    def _abstention_narrative(self, persona, anomaly, abst):
        region = _region_name(anomaly["state_id"], self.contract)
        # The headline names the actual reason the engine withheld a recommendation --
        # a high-confidence, evidence-free anomaly must not read "confidence insufficient"
        # when its statistical confidence is in fact high; the gap is in evidence, not stats.
        category = abst.get("category")
        if category == "insufficient_evidence":
            headline = f"Abstained: {abs(anomaly['deviation_pct']) * 100:.0f}% movement detected with no corroborating evidence"
        elif category == "contradictory_evidence":
            headline = "Abstained: structured and unstructured evidence contradict each other"
        else:
            headline = f"Abstained: confidence {anomaly['confidence']:.0f}% below the automated recommendation threshold"
        synthesis_title = f"Engine abstained for {anomaly['item_id']} in {region}"
        synthesis_body = (
            f"{abst['reason']} Per policy, the engine withholds an automated recommendation under low "
            "confidence or contradictory evidence and flags this case for manual analyst review instead "
            "of guessing."
        )
        return {
            "persona": persona,
            "headline": headline,
            "summary": abst["reason"],
            "synthesis_title": synthesis_title,
            "synthesis_body": synthesis_body,
            "recommended_action": None,
            "abstention": {
                "abstained": True,
                "reason": abst["reason"],
                "confidence": float(anomaly["confidence"]),
                "conflicting_signals": abst["conflicting_signals"],
            },
            "generation_method": "deterministic",
        }

    def _sparse_history_bundle(self, anomaly, evidence_results, graph_results):
        """
        Dedicated path for newly launched items/categories with fewer than 3 prior
        periods of history -- the statistical detector cannot compute a reliable
        baseline, so instead of a z-score-driven anomaly we surface an explicit
        "insufficient baseline" narrative with a concrete recommendation to defer
        automated alerting rather than either hallucinating a false anomaly or
        abstaining with nothing useful to say.
        """
        item_id = anomaly["item_id"]
        region = _region_name(anomaly["state_id"], self.contract)
        actual_val = anomaly["actual_value"]

        vp_headline = f"New item {item_id} launched in {region}: insufficient history for a statistical baseline"
        vp_summary = (
            f"{item_id} has fewer than 3 prior monthly periods of sales history in {region}, so the rolling "
            f"z-score baseline cannot be computed reliably. Observed revenue this period: {_fmt_money_abs(actual_val)}."
        )
        vp_body = (
            "Statistical anomaly detection requires a minimum rolling window of prior periods to establish a "
            "stable baseline. Flagging early-launch volume swings as anomalies against an empty baseline would "
            "produce false positives, so this item is instead marked for baseline establishment."
        )
        vp_action = {
            "driver": f"Sparse launch history for {item_id} in {region}",
            "controllable_lever": "Statistical monitoring configuration",
            "action": f"Bypass automated z-score alerting for {item_id} until a minimum 3-period baseline accumulates; track velocity manually.",
            "expected_impact": "Prevent false-positive anomaly alerts during the early-launch ramp period.",
            "owner": "VP of Retail Sales",
            "confidence": float(anomaly["confidence"]),
            "monitoring_plan": f"Re-enable automated detection for {item_id} once 3 monthly periods of history are available.",
        }

        pl_headline = f"{item_id} launch in {anomaly['state_id']}: baseline not yet established"
        graph_hits = graph_results.get("hops", [])
        pl_summary = (
            f"{len(graph_hits)} related record(s) found in the knowledge graph for {item_id}/{anomaly['state_id']}. "
            "No statistical baseline exists yet for this SKU in this region."
        )
        pl_body = (
            f"Inventory and demand planning for {item_id} should rely on manual velocity tracking and "
            "planner judgment until sufficient historical data accumulates for automated reorder-point calibration."
        )
        pl_action = {
            "driver": f"Sparse launch history for {item_id} in {anomaly['state_id']}",
            "controllable_lever": "Manual reorder point setting",
            "action": f"Set an initial manual reorder point for {item_id} based on comparable-category launch velocity until automated calibration is available.",
            "expected_impact": "Avoid early-launch stockouts or overstock while the automated baseline builds.",
            "owner": "Regional Supply Chain Planner",
            "confidence": float(anomaly["confidence"]),
            "monitoring_plan": f"Review {item_id} inventory position weekly until a 3-period sales baseline is established.",
        }

        return {
            "vp_sales": {
                "persona": "vp_sales", "headline": vp_headline, "summary": vp_summary,
                "synthesis_title": f"Baseline establishment required for {item_id}",
                "synthesis_body": vp_body, "recommended_action": vp_action, "abstention": None,
                "generation_method": "deterministic",
            },
            "supply_planner": {
                "persona": "supply_planner", "headline": pl_headline, "summary": pl_summary,
                "synthesis_title": f"Baseline establishment required for {item_id}",
                "synthesis_body": pl_body, "recommended_action": pl_action, "abstention": None,
                "generation_method": "deterministic",
            },
        }

    def _vp_prose_generic_kpi(self, anomaly, evidence_results, region):
        """
        GrossMarginPercent/InventoryTurnover narrative path -- Price-Volume-Mix is a
        Revenue-specific decomposition (semantic_contract.json's "driver_method" for
        these two KPIs states this explicitly), so this states the movement honestly
        in its own units instead of forcing it through Revenue's PVM template (which
        would dollar-format a margin percentage or a turnover ratio and invent a fake
        "dominant PVM driver" for a KPI that was never decomposed).
        """
        kpi_name = anomaly.get("kpi_name", "Revenue")
        kpi_label = _kpi_display_name(kpi_name)
        deviation_pct = anomaly["deviation_pct"] * 100
        direction_word = "improved" if deviation_pct >= 0 else "declined"
        headline = f"{kpi_label} {direction_word} {abs(deviation_pct):.1f}% in {region}"

        summary = (
            f"{region} {kpi_label} moved from {_fmt_kpi_value(kpi_name, anomaly['baseline_value'])} baseline to "
            f"{_fmt_kpi_value(kpi_name, anomaly['actual_value'])} ({abs(deviation_pct):.1f}% "
            f"{'improvement' if deviation_pct >= 0 else 'decline'}, z={anomaly['z_score']:.2f}). This prototype "
            f"does not decompose {kpi_label} into sub-drivers -- Revenue is the only KPI with a Price-Volume-Mix "
            f"breakdown; see the evidence trail below for contributing context instead."
        )

        synthesis_title = f"{kpi_label} {direction_word} in {region} ({abs(deviation_pct):.1f}%)"

        body_parts = [
            f"{kpi_label} is detected via the same rolling z-score engine as Revenue "
            f"(analytics/anomaly_detector.py), but explained through retrieved evidence rather than a "
            f"Price-Volume-Mix decomposition in this prototype."
        ]
        marketing_total = evidence_results.get("marketing_total", 0.0)
        if marketing_total > 0:
            body_parts.append(f"Regional marketing spend during this window totaled {_fmt_money_abs(marketing_total)}.")
        evidence_count = len(
            [e for e in evidence_results.get("evidence", []) if str(e.get("source", "")).startswith("unstructured_feedback")]
        )
        if evidence_count > 0:
            body_parts.append(f"{evidence_count} corroborating customer/support record(s) were found for this period.")
        else:
            body_parts.append("No corroborating customer or support records were found for this period.")

        return headline, summary, synthesis_title, " ".join(body_parts)

    def _vp_action_generic_kpi(self, anomaly, evidence_results, region):
        kpi_name = anomaly.get("kpi_name", "Revenue")
        kpi_label = _kpi_display_name(kpi_name)
        deviation_pct = anomaly["deviation_pct"]
        fill_rate = evidence_results.get("supply_indicators", {}).get("fill_rate")

        if fill_rate is not None and fill_rate < 0.90 and deviation_pct < 0:
            driver = f"Supply-side constraint coinciding with the {kpi_label} decline in {region} (fill rate {fill_rate:.2f})"
            lever = "Inventory replenishment prioritization"
            action = f"Investigate the warehouse fill-rate shortfall behind this {kpi_label} movement in {region} and coordinate with supply planning."
            impact = f"Restore {kpi_label} toward its pre-anomaly baseline as fill rate normalizes."
        else:
            driver = f"{kpi_label} moved {abs(deviation_pct) * 100:.1f}% in {region} with no identified supply-side cause"
            lever = "Manual analyst review"
            action = f"Route this {kpi_label} movement in {region} to a business analyst for root-cause review -- no automated driver decomposition is available for this KPI in the current prototype."
            impact = f"Establish the underlying cause of the {kpi_label} movement before committing to a corrective lever."

        return {
            "driver": driver,
            "controllable_lever": lever,
            "action": action,
            "expected_impact": impact,
            "owner": "VP of Retail Sales",
            "confidence": float(anomaly["confidence"]),
            "monitoring_plan": f"Track weekly {kpi_label} for {region} against the pre-anomaly baseline for the next 4 weeks.",
        }

    def _vp_prose(self, anomaly, pvm_results, evidence_results):
        region = _region_name(anomaly["state_id"], self.contract)
        if anomaly.get("kpi_name", "Revenue") != "Revenue":
            return self._vp_prose_generic_kpi(anomaly, evidence_results, region)
        deviation_pct = anomaly["deviation_pct"] * 100
        direction_word = "grew" if deviation_pct >= 0 else "declined"
        headline = f"Revenue {direction_word} {abs(deviation_pct):.1f}% in {region}"

        effects = {k: pvm_results[k]["val"] for k in ("volume", "price", "mix")}
        dominant = pvm_results.get("dominant_driver") or max(effects, key=lambda k: abs(effects[k]))
        opposing = bool(pvm_results.get("drivers_opposing"))

        if opposing:
            driver_clause = (f"Volume and price moved in opposite directions; the {dominant} effect "
                             f"({_fmt_money(effects.get(dominant, 0.0))}) was the larger force.")
        else:
            driver_clause = (f"{dominant.capitalize()} effect ({pvm_results[dominant]['pct']} of baseline) "
                             f"is the dominant driver.")
        summary = (
            f"{region} revenue moved from {_fmt_money_abs(anomaly['baseline_value'])} baseline to "
            f"{_fmt_money_abs(anomaly['actual_value'])} ({abs(deviation_pct):.1f}% "
            f"{'growth' if deviation_pct >= 0 else 'decline'}, z={anomaly['z_score']:.2f}). "
            f"{driver_clause}"
        )

        synthesis_title = f"{dominant.capitalize()}-driven {'growth' if deviation_pct >= 0 else 'decline'} in {region}: {pvm_results[dominant]['expl']}"

        body_parts = [
            pvm_results.get("driver_summary")
            or (f"Price-Volume-Mix decomposition: volume {pvm_results['volume']['pct']}, "
                f"price {pvm_results['price']['pct']}, mix {pvm_results['mix']['pct']} of baseline revenue.")
        ]
        marketing_total = evidence_results.get("marketing_total", 0.0)
        if marketing_total > 0:
            body_parts.append(f"Regional marketing spend during this window totaled {_fmt_money_abs(marketing_total)}.")
        evidence_count = len(
            [e for e in evidence_results.get("evidence", []) if str(e.get("source", "")).startswith("unstructured_feedback")]
        )
        if evidence_count > 0:
            body_parts.append(f"{evidence_count} corroborating customer/support record(s) were found for this period.")
        else:
            body_parts.append("No corroborating customer or support records were found for this period.")

        return headline, summary, synthesis_title, " ".join(body_parts)

    def _vp_action(self, anomaly, pvm_results, evidence_results):
        region = _region_name(anomaly["state_id"], self.contract)
        if anomaly.get("kpi_name", "Revenue") != "Revenue":
            return self._vp_action_generic_kpi(anomaly, evidence_results, region)
        effects = {k: pvm_results[k]["val"] for k in ("volume", "price", "mix")}
        dominant = max(effects, key=lambda k: abs(effects[k]))
        dominant_val = effects[dominant]
        fill_rate = evidence_results.get("supply_indicators", {}).get("fill_rate")

        if dominant == "volume" and dominant_val < 0:
            if fill_rate is not None and fill_rate < 0.90:
                driver = f"Supply-constrained volume contraction in {region} (fill rate {fill_rate:.2f})"
                lever = "Inventory replenishment prioritization"
                action = f"Authorize emergency replenishment allocation for the affected category in {region} and reallocate buffer stock from an adjacent region."
                impact = f"Recover approximately {_fmt_money_abs(dominant_val)} in run-rate revenue as fill rate normalizes."
            else:
                driver = f"Demand softening in {region} (volume effect {_fmt_money(dominant_val)})"
                lever = "Regional promotional / marketing spend"
                action = f"Launch a targeted promotional campaign in {region} to rebuild sales velocity."
                impact = f"Offset an estimated {_fmt_money_abs(dominant_val)} of lost volume-driven revenue."
        elif dominant == "volume":
            driver = f"Volume-driven revenue lift in {region} ({_fmt_money(dominant_val)})"
            lever = "Demand capture / inventory continuity"
            action = f"Maintain current stock allocation in {region} and monitor for stockout risk as velocity increases."
            impact = f"Protect the {_fmt_money_abs(dominant_val)} incremental volume gain from a stockout-driven reversal."
        elif dominant == "price" and dominant_val < 0:
            driver = f"Price/discount-driven margin compression in {region} ({_fmt_money(dominant_val)})"
            lever = "Pricing policy"
            action = f"Review markdown depth in {region} and evaluate reverting or tapering the current discount."
            impact = f"Recover up to {_fmt_money_abs(dominant_val)} in price-driven revenue."
        elif dominant == "price":
            driver = f"Favorable price realization in {region} ({_fmt_money(dominant_val)})"
            lever = "Pricing strategy"
            action = f"Sustain the current pricing position in {region} and evaluate extending it to comparable categories."
            impact = f"Protect and extend the {_fmt_money_abs(dominant_val)} price-driven gain."
        else:
            direction_word = "Unfavorable" if dominant_val < 0 else "Favorable"
            driver = f"{direction_word} product mix shift in {region} ({_fmt_money(dominant_val)})"
            lever = "Category assortment / shelf placement"
            action = f"Reassess category shelf-space allocation in {region} to rebalance toward higher-margin SKUs."
            impact = f"Address the {_fmt_money_abs(dominant_val)} mix-driven variance."

        return {
            "driver": driver,
            "controllable_lever": lever,
            "action": action,
            "expected_impact": impact,
            "owner": "VP of Retail Sales",
            "confidence": float(anomaly["confidence"]),
            "monitoring_plan": f"Track weekly Revenue and Gross Margin % for {region} against the pre-anomaly baseline for the next 4 weeks.",
        }

    def _planner_prose(self, anomaly, evidence_results, graph_results):
        item_id = anomaly["item_id"]
        state_id = anomaly["state_id"]
        supply = evidence_results.get("supply_indicators", {})
        fill_rate = supply.get("fill_rate")
        stockout_days = supply.get("stockout_days")
        warehouse_sku = supply.get("warehouse_sku")

        if fill_rate is not None:
            headline = f"Fill rate at {warehouse_sku} is {fill_rate:.2f} ({stockout_days} stockout day(s))"
        else:
            headline = f"Inventory signal detected for {item_id} in {state_id}"

        graph_hits = graph_results.get("hops", [])
        max_hop = max([h["hops"] for h in graph_hits], default=0)
        summary = f"{len(graph_hits)} related record(s) found within {max_hop} hop(s) of {item_id}/{state_id} in the knowledge graph."

        synthesis_title = f"Operational trace for {item_id} in {state_id}"
        body_parts = []
        if fill_rate is not None:
            body_parts.append(f"Warehouse {warehouse_sku} reported fill_rate={fill_rate:.2f} with {stockout_days} stockout day(s) this period.")
        for h in graph_hits[:3]:
            body_parts.append(f"[{h['hops']}-hop, {h['temporal_role']}] {h['source']} ({h['date']}): {h['text'][:140]}")
        if not body_parts:
            body_parts.append("No structured supply signal or linked feedback records were found for this SKU/region in the current window.")

        return headline, summary, synthesis_title, " ".join(body_parts)

    def _planner_action(self, anomaly, evidence_results, graph_results):
        item_id = anomaly["item_id"]
        state_id = anomaly["state_id"]
        supply = evidence_results.get("supply_indicators", {})
        fill_rate = supply.get("fill_rate")
        stockout_days = supply.get("stockout_days")
        warehouse_sku = supply.get("warehouse_sku", item_id)

        if fill_rate is not None and fill_rate < 0.90:
            driver = f"Fill-rate constraint at {warehouse_sku} ({fill_rate:.2f}, {stockout_days} stockout day(s))"
            lever = "Safety stock / inter-warehouse transfer"
            action = f"Trigger an emergency stock transfer of {item_id} into {warehouse_sku} from the nearest warehouse with surplus, and raise the safety stock threshold."
            impact = f"Restore fill rate above 0.95 at {warehouse_sku} within the next replenishment cycle."
            monitoring = f"Monitor daily fill_rate and stockout_days for {warehouse_sku}/{item_id} until fill rate exceeds 0.95 for 5 consecutive days."
        else:
            graph_hits = len(graph_results.get("hops", []))
            driver = f"Demand/velocity shift for {item_id} in {state_id} ({graph_hits} related record(s) found)"
            lever = "Reorder point calibration"
            action = f"Recalibrate the reorder point for {item_id} in {state_id} based on the latest 8-period velocity trend."
            impact = "Reduce the risk of a future stockout or overstock event for this SKU."
            monitoring = f"Review inventory_on_hand versus reorder point for {item_id} weekly for the next 4 weeks."

        return {
            "driver": driver,
            "controllable_lever": lever,
            "action": action,
            "expected_impact": impact,
            "owner": "Regional Supply Chain Planner",
            "confidence": float(anomaly["confidence"]),
            "monitoring_plan": monitoring,
        }

    def _logistics_card(self, evidence_results, graph_results):
        supply = evidence_results.get("supply_indicators", {})
        fill_rate = supply.get("fill_rate")
        stockout_days = supply.get("stockout_days")
        warehouse_sku = supply.get("warehouse_sku")
        graph_hits = graph_results.get("hops", [])

        if fill_rate is not None:
            status = "Disrupted" if fill_rate < 0.9 else "Nominal"
            status_class = "critical" if fill_rate < 0.9 else "active"
            desc = f"Warehouse {warehouse_sku} reported fill_rate={fill_rate:.2f} with {stockout_days} stockout day(s) this period."
        else:
            status = "Monitored"
            status_class = "active"
            desc = "No structured supply signal recorded for this SKU/region in the current window."

        metrics = []
        if fill_rate is not None:
            metrics.append({
                "label": "Fill Rate", "val": f"{fill_rate:.2f}",
                "valClass": "danger" if fill_rate < 0.9 else "", "sub": f"{stockout_days} stockout day(s)",
            })
        metrics.append({"label": "Knowledge Graph Hits", "val": str(len(graph_hits)), "valClass": "", "sub": "Related records within 3 hops"})

        return {
            "title": "Supply Logistics & Knowledge Graph Context",
            "status": status,
            "statusClass": status_class,
            "desc": desc,
            "metrics": metrics,
        }

    # ------------------------------------------------------------------ #
    # Optional LLM prose polish (never touches numbers/schema fields)
    # ------------------------------------------------------------------ #
    def _facts_for_persona(self, anomaly, pvm_results, evidence_results, graph_results, persona):
        region = _region_name(anomaly["state_id"], self.contract)
        if persona == "vp_sales":
            return {
                "region": region,
                "deviation_pct": round(anomaly["deviation_pct"] * 100, 1),
                "z_score": round(anomaly["z_score"], 2),
                "confidence": anomaly["confidence"],
                "pvm": {k: {"pct_of_baseline": pvm_results[k]["pct"],
                            "share_of_change": pvm_results[k].get("share_of_change")}
                        for k in ("volume", "price", "mix")},
                "drivers_opposing": bool(pvm_results.get("drivers_opposing")),
                "driver_summary": pvm_results.get("driver_summary", ""),
                "marketing_spend_note": "present" if evidence_results.get("marketing_total", 0.0) > 0 else "none",
                "corroborating_records": len(
                    [e for e in evidence_results.get("evidence", []) if str(e.get("source", "")).startswith("unstructured_feedback")]
                ),
            }
        return {
            "item_id": anomaly["item_id"],
            "state_id": anomaly["state_id"],
            "fill_rate": evidence_results.get("supply_indicators", {}).get("fill_rate"),
            "stockout_days": evidence_results.get("supply_indicators", {}).get("stockout_days"),
            "graph_related_record_count": len(graph_results.get("hops", [])),
        }

    def _try_llm_polish(self, anomaly, pvm_results, evidence_results, deterministic_bundle):
        facts_vp = self._facts_for_persona(anomaly, pvm_results, evidence_results, {}, "vp_sales")
        facts_planner = self._facts_for_persona(anomaly, pvm_results, evidence_results, {}, "supply_planner")

        system_prompt = (
            "You are a business narrative writer for a retail KPI intelligence system. "
            "You NEVER invent numbers -- only use numbers given in the facts. Return strict JSON with keys "
            "vp_sales and supply_planner, each an object with headline, summary, synthesis_title, "
            "synthesis_body (plain text, no markdown, no numbered lists). vp_sales text must stay under 250 "
            "words total and must never mention SKU IDs, warehouse names, or logistics details. "
            "supply_planner text must stay under 400 words total and must never mention revenue, gross "
            "margin, COGS, or marketing spend dollar figures."
        )
        user_prompt = json.dumps({"vp_sales_facts": facts_vp, "supply_planner_facts": facts_planner})

        llm_result = llm_client.generate_json(system_prompt, user_prompt, max_tokens=700)
        telemetry = {
            "tokens_in": llm_result["tokens_in"],
            "tokens_out": llm_result["tokens_out"],
            "cost_usd": llm_result["cost_usd"],
            "latency_s": llm_result["latency_s"],
            "model": llm_result["model"],
            "calls": 1,
            "generation_method": "llm" if llm_result["success"] else "deterministic",
            "llm_error": llm_result["error"],
        }

        if not llm_result["success"]:
            return None, telemetry

        content = llm_result["content"] or {}
        try:
            merged = {}
            for persona in ("vp_sales", "supply_planner"):
                prose = content.get(persona, {}) or {}
                base = dict(deterministic_bundle[persona])
                item_id = anomaly["item_id"] if persona == "vp_sales" else None
                base["headline"] = _redact(prose.get("headline") or base["headline"], persona, item_id)
                base["summary"] = _redact(prose.get("summary") or base["summary"], persona, item_id)
                base["synthesis_title"] = _redact(prose.get("synthesis_title") or base["synthesis_title"], persona, item_id)
                base["synthesis_body"] = _redact(prose.get("synthesis_body") or base["synthesis_body"], persona, item_id)
                base["generation_method"] = "llm"
                PersonaNarrative(**base)  # raises on schema violation -> caught below
                merged[persona] = base
            return merged, telemetry
        except (ValidationError, Exception) as e:
            telemetry["generation_method"] = "deterministic"
            telemetry["llm_error"] = f"validation_failed: {type(e).__name__}"
            return None, telemetry

"""
src/analytics/graph_narrative_adapter.py

Compatibility shim: projects the new evidence graph
(analytics.graph_builder / analytics.graph_subgraph) into the legacy
`graph_results` shape that llm/narrative_generator.py still consumes, so the
narrative/action prose keeps working without touching narrative_generator.

narrative_generator only ever reads:
    graph_results.get("hops", [])            -> list
    h["hops"], h["temporal_role"], h["source"], h["date"], h["text"]  per hop

Here each layer-2 corroborating node from anomaly_subgraph() becomes one
"hop" record: a synthesized one-line description, tagged preceding_cause vs
concurrent_or_aftermath by its date relative to the anomaly's period start.
"""

from analytics.graph_subgraph import anomaly_subgraph


def _synth_text(node):
    k = node.get("kind")
    if k == "supply_anomaly":
        return (f"Supply signal: fill rate {node.get('fill_rate')} with "
                f"{node.get('stockout_days')} stockout day(s) at "
                f"{node.get('warehouse_sku', 'the warehouse')} in {node.get('month', '')}.")
    if k == "marketing_anomaly":
        return (f"Marketing signal: {node.get('channel', '')} spend anomaly "
                f"(z={node.get('z', 0):.1f}) for the week of {node.get('week_start', '')}.")
    if k == "review_shift":
        return (f"Customer sentiment {node.get('direction', 'shift')} in "
                f"{node.get('month', '')} (mean {node.get('mean_sentiment', 0):.2f} "
                f"over {node.get('review_count', 0)} review(s)).")
    if k == "event":
        return (f"Calendar event: {node.get('event_name', 'event')} "
                f"({node.get('event_type', '')}) on {node.get('date', '')}.")
    if k == "sales_anomaly":
        return (f"Co-occurring {node.get('column', 'sales')} movement on "
                f"{node.get('date', '')} (score {node.get('score', 0):.2f}).")
    return f"{k} node related to this anomaly."


def _node_date(node):
    return (node.get("date")
            or (f"{node['month']}-01" if node.get("month") else None)
            or node.get("week_start")
            or "")


def legacy_graph_results(graph, kpi_name, item_id, state_id, period_start, period_end):
    sub = anomaly_subgraph(graph, kpi_name, item_id, state_id, period_start, period_end)
    lo = str(period_start)[:10]

    hops = []
    for n in sub["nodes"]:
        if n.get("layer") != 2:
            continue
        d = _node_date(n)
        role = "preceding_cause" if (d and d <= lo) else "concurrent_or_aftermath"
        hops.append({
            "hops": 2,
            "temporal_role": role,
            "source": n.get("kind", "graph_node"),
            "date": d,
            "feedback_id": None,
            "recency_weight": n.get("recency_weight"),
            "text": _synth_text(n),
        })

    # Most recent / temporally closest corroboration first, so downstream prose
    # (which samples the first few hops) leads with the most relevant.
    hops.sort(key=lambda h: (h.get("recency_weight") if h.get("recency_weight") is not None else 0.0),
              reverse=True)

    return {"hops": hops, "node_count": sub["node_count"], "graph": sub}

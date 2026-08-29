"""
src/analytics/graph_subgraph.py

Extracts a small, per-anomaly subgraph from the persisted evidence graph
(analytics.graph_builder.build_graph) for the dashboard's "Interactive
Knowledge Graph" drawer panel and for the anomaly-detail payload's embedded
`graph_context`.

The full evidence graph has thousands of daily anomaly nodes; a drawer diagram
needs a handful. anomaly_subgraph() resolves the single most material graph
node for a given (kpi, item, state, month) anomaly row from the SQLite
`anomalies` table, then pulls in:

  layer 0  the focal anomaly node
  layer 1  its belongs_to entity nodes (item / state / warehouse) and its
           `explains` PVM driver nodes (units / sell_price)
  layer 2  entity-relevant, same-month corroborating nodes reached by the
           focal node's own temporal edges -- supply_anomaly / review_shift
           / marketing_anomaly via same_month|same_week, and the same-day
           `event` node via co_occurs_same_day (kept unconditionally, exactly
           as graph_query.explain_revenue_drop keeps same_day_event).

entity relevance uses graph_query.entity_relevant so the deliberately
unfiltered same_week / same_month fan-out is trimmed back to this item/state.
"""

import datetime as _dt

try:  # numpy is a hard dep of the graph build, but keep the import defensive
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

from analytics import graph_query as _gq


# KPI name (semantic_contract.json / anomalies.kpi_name) -> graph node-id prefix.
_KPI_PREFIX = {
    "Revenue": "revenue_anom_",
    "GrossMarginPercent": "gross_margin_percent_anom_",
    "InventoryTurnover": "turnover_anom_",
}

# Relations that make a node a layer-2 "corroborating" neighbour of the focal.
_CORROBORATION_RELATIONS = ("same_week", "same_month", "co_occurs_same_day")
_CORROBORATION_KINDS = ("supply_anomaly", "review_shift", "marketing_anomaly")

# Upper bound on layer-2 nodes so the drawer diagram stays readable.
_MAX_LAYER2 = 8

# Recency weighting: a corroborating anomaly's influence halves every
# _RECENCY_HALF_LIFE_DAYS of temporal distance from the focal anomaly, and an
# anomaly that occurred strictly *before* the focal one is discounted a further
# _OLDER_PENALTY (more-recent movement is treated as more relevant, per request).
_RECENCY_HALF_LIFE_DAYS = 30.0
_OLDER_PENALTY = 0.8


def _jsonable(v):
    """Coerce numpy scalars / dates so json.dumps() won't choke."""
    if _np is not None and isinstance(v, _np.generic):
        return v.item()
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def _node_day(a):
    """Best calendar date for any node kind, as a datetime.date (or None)."""
    s = a.get("date") or a.get("week_start")
    if not s and a.get("month"):
        s = f"{a['month']}-01"
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _recency_weight(days_from_focal):
    """(0, 1] weight: 1.0 at the focal date, halving every _RECENCY_HALF_LIFE_DAYS,
    with an extra penalty for anomalies that predate the focal one."""
    w = 0.5 ** (abs(days_from_focal) / _RECENCY_HALF_LIFE_DAYS)
    if days_from_focal < 0:
        w *= _OLDER_PENALTY
    return round(w, 4)


def _label_for(node_id, a):
    k = a.get("kind")
    if k == "item_entity":
        return a.get("item_id", node_id)
    if k == "state_entity":
        return a.get("state_id", node_id)
    if k == "warehouse_entity":
        return a.get("warehouse_sku", node_id)
    if k == "channel_entity":
        return a.get("channel", node_id)
    if k == "store_entity":
        return a.get("store_id", node_id)
    if k == "eventname_entity":
        return a.get("event_name", node_id)
    if k == "event":
        return f"{a.get('event_name', 'event')} ({a.get('date', '')})"
    if k == "sales_anomaly":
        return f"{a.get('column', 'sales')} · {a.get('date', '')}"
    if k == "inventory_anomaly":
        return f"turnover · {a.get('date', '')}"
    if k == "marketing_anomaly":
        return f"{a.get('channel', '')} spend · {a.get('week_start', '')}"
    if k == "supply_anomaly":
        return f"fill {a.get('fill_rate', '')} · {a.get('month', '')}"
    if k == "review_shift":
        return f"sentiment {a.get('direction', 'shift')} · {a.get('month', '')}"
    return node_id


def _node_out(graph, node_id, layer, recency=None):
    a = {k: _jsonable(v) for k, v in graph.nodes[node_id].items()}
    out = {
        "id": node_id,
        "kind": a.get("kind", "unknown"),
        "label": _label_for(node_id, a),
        "layer": layer,
    }
    if recency is not None:
        out["days_from_focal"], out["recency_weight"] = recency
    # carry the numeric/context attributes the panel's detail pane shows
    for k in ("date", "month", "week_start", "column", "value", "score", "z",
              "baseline_mean", "fill_rate", "stockout_days", "turnover_ratio",
              "inventory_on_hand", "channel", "region", "event_name", "event_type",
              "direction", "mean_sentiment", "review_count", "warehouse_sku",
              "price_effect", "volume_effect", "interaction_effect",
              "total_decomposed", "actual_delta", "item", "state"):
        if k in a:
            out[k] = a[k]
    return out


def _focal_node(graph, kpi_name, item_id, state_id, lo, hi):
    """Peak-|score| daily anomaly node of this KPI for item/state within [lo, hi]."""
    prefix = _KPI_PREFIX.get(kpi_name)
    if not prefix:
        return None
    best, best_mag = None, -1.0
    for node_id, a in graph.nodes(data=True):
        if not node_id.startswith(prefix):
            continue
        if a.get("item") != item_id or a.get("state") != state_id:
            continue
        d = str(a.get("date", ""))
        if not (lo <= d <= hi):
            continue
        mag = abs(a.get("score", a.get("z", 0.0)) or 0.0)
        if mag > best_mag:
            best, best_mag = node_id, mag
    return best


def anomaly_subgraph(graph, kpi_name, item_id, state_id, period_start, period_end):
    """
    Returns {"nodes": [...], "edges": [...], "node_count": int, "focal": node_id|None}.
    Node dicts carry: id, kind, label, layer (0/1/2), plus real attributes; layer-2
    anomaly nodes also carry days_from_focal and recency_weight.
    Edge dicts carry: source, target, relation; driver/weight/dollar_effect for
    `explains` edges; and for anomaly<->anomaly edges: day_diff (signed
    target-minus-source days), days_from_focal, and recency_weight (0-1, higher =
    more recent relative to the focal anomaly).
    """
    empty = {"nodes": [], "edges": [], "node_count": 0, "focal": None}
    if graph is None:
        return empty

    lo = str(period_start)[:10]
    hi = str(period_end)[:10]
    month = lo[:7]

    focal = _focal_node(graph, kpi_name, item_id, state_id, lo, hi)
    focal_day = _node_day(graph.nodes[focal]) if focal else None

    visited = {}         # node_id -> layer
    recency = {}         # node_id -> (days_from_focal, recency_weight)  [layer-2 anomaly nodes]
    edges = []
    seen_edge = set()

    _ANOM_KINDS = ("sales_anomaly", "inventory_anomaly", "marketing_anomaly",
                   "supply_anomaly", "review_shift", "event")

    def _recency_for(node_id):
        """(days_from_focal, weight) for a node relative to the focal anomaly."""
        if focal_day is None:
            return None
        nd = _node_day(graph.nodes.get(node_id, {}))
        if nd is None:
            return None
        dd = (nd - focal_day).days
        return dd, _recency_weight(dd)

    def add_edge(u, v, data):
        rel = data.get("relation")
        key = (u, v, rel)
        if key in seen_edge:
            return
        seen_edge.add(key)
        e = {"source": u, "target": v, "relation": rel}
        for k in ("driver", "weight", "dollar_effect"):
            if k in data:
                e[k] = _jsonable(data[k])
        # Day difference + recency weight for anomaly<->anomaly edges (both
        # endpoints dated). day_diff is signed target-minus-source in days;
        # recency_weight favours the more recent of the two endpoints.
        ua, va = graph.nodes.get(u, {}), graph.nodes.get(v, {})
        if ua.get("kind") in _ANOM_KINDS and va.get("kind") in _ANOM_KINDS:
            ud, vd = _node_day(ua), _node_day(va)
            if ud and vd:
                e["day_diff"] = (vd - ud).days
                # days_from_focal / recency_weight only where one endpoint is the
                # focal anomaly (the meaningful "how recent is this corroborator").
                if focal_day is not None and focal in (u, v):
                    other = vd if u == focal else ud
                    e["days_from_focal"] = (other - focal_day).days
                    e["recency_weight"] = _recency_weight(e["days_from_focal"])
        edges.append(e)

    if focal is None:
        # No graph anomaly node for this exact KPI/period -- anchor on the entity
        # layer so the panel can still show the structural context.
        anchor = f"item_{item_id}"
        if not graph.has_node(anchor):
            return empty
        visited[anchor] = 0
        for ent in (f"state_{state_id}",):
            if graph.has_node(ent):
                visited[ent] = 1
        nodes = [_node_out(graph, n, lyr) for n, lyr in visited.items()]
        return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "focal": None}

    visited[focal] = 0

    # --- layer 1: entity chain + PVM driver nodes ---
    for u, v, d in graph.out_edges(focal, data=True):
        rel = d.get("relation")
        if rel == "belongs_to":
            visited.setdefault(v, 1)
            add_edge(u, v, d)
        elif rel == "explains":
            visited.setdefault(v, 1)
            add_edge(u, v, d)
    for u, v, d in graph.in_edges(focal, data=True):
        if d.get("relation") == "explains":
            visited.setdefault(u, 1)
            add_edge(u, v, d)

    # --- layer 2: entity-relevant, same-window corroboration ---
    for u, v, d in graph.out_edges(focal, data=True):
        rel = d.get("relation")
        if rel not in _CORROBORATION_RELATIONS:
            continue
        cand_attrs = graph.nodes[v]
        if cand_attrs.get("kind") not in _CORROBORATION_KINDS:
            continue
        try:
            if not _gq.entity_relevant(graph, focal, v):
                continue
        except Exception:
            continue
        visited.setdefault(v, 2)
        recency.setdefault(v, _recency_for(v))
        add_edge(u, v, d)

    for u, v, d in graph.in_edges(focal, data=True):
        rel = d.get("relation")
        if rel == "co_occurs_same_day" and graph.nodes[u].get("kind") == "event":
            # same-day calendar event -- date-exact, kept unconditionally
            visited.setdefault(u, 2)
            recency.setdefault(u, _recency_for(u))
            add_edge(u, v, d)
        elif rel in _CORROBORATION_RELATIONS and graph.nodes[u].get("kind") in _CORROBORATION_KINDS:
            try:
                if _gq.entity_relevant(graph, focal, u):
                    visited.setdefault(u, 2)
                    recency.setdefault(u, _recency_for(u))
                    add_edge(u, v, d)
            except Exception:
                pass

    # Keep the diagram legible: cap layer-2 corroboration at the strongest few.
    # Ranking = statistical magnitude * recency weight, so a more recent anomaly
    # outranks an equally-strong but older one (per request).
    layer2 = [n for n, lyr in visited.items() if lyr == 2]
    if len(layer2) > _MAX_LAYER2:
        def _rank(n):
            a = graph.nodes[n]
            mag = abs(a.get("z", a.get("score", 0.0)) or 0.0) or 0.1
            rw = (recency.get(n) or (0, 1.0))[1]
            return mag * rw
        drop = sorted(layer2, key=_rank)[:len(layer2) - _MAX_LAYER2]
        for n in drop:
            del visited[n]
            recency.pop(n, None)
        edges[:] = [e for e in edges if e["source"] in visited and e["target"] in visited]
        seen_edge = {(e["source"], e["target"], e["relation"]) for e in edges}

    # any belongs_to / explains edges strictly between already-visited nodes
    for u, v, d in graph.edges(data=True):
        if u in visited and v in visited:
            add_edge(u, v, d)

    nodes = [_node_out(graph, n, lyr, recency.get(n)) for n, lyr in visited.items()]
    return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "focal": focal}

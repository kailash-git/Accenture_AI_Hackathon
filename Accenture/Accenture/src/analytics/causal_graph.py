"""Hand-authored summary causal graph over the KPI variables -- the input
EasyRCA (Assaad et al., AISTATS 2023) needs. One node per variable, edges are
causal mechanisms that hold in normal operation. `sell_price -> revenue` and
`units -> revenue` are the PVM identity (also `explains` edges in graph_builder).
"""
import collections

import networkx as nx

VARIABLES = [
    "event", "marketing_spend", "sell_price", "units", "revenue",
    "gross_margin_percent", "fill_rate", "stockout_days", "inventory_turnover",
    "sentiment",
]

# (parent, child, origin) -- origin is "pvm" (from graph_builder.add_explains_edges)
# or "domain". The units -> sentiment feedback edge is omitted to keep a DAG.
_EDGES = [
    ("event", "units", "domain"),
    ("event", "marketing_spend", "domain"),
    ("marketing_spend", "units", "domain"),
    ("sell_price", "units", "domain"),
    ("sell_price", "revenue", "pvm"),
    ("units", "revenue", "pvm"),
    ("sell_price", "gross_margin_percent", "domain"),
    ("sell_price", "sentiment", "domain"),
    ("stockout_days", "fill_rate", "domain"),
    ("fill_rate", "units", "domain"),
    ("stockout_days", "units", "domain"),
    ("units", "inventory_turnover", "domain"),
    ("fill_rate", "inventory_turnover", "domain"),
    ("fill_rate", "sentiment", "domain"),
    ("stockout_days", "sentiment", "domain"),
    ("sentiment", "units", "domain"),
]


def build_summary_causal_graph():
    g = nx.DiGraph()
    g.add_nodes_from(VARIABLES)
    for u, v, origin in _EDGES:
        g.add_edge(u, v, origin=origin)
    if not nx.is_directed_acyclic_graph(g):
        raise ValueError("summary causal graph must be acyclic")
    return g


SUMMARY_CAUSAL_GRAPH = build_summary_causal_graph()


def variable_of(evidence_graph, node_id, stockout_threshold=2):
    """Evidence-graph anomaly/event node -> its causal variable, or None."""
    if not evidence_graph.has_node(node_id):
        return None
    a = evidence_graph.nodes[node_id]
    kind = a.get("kind")
    if kind == "sales_anomaly":
        col = a.get("column")
        return col if col in ("units", "revenue", "sell_price", "gross_margin_percent") else None
    if kind == "marketing_anomaly":
        return "marketing_spend"
    if kind == "supply_anomaly":
        return "stockout_days" if a.get("stockout_days", 0) >= stockout_threshold else "fill_rate"
    if kind == "inventory_anomaly":
        return "inventory_turnover"
    if kind == "review_shift":
        return "sentiment"
    if kind == "event":
        return "event"
    return None


def is_d_separated(g, x, y, z):
    """networkx renamed d_separated -> is_d_separator (~3.3) and dropped the old name by 3.6."""
    xs = {x} if isinstance(x, str) else set(x)
    ys = {y} if isinstance(y, str) else set(y)
    zs = {z} if isinstance(z, str) else set(z)
    if hasattr(nx, "is_d_separator"):
        return nx.is_d_separator(g, xs, ys, zs)
    return nx.d_separated(g, xs, ys, zs)


def backdoor_adjustment_set(g, treatment, outcome):
    """Parents of `treatment` that are also ancestors of `outcome` -- a valid
    backdoor set in a DAG."""
    if treatment not in g or outcome not in g:
        return set()
    return set(g.predecessors(treatment)) & nx.ancestors(g, outcome)


def validate_against_evidence_graph(evidence_graph, causal_graph=None):
    """For each authored edge, count how often the two variables' anomaly nodes
    co-occur in the persisted evidence graph. Zero support flags an edge to revisit."""
    causal_graph = causal_graph or SUMMARY_CAUSAL_GRAPH
    temporal = {"co_occurs_same_day", "same_week", "same_month", "explains"}
    pair_counts = collections.Counter()
    for u, v, a in evidence_graph.edges(data=True):
        if a.get("relation") not in temporal:
            continue
        vu, vv = variable_of(evidence_graph, u), variable_of(evidence_graph, v)
        if vu and vv and vu != vv:
            pair_counts[frozenset((vu, vv))] += 1
    return [
        {"edge": (x, y), "cooccurrence_support": pair_counts.get(frozenset((x, y)), 0),
         "supported": pair_counts.get(frozenset((x, y)), 0) > 0}
        for x, y in causal_graph.edges()
    ]

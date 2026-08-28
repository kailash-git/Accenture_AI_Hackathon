"""
src/retrieval/knowledge_graph.py
Lightweight property graph + multi-hop traversal (REQ-02).

Builds a real networkx graph over the structured tables:

    Feedback --MENTIONS_PRODUCT--> Item --BELONGS_TO_CATEGORY--> Category
    Feedback --REPORTED_IN_REGION--> Region
    Item --SOLD_IN_REGION--> Region
    Item --STOCKED_AT--> Warehouse (SKU-level)
    Item --INVENTORIED_AT--> WarehouseSite --LOCATED_IN--> Region

`get_related_context` performs a real BFS traversal outward from an anomaly's
(item, region) anchor to `max_hops`, returning every reachable feedback node
with its hop distance and a temporal-causality label -- this is the concrete
mechanism behind the dashboard's "Knowledge Graph" panel, not decoration.
"""

import sqlite3

import networkx as nx
import pandas as pd


def build_graph(db_path: str) -> nx.MultiDiGraph:
    conn = sqlite3.connect(db_path)
    try:
        sku_df = pd.read_sql_query("SELECT item_id, warehouse_sku FROM sku_lookup", conn)
        sales_df = pd.read_sql_query(
            "SELECT DISTINCT item_id, cat_id, state_id FROM fact_sales_daily", conn
        )
        inv_df = pd.read_sql_query(
            "SELECT DISTINCT item_id, warehouse_name, state_id FROM inventory_logs", conn
        )
        fb_df = pd.read_sql_query(
            "SELECT feedback_id, item_id, state_id, source, text_content, date FROM unstructured_feedback",
            conn,
        )
    finally:
        conn.close()

    g = nx.MultiDiGraph()

    for _, row in sales_df.iterrows():
        item_node = f"item:{row['item_id']}"
        cat_node = f"category:{row['cat_id']}"
        region_node = f"region:{row['state_id']}"
        g.add_node(item_node, type="item", item_id=row["item_id"])
        g.add_node(cat_node, type="category", cat_id=row["cat_id"])
        g.add_node(region_node, type="region", state_id=row["state_id"])
        g.add_edge(item_node, cat_node, relation="BELONGS_TO_CATEGORY")
        g.add_edge(item_node, region_node, relation="SOLD_IN_REGION")

    for _, row in sku_df.iterrows():
        item_node = f"item:{row['item_id']}"
        wh_node = f"warehouse_sku:{row['warehouse_sku']}"
        if not g.has_node(item_node):
            continue
        g.add_node(wh_node, type="warehouse", warehouse_sku=row["warehouse_sku"])
        g.add_edge(item_node, wh_node, relation="STOCKED_AT")

    for _, row in inv_df.iterrows():
        item_node = f"item:{row['item_id']}"
        site_node = f"warehouse_site:{row['warehouse_name']}"
        region_node = f"region:{row['state_id']}"
        if not g.has_node(item_node):
            continue
        g.add_node(site_node, type="warehouse_site", warehouse_name=row["warehouse_name"])
        g.add_node(region_node, type="region", state_id=row["state_id"])
        g.add_edge(site_node, region_node, relation="LOCATED_IN")
        g.add_edge(item_node, site_node, relation="INVENTORIED_AT")

    for _, row in fb_df.iterrows():
        fb_node = f"feedback:{int(row['feedback_id'])}"
        item_node = f"item:{row['item_id']}"
        region_node = f"region:{row['state_id']}"
        g.add_node(
            fb_node,
            type="feedback",
            feedback_id=int(row["feedback_id"]),
            source=row["source"],
            text=row["text_content"],
            date=row["date"],
        )
        if g.has_node(item_node):
            g.add_edge(fb_node, item_node, relation="MENTIONS_PRODUCT")
        g.add_node(region_node, type="region", state_id=row["state_id"])
        g.add_edge(fb_node, region_node, relation="REPORTED_IN_REGION")

    return g


def _node_label(node_id: str, data: dict) -> str:
    """Human-readable label for a graph node, used by the frontend GraphRAG visualization."""
    t = data.get("type")
    if t == "item":
        return data.get("item_id", node_id)
    if t == "category":
        return data.get("cat_id", node_id)
    if t == "region":
        return data.get("state_id", node_id)
    if t == "warehouse":
        return data.get("warehouse_sku", node_id)
    if t == "warehouse_site":
        return data.get("warehouse_name", node_id)
    if t == "feedback":
        return f"{str(data.get('source', 'feedback')).title()} ({data.get('date', '')})"
    return node_id


def _export_subgraph(g: nx.MultiDiGraph, visited: dict) -> dict:
    """
    Renders the BFS-visited node set as a real node/edge graph export -- this is
    the concrete payload the dashboard's "Interactive Knowledge Graph" panel draws,
    not a decorative static chain. Kept to just the visited neighborhood (typically
    a handful of nodes at this dataset's scale) so it stays legible as a diagram.
    """
    nodes = []
    for node_id, dist in visited.items():
        data = g.nodes[node_id]
        nodes.append({
            "id": node_id,
            "type": data.get("type"),
            "label": _node_label(node_id, data),
            "hops": dist,
        })

    edges = []
    seen = set()
    for u, v, edata in g.edges(data=True):
        if u in visited and v in visited:
            relation = edata.get("relation")
            key = (u, v, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": u, "target": v, "relation": relation})

    return {"nodes": nodes, "edges": edges}


def get_related_context(
    g: nx.MultiDiGraph, item_id: str, state_id: str, period_start: str, period_end: str = None,
    max_hops: int = 3, window_days_before: int = 5, window_days_after: int = 10,
) -> dict:
    """
    Real BFS traversal from the (item, region) anchor nodes out to `max_hops`.
    Returns every feedback node reached, tagged with hop distance, the
    traversal path (node ids), and whether it plausibly precedes the anomaly
    (a candidate cause) or is concurrent/after it (a corroborating consequence).

    Feedback nodes are only included if their date falls within
    [-window_days_before, +window_days_after] of the anomaly's
    [period_start, period_end] window -- graph connectivity alone (e.g.
    "mentions the same SKU") is not enough; a record from a different month
    describes a different event and must not be pulled into this anomaly's
    narrative (REQ-02 temporal relevance filter).

    These defaults intentionally match evidence_reconciler.reconcile_evidence's
    own temporal window exactly. The two used to disagree (this traversal used
    a +/-45-day window while the reconciler used -5/+10 days), which let a
    neighboring month's real event (e.g. the Nov 2012 stockout feedback) leak
    into an adjacent month's anomaly (e.g. Oct 2012) as if it were corroborating
    evidence, even though the reconciler correctly excluded it from that same
    anomaly's structured evidence list. One shared window keeps "what counts as
    related to this anomaly" a single, consistent policy across both retrieval
    paths instead of two silently-conflicting ones.
    """
    anchor_nodes = [n for n in (f"item:{item_id}", f"region:{state_id}") if g.has_node(n)]
    if not anchor_nodes:
        return {"hops": [], "node_count": 0, "graph": {"nodes": [], "edges": []}}

    period_start_dt = pd.to_datetime(period_start)
    period_end_dt = pd.to_datetime(period_end) if period_end else period_start_dt
    window_lo = period_start_dt - pd.Timedelta(days=window_days_before)
    window_hi = period_end_dt + pd.Timedelta(days=window_days_after)

    visited = {n: 0 for n in anchor_nodes}
    frontier = [(n, 0, [n]) for n in anchor_nodes]
    out_of_window_feedback = set()

    results = []
    idx = 0
    while idx < len(frontier):
        node, dist, path = frontier[idx]
        idx += 1
        if dist >= max_hops:
            continue
        neighbors = set(g.successors(node)) | set(g.predecessors(node))
        for nbr in neighbors:
            if nbr in visited:
                continue
            visited[nbr] = dist + 1
            new_path = path + [nbr]
            frontier.append((nbr, dist + 1, new_path))

            node_data = g.nodes[nbr]
            if node_data.get("type") == "feedback":
                fb_date = pd.to_datetime(node_data["date"])
                if fb_date < window_lo or fb_date > window_hi:
                    out_of_window_feedback.add(nbr)  # a different event -- excluded below, not just from `results`
                    continue
                precedes_or_concurrent = fb_date <= period_start_dt
                results.append(
                    {
                        "feedback_id": node_data["feedback_id"],
                        "hops": dist + 1,
                        "path": new_path,
                        "date": node_data["date"],
                        "source": node_data["source"],
                        "text": node_data["text"],
                        "temporal_role": "preceding_cause" if precedes_or_concurrent else "concurrent_or_aftermath",
                    }
                )

    results.sort(key=lambda r: r["hops"])
    # The graph visualization must respect the same temporal relevance filter as the
    # narrative -- an out-of-window feedback node (a different month's event) must not
    # appear as an undifferentiated node in this anomaly's knowledge-graph diagram.
    exportable = {n: d for n, d in visited.items() if n not in out_of_window_feedback}
    return {"hops": results, "node_count": len(visited), "graph": _export_subgraph(g, exportable)}

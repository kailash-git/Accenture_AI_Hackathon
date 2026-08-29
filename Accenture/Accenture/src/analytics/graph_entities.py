import sqlite3
import networkx as nx


def build_entity_graph(db_path):
    """
    Builds the entity layer of the evidence graph: one first-class node per
    item, state, store, marketing channel, warehouse_sku, and event_name,
    plus 'belongs_to' hierarchy edges between them (store -> state,
    warehouse -> item, via sku_lookup's 1:1 mapping).

    This exists so a query like "everything for FOODS_3_090" can be a graph
    traversal (predecessors of item_FOODS_3_090) instead of an attribute-scan
    filter over every anomaly/event node -- see handoff doc section 7, item 3.
    """
    g = nx.DiGraph()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT item_id FROM sku_lookup")
    for (item_id,) in cur.fetchall():
        g.add_node(f"item_{item_id}", kind="item_entity", item_id=item_id)

    cur.execute("SELECT item_id, warehouse_sku FROM sku_lookup")
    for item_id, warehouse_sku in cur.fetchall():
        wh_node = f"warehouse_{warehouse_sku}"
        g.add_node(wh_node, kind="warehouse_entity", warehouse_sku=warehouse_sku, item_id=item_id)
        g.add_edge(wh_node, f"item_{item_id}", relation="belongs_to")

    cur.execute("SELECT DISTINCT state_id FROM fact_sales_daily")
    for (state_id,) in cur.fetchall():
        g.add_node(f"state_{state_id}", kind="state_entity", state_id=state_id)

    cur.execute("SELECT DISTINCT state_id, store_id FROM fact_sales_daily")
    for state_id, store_id in cur.fetchall():
        store_node = f"store_{store_id}"
        g.add_node(store_node, kind="store_entity", store_id=store_id, state_id=state_id)
        g.add_edge(store_node, f"state_{state_id}", relation="belongs_to")

    cur.execute("SELECT DISTINCT channel FROM source_marketing_weekly")
    for (channel,) in cur.fetchall():
        g.add_node(f"channel_{channel}", kind="channel_entity", channel=channel)

    # Event-name entities are the recurring calendar category (e.g. 'VeteransDay').
    # Kept as a distinct 'eventname_' prefix from the dated occurrence node
    # ('event_{date}') that the occurrence layer will add later, to avoid collision.
    cur.execute(
        "SELECT DISTINCT event_name_1, event_type_1 FROM fact_sales_daily WHERE event_name_1 IS NOT NULL"
    )
    for event_name, event_type in cur.fetchall():
        g.add_node(f"eventname_{event_name}", kind="eventname_entity",
                    event_name=event_name, event_type=event_type)

    conn.close()
    return g


def attach_belongs_to(graph, anomaly_node_id, item_id=None, state_id=None, store_id=None,
                       channel=None, warehouse_sku=None, event_name=None):
    """
    Wires a belongs_to edge from an anomaly/event node into each of its
    matching entity nodes, for whichever attributes are actually passed.
    Silently skips any attribute whose entity node doesn't exist in the graph.
    """
    candidates = {
        "item": (item_id, f"item_{item_id}" if item_id is not None else None),
        "state": (state_id, f"state_{state_id}" if state_id is not None else None),
        "store": (store_id, f"store_{store_id}" if store_id is not None else None),
        "channel": (channel, f"channel_{channel}" if channel is not None else None),
        "warehouse": (warehouse_sku, f"warehouse_{warehouse_sku}" if warehouse_sku is not None else None),
        "eventname": (event_name, f"eventname_{event_name}" if event_name is not None else None),
    }
    for _, (value, entity_node) in candidates.items():
        if value is not None and graph.has_node(entity_node):
            graph.add_edge(anomaly_node_id, entity_node, relation="belongs_to")

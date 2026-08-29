"""
Query functions over the persisted evidence graph.

same_week / same_month edges are NOT entity-filtered at construction time
(see graph_builder.add_temporal_edges) -- per handoff doc section 7 item 4,
confirmed empirically (one review_shift node had 88 same_month in-edges,
nearly all belonging to unrelated items). filtered_temporal_neighbors()
below applies the entity filter at query time instead.
"""


def _entity_set(graph, node_id, max_hops=2):
    """
    All *_entity nodes reachable from node_id by walking 'belongs_to' edges
    outward, up to max_hops (2 covers e.g. supply_anomaly -> warehouse -> item).
    """
    entities = set()
    seen = {node_id}
    frontier = {node_id}
    for _ in range(max_hops):
        next_frontier = set()
        for n in frontier:
            for _, v, a in graph.out_edges(n, data=True):
                if a['relation'] == 'belongs_to' and v not in seen:
                    seen.add(v)
                    if graph.nodes[v]['kind'].endswith('_entity'):
                        entities.add(v)
                    next_frontier.add(v)
        frontier = next_frontier
    return entities


def entity_relevant(graph, center_node, candidate_node, max_hops=2):
    """
    True if candidate_node is entity-relevant to center_node.

    Rule: require a shared item entity if the candidate has one at all
    (e.g. sales_anomaly, review_shift, or supply_anomaly via its warehouse ->
    item edge). Only fall back to a shared state entity for node kinds that
    structurally never have an item entity -- e.g. marketing_anomaly, which
    is only ever channel/state-scoped. A state-only match is NOT accepted
    when the candidate does have an item entity: with only 2 states in this
    dataset, "same state" barely discriminates and would readmit most of
    the fan-out this filter exists to remove.
    """
    center_entities = _entity_set(graph, center_node, max_hops)
    candidate_entities = _entity_set(graph, candidate_node, max_hops)

    center_items = {e for e in center_entities if graph.nodes[e]['kind'] == 'item_entity'}
    candidate_items = {e for e in candidate_entities if graph.nodes[e]['kind'] == 'item_entity'}

    if candidate_items:
        return bool(center_items & candidate_items)

    center_states = {e for e in center_entities if graph.nodes[e]['kind'] == 'state_entity'}
    candidate_states = {e for e in candidate_entities if graph.nodes[e]['kind'] == 'state_entity'}
    return bool(center_states & candidate_states)


def filtered_temporal_neighbors(graph, node_id, relation, direction='in', max_hops=2):
    """
    same_week / same_month neighbors of node_id, restricted to those that
    are entity_relevant. direction='in' matches how these edges are built
    (daily nodes -> weekly/monthly nodes), so this is the normal case for
    "what corroborates this monthly/weekly anomaly".
    """
    if direction == 'in':
        candidates = [u for u, v, a in graph.in_edges(node_id, data=True) if a['relation'] == relation]
    else:
        candidates = [v for u, v, a in graph.out_edges(node_id, data=True) if a['relation'] == relation]

    return [n for n in candidates if entity_relevant(graph, node_id, n, max_hops)]


def anomalies_for_item(graph, item_id, kinds=None):
    """
    Every anomaly/event/review_shift node belonging to an item, via the
    belongs_to traversal (not an attribute scan).
    """
    node = f"item_{item_id}"
    if not graph.has_node(node):
        return []
    results = list(graph.predecessors(node))
    if kinds:
        results = [n for n in results if graph.nodes[n]['kind'] in kinds]
    return results


def anomalies_for_state(graph, state_id, kinds=None):
    """Every anomaly/event node belonging to a state, via belongs_to."""
    node = f"state_{state_id}"
    if not graph.has_node(node):
        return []
    results = list(graph.predecessors(node))
    if kinds:
        results = [n for n in results if graph.nodes[n]['kind'] in kinds]
    return results


def same_day_evidence(graph, date_str):
    """
    The event node (if any) on a given date, plus every sales_anomaly
    co-occurring on that exact date. Safe/precise: same-day, not same-month.
    """
    event_node = f"event_{date_str}"
    if not graph.has_node(event_node):
        return {'event': None, 'co_occurring_anomalies': []}
    targets = [v for u, v, a in graph.out_edges(event_node, data=True)
               if a['relation'] == 'co_occurs_same_day']
    return {
        'event': dict(graph.nodes[event_node]),
        'co_occurring_anomalies': [{'node': n, **graph.nodes[n]} for n in targets],
    }


def explain_revenue_anomaly(graph, node_id):
    """
    The PVM decomposition attributes for a revenue_anomaly node, plus any
    driver nodes (units/price) it has an 'explains' edge to.
    """
    if not graph.has_node(node_id):
        return None
    drivers = [
        {'driver_node': v, 'driver': a['driver'], 'dollar_effect': a['dollar_effect'], 'weight': a['weight']}
        for u, v, a in graph.out_edges(node_id, data=True) if a['relation'] == 'explains'
    ]
    return {'node': node_id, 'attrs': dict(graph.nodes[node_id]), 'drivers': drivers}


def get_node(graph, node_id):
    """Raw attribute lookup for any node id."""
    if not graph.has_node(node_id):
        return None
    return {'node': node_id, **graph.nodes[node_id]}


def explain_revenue_drop(graph, item_id, state_id, date_str):
    """
    Full "why did revenue move on this item/state/date" answer: PVM driver
    breakdown, same-day event, entity-filtered supply evidence for that
    month, and review sentiment for that item/state/month. Consolidates the
    per-node query functions above with the entity filter, instead of the
    ad hoc per-query filtering done by hand previously.
    """
    node_id = f"revenue_anom_{item_id}_{state_id}_{date_str}"
    if not graph.has_node(node_id):
        return None

    result = explain_revenue_anomaly(graph, node_id)
    prev_rev = result['attrs']['value'] - result['attrs']['actual_delta']
    result['pct_change'] = (result['attrs']['actual_delta'] / prev_rev) if prev_rev else None

    result['same_day_event'] = same_day_evidence(graph, date_str)['event']

    month = date_str[:7]
    review_node = f"review_shift_{item_id}_{state_id}_{month}"
    result['review_sentiment'] = get_node(graph, review_node)

    supply_candidates = [n for n, a in graph.nodes(data=True)
                          if a['kind'] == 'supply_anomaly' and a['month'] == month]
    result['supply_evidence'] = [
        get_node(graph, n) for n in supply_candidates if entity_relevant(graph, node_id, n)
    ]

    marketing_candidates = filtered_temporal_neighbors(graph, node_id, 'same_week', direction='out')
    result['marketing_evidence'] = [get_node(graph, n) for n in marketing_candidates
                                     if graph.nodes[n]['kind'] == 'marketing_anomaly']

    return result

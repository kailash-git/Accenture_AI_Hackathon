from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from analytics.aggregation import (
    get_item_state_daily, get_marketing_weekly, get_supply_monthly, get_events,
    get_inventory_turnover_daily,
)
from analytics.series_anomaly import detect_trailing_zscore, detect_pct_change, detect_supply_rule
from analytics.sentiment import get_feedback_monthly_sentiment
from analytics.graph_entities import build_entity_graph, attach_belongs_to


def add_sales_anomaly_nodes(graph, db_path, window=8, z_threshold=1.1, price_threshold=0.03,
                             margin_threshold=1.1):
    """
    Adds sales_anomaly nodes (units, revenue, sell_price, gross_margin_percent)
    computed on the item x state x day aggregate, and wires each into its
    item/state entity nodes via belongs_to edges.

    Node id deviates from the handoff doc's original '{column}_anom_{date}'
    convention by including item_id and state_id: that convention assumed a
    single item/store series. Once aggregated to item x state grain (item 1),
    date alone is no longer unique -- multiple item/state combinations can
    each have their own anomaly on the same column and date.

    gross_margin_percent uses the same trailing z-score detector as
    units/revenue (a ratio, not a price -- percent-change detection doesn't
    apply the same way). It inherits the same zero-sales-day artifact already
    documented for sell_price: a closure day (units=0) also has
    gross_margin_percent coerced to 0.0 by get_item_state_daily, which can
    register as a fake margin anomaly the same way it does for price. Known,
    not fixed here -- consistent with the existing, already-flagged
    sell_price behavior rather than a new inconsistency.
    """
    df = get_item_state_daily(db_path)

    units = detect_trailing_zscore(df, ['item_id', 'state_id'], 'date', 'units',
                                    window=window, threshold=z_threshold)
    revenue = detect_trailing_zscore(df, ['item_id', 'state_id'], 'date', 'revenue',
                                      window=window, threshold=z_threshold)
    price = detect_pct_change(df, ['item_id', 'state_id'], 'date', 'sell_price',
                               threshold=price_threshold)
    margin = detect_trailing_zscore(df, ['item_id', 'state_id'], 'date', 'gross_margin_percent',
                                     window=window, threshold=margin_threshold)

    for _, row in units.iterrows():
        _add_sales_node(graph, row, 'units', row['z'], baseline_mean=row['baseline_mean'])
    for _, row in revenue.iterrows():
        _add_sales_node(graph, row, 'revenue', row['z'], baseline_mean=row['baseline_mean'])
    for _, row in price.iterrows():
        _add_sales_node(graph, row, 'sell_price', row['pct_chg'])  # detect_pct_change has no baseline_mean column
    for _, row in margin.iterrows():
        _add_sales_node(graph, row, 'gross_margin_percent', row['z'], baseline_mean=row['baseline_mean'])

    return graph


def _add_sales_node(graph, row, column, score, baseline_mean=None):
    date_str = row['date'].strftime('%Y-%m-%d')
    node_id = f"{column}_anom_{row['item_id']}_{row['state_id']}_{date_str}"
    graph.add_node(
        node_id,
        kind='sales_anomaly',
        column=column,
        date=date_str,
        value=row[column],
        score=score,
        baseline_mean=baseline_mean,
        item=row['item_id'],
        state=row['state_id'],
    )
    attach_belongs_to(graph, node_id, item_id=row['item_id'], state_id=row['state_id'])


def add_marketing_anomaly_nodes(graph, db_path, window=8, z_threshold=1.1):
    """
    Adds marketing_anomaly nodes: trailing z-score on marketing_spend, per
    channel x state x week.

    Node id deviates from the handoff doc's '{mkt_anom}_{channel}_{week_start}'
    convention by adding state_id: a channel+week combination spans both
    regions independently (315 of 630 channel/week combos in this dataset
    have anomalous spend in one region but not the other), so channel+week
    alone is not a unique key.
    """
    df = get_marketing_weekly(db_path)

    anomalies = detect_trailing_zscore(df, ['channel', 'state_id'], 'week_start_monday',
                                        'marketing_spend', window=window, threshold=z_threshold)

    for _, row in anomalies.iterrows():
        week_str = row['week_start_monday'].strftime('%Y-%m-%d')
        node_id = f"mkt_anom_{row['channel']}_{row['state_id']}_{week_str}"
        graph.add_node(
            node_id,
            kind='marketing_anomaly',
            channel=row['channel'],
            week_start=week_str,
            value=row['marketing_spend'],
            z=row['z'],
            region=row['region_name'],
            state=row['state_id'],
        )
        attach_belongs_to(graph, node_id, state_id=row['state_id'], channel=row['channel'])

    return graph


def add_supply_anomaly_nodes(graph, db_path, fill_rate_threshold=0.90, stockout_threshold=2):
    """
    Adds supply_anomaly nodes: fixed-rule flag (fill_rate < threshold or
    stockout_days >= threshold), per warehouse_sku x state x month.

    Node id deviates from the handoff doc's '{supply_anom}_{month}' convention
    by adding warehouse_sku and state_id: the same warehouse_sku reports
    independently in both CA and TX each month (72 warehouse/month combos in
    this dataset span more than one state), so month alone is not unique.
    """
    df = get_supply_monthly(db_path)

    anomalies = detect_supply_rule(df, fill_rate_threshold=fill_rate_threshold,
                                    stockout_threshold=stockout_threshold)

    for _, row in anomalies.iterrows():
        node_id = f"supply_anom_{row['warehouse_sku']}_{row['state_id']}_{row['month']}"
        graph.add_node(
            node_id,
            kind='supply_anomaly',
            month=row['month'],
            fill_rate=row['fill_rate'],
            stockout_days=row['stockout_days'],
            state=row['state_id'],
            warehouse_sku=row['warehouse_sku'],
        )
        attach_belongs_to(graph, node_id, state_id=row['state_id'], warehouse_sku=row['warehouse_sku'])

    return graph


def add_inventory_anomaly_nodes(graph, db_path, window=8, z_threshold=1.1):
    """
    Adds inventory_anomaly nodes: trailing z-score on daily inventory
    turnover ratio (COGS / inventory value), per item x state, sourced from
    inventory_logs.

    A distinct node kind from sales_anomaly (unlike gross_margin_percent,
    which reuses sales_anomaly since it's computed from the same
    get_item_state_daily source) -- turnover is about warehouse stock levels
    from a genuinely different source table, the same reasoning that already
    keeps marketing_anomaly/supply_anomaly separate from sales_anomaly.
    """
    df = get_inventory_turnover_daily(db_path)
    anomalies = detect_trailing_zscore(df, ['item_id', 'state_id'], 'date',
                                        'turnover_ratio', window=window, threshold=z_threshold)

    for _, row in anomalies.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d')
        node_id = f"turnover_anom_{row['item_id']}_{row['state_id']}_{date_str}"
        graph.add_node(
            node_id,
            kind='inventory_anomaly',
            date=date_str,
            item=row['item_id'],
            state=row['state_id'],
            warehouse_sku=row['warehouse_sku'],
            inventory_on_hand=row['inventory_on_hand'],
            turnover_ratio=round(row['turnover_ratio'], 4),
            z=row['z'],
            baseline_mean=row['baseline_mean'],
        )
        attach_belongs_to(graph, node_id, item_id=row['item_id'], state_id=row['state_id'],
                           warehouse_sku=row['warehouse_sku'])

    return graph


def add_event_nodes(graph, db_path):
    """
    Adds one event node per (date, event_name_1) pair, unconditionally --
    every calendar event in the data, regardless of whether any anomaly fired
    that day. Per section 3: no day/week/month hub node, temporal relations
    are expressed as direct edges (added separately in add_temporal_edges).
    """
    df = get_events(db_path)
    for _, row in df.iterrows():
        node_id = f"event_{row['date']}"
        graph.add_node(
            node_id,
            kind='event',
            date=row['date'],
            event_name=row['event_name_1'],
            event_type=row['event_type_1'],
        )
        attach_belongs_to(graph, node_id, event_name=row['event_name_1'])
    return graph


def add_review_shift_nodes(graph, db_path, window=3, threshold=1.1):
    """
    Adds review_shift nodes: a trailing z-score on mean monthly sentiment,
    per item x state, flagging months where review sentiment moves sharply
    from its recent trend.

    Window reduced from the sales-side default of 8 to 3: with ~100 reviews
    spread across 3 items x 2 states x 24 months, most (item, state) series
    have only 7-14 non-empty months total, so a window of 8 would almost
    never accumulate enough history to score anything. Bins are built only
    from months with an actual review (see get_feedback_monthly_sentiment) --
    no zero-filled gap months, so this is a z-score over real observations,
    not calendar time.
    """
    df = get_feedback_monthly_sentiment(db_path)

    shifts = detect_trailing_zscore(df, ['item_id', 'state_id'], 'month_date',
                                     'mean_sentiment', window=window, threshold=threshold)

    for _, row in shifts.iterrows():
        node_id = f"review_shift_{row['item_id']}_{row['state_id']}_{row['month']}"
        direction = 'turns_positive' if row['z'] > 0 else 'turns_negative'
        graph.add_node(
            node_id,
            kind='review_shift',
            item=row['item_id'],
            state=row['state_id'],
            month=row['month'],
            mean_sentiment=row['mean_sentiment'],
            z=row['z'],
            review_count=row['review_count'],
            direction=direction,
        )
        attach_belongs_to(graph, node_id, item_id=row['item_id'], state_id=row['state_id'])

    return graph


def add_explains_edges(graph, db_path):
    """
    PVM day-over-day decomposition for every revenue_anomaly node, per
    section 4:

        price_effect       = d_price * units[t-1]
        volume_effect      = d_units * price[t-1]
        interaction_effect = d_price * d_units
        total == actual_delta  (exact algebraic identity, always)

    Decomposition values are stored as attributes on the revenue_anomaly node
    regardless. An 'explains' edge to the matching units_anomaly / sell_price
    anomaly node is only added when that node actually exists in the graph
    (i.e. that column independently crossed its own threshold that day) --
    there is no non-anomalous node to attach the edge to otherwise.

    Returns the count of decompositions whose total did not match the actual
    revenue delta to the cent (expected to be zero -- any mismatch indicates
    a bug, not a data issue, since this is pure algebra).
    """
    df = get_item_state_daily(db_path).sort_values(['item_id', 'state_id', 'date'])
    df['prev_units'] = df.groupby(['item_id', 'state_id'])['units'].shift(1)
    df['prev_price'] = df.groupby(['item_id', 'state_id'])['sell_price'].shift(1)
    df['prev_revenue'] = df.groupby(['item_id', 'state_id'])['revenue'].shift(1)
    df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
    df_idx = df.set_index(['item_id', 'state_id', 'date_str'])

    revenue_nodes = [(n, a) for n, a in graph.nodes(data=True)
                      if a.get('kind') == 'sales_anomaly' and a.get('column') == 'revenue']

    mismatches = 0
    for node_id, attrs in revenue_nodes:
        key = (attrs['item'], attrs['state'], attrs['date'])
        if key not in df_idx.index:
            continue
        row = df_idx.loc[key]
        if pd.isna(row['prev_units']) or pd.isna(row['prev_price']) or pd.isna(row['prev_revenue']):
            continue

        d_units = row['units'] - row['prev_units']
        d_price = row['sell_price'] - row['prev_price']

        price_effect = d_price * row['prev_units']
        volume_effect = d_units * row['prev_price']
        interaction_effect = d_price * d_units

        total = price_effect + volume_effect + interaction_effect
        actual_delta = row['revenue'] - row['prev_revenue']

        if abs(total - actual_delta) > 0.01:
            mismatches += 1

        graph.nodes[node_id]['price_effect'] = round(price_effect, 4)
        graph.nodes[node_id]['volume_effect'] = round(volume_effect, 4)
        graph.nodes[node_id]['interaction_effect'] = round(interaction_effect, 4)
        graph.nodes[node_id]['total_decomposed'] = round(total, 4)
        graph.nodes[node_id]['actual_delta'] = round(actual_delta, 4)

        denom = abs(price_effect) + abs(volume_effect) + abs(interaction_effect)
        if denom == 0:
            continue

        units_node = f"units_anom_{attrs['item']}_{attrs['state']}_{attrs['date']}"
        price_node = f"sell_price_anom_{attrs['item']}_{attrs['state']}_{attrs['date']}"

        if graph.has_node(units_node):
            graph.add_edge(node_id, units_node, relation='explains', driver='volume',
                            dollar_effect=round(volume_effect, 4), weight=round(abs(volume_effect) / denom, 4))
        if graph.has_node(price_node):
            graph.add_edge(node_id, price_node, relation='explains', driver='price',
                            dollar_effect=round(price_effect, 4), weight=round(abs(price_effect) / denom, 4))

    return mismatches


def add_temporal_edges(graph):
    """
    Wires the temporal edge types from section 3 (no day/week/month hub --
    direct typed edges between anomaly/event nodes):

      co_occurs_same_day: event -> sales_anomaly, same date
      same_week:          (sales_anomaly | event) -> marketing_anomaly
      same_month:         (sales_anomaly | event) -> supply_anomaly
      same_month:         (sales_anomaly | event) -> review_shift (extension:
                           review_shift is monthly-grain like supply_anomaly,
                           so it gets the same rule)
      same_month:          marketing_anomaly -> supply_anomaly / review_shift,
                           when the marketing week falls within that month

    None of these are entity-filtered (matches the doc's literal same_week /
    same_month rules) -- finer-grained item/state filtering is available
    downstream via each node's belongs_to edges.
    """
    event_nodes = [(n, a) for n, a in graph.nodes(data=True) if a['kind'] == 'event']
    sales_nodes = [(n, a) for n, a in graph.nodes(data=True) if a['kind'] == 'sales_anomaly']
    inventory_nodes = [(n, a) for n, a in graph.nodes(data=True) if a['kind'] == 'inventory_anomaly']
    mkt_nodes = [(n, a) for n, a in graph.nodes(data=True) if a['kind'] == 'marketing_anomaly']
    supply_nodes = [(n, a) for n, a in graph.nodes(data=True) if a['kind'] == 'supply_anomaly']
    review_nodes = [(n, a) for n, a in graph.nodes(data=True) if a['kind'] == 'review_shift']

    # inventory_anomaly shares sales_anomaly's daily grain, so it gets the
    # same co_occurs_same_day / same_week / same_month treatment throughout.
    daily_nodes = sales_nodes + inventory_nodes + event_nodes

    sales_by_date = defaultdict(list)
    for n, a in sales_nodes + inventory_nodes:
        sales_by_date[a['date']].append(n)
    for n, a in event_nodes:
        for target in sales_by_date.get(a['date'], []):
            graph.add_edge(n, target, relation='co_occurs_same_day')

    parsed = [(n, a, datetime.strptime(a['date'], '%Y-%m-%d')) for n, a in daily_nodes]

    for mn, ma in mkt_nodes:
        week_start = datetime.strptime(ma['week_start'], '%Y-%m-%d')
        week_end = week_start + timedelta(days=6)
        for n, a, d in parsed:
            if week_start <= d <= week_end:
                graph.add_edge(n, mn, relation='same_week')

    for sn, sa in supply_nodes:
        month = sa['month']
        for n, a, d in parsed:
            if a['date'][:7] == month:
                graph.add_edge(n, sn, relation='same_month')

    for rn, ra in review_nodes:
        month = ra['month']
        for n, a, d in parsed:
            if a['date'][:7] == month:
                graph.add_edge(n, rn, relation='same_month')

    for mn, ma in mkt_nodes:
        week_month = ma['week_start'][:7]
        for sn, sa in supply_nodes:
            if week_month == sa['month']:
                graph.add_edge(mn, sn, relation='same_month')
        for rn, ra in review_nodes:
            if week_month == ra['month']:
                graph.add_edge(mn, rn, relation='same_month')

    return graph


def build_graph(db_path, window=8, z_threshold=1.1, price_threshold=0.03,
                 fill_rate_threshold=0.90, stockout_threshold=2,
                 review_window=3, review_threshold=1.1,
                 turnover_window=8, turnover_z_threshold=1.1):
    graph = build_entity_graph(db_path)
    add_sales_anomaly_nodes(graph, db_path, window=window,
                             z_threshold=z_threshold, price_threshold=price_threshold)
    add_marketing_anomaly_nodes(graph, db_path, window=window, z_threshold=z_threshold)
    add_supply_anomaly_nodes(graph, db_path, fill_rate_threshold=fill_rate_threshold,
                              stockout_threshold=stockout_threshold)
    add_inventory_anomaly_nodes(graph, db_path, window=turnover_window, z_threshold=turnover_z_threshold)
    add_event_nodes(graph, db_path)
    add_review_shift_nodes(graph, db_path, window=review_window, threshold=review_threshold)
    mismatches = add_explains_edges(graph, db_path)
    add_temporal_edges(graph)
    graph.graph['pvm_mismatches'] = mismatches
    return graph

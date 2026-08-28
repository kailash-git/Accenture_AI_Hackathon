"""
src/analytics/anomaly_detector.py
Statistical Anomaly Detector for tracking KPI movements (Revenue, Gross Margin %, Inventory Turnover).
"""

import sqlite3
import numpy as np
import pandas as pd

class AnomalyDetector:
    def __init__(self, db_path):
        self.db_path = db_path

    def _load_kpi_series(self, kpi_name, time_grain):
        """Loads and aggregates the raw per-period KPI series for every (item, state)
        pair -- shared by both run_detection() and compute_period_stats() so the two
        never compute the underlying numbers differently."""
        conn = sqlite3.connect(self.db_path)
        try:
            if kpi_name == "Revenue" or kpi_name == "GrossMarginPercent":
                query = """
                SELECT date, item_id, state_id, revenue, cost_of_goods_sold
                FROM fact_sales_daily
                """
                df = pd.read_sql_query(query, conn)
                df['date'] = pd.to_datetime(df['date'])

                if time_grain == "monthly":
                    df['period'] = df['date'].dt.to_period('M')
                elif time_grain == "weekly":
                    df['period'] = df['date'].dt.to_period('W-MON')
                else:
                    df['period'] = df['date'].dt.to_period('D')

                grouped = df.groupby(['period', 'item_id', 'state_id']).agg({
                    'revenue': 'sum',
                    'cost_of_goods_sold': 'sum'
                }).reset_index()

                # Formulate Gross Margin % at period level (not average of daily percentages)
                grouped['gross_margin_percent'] = (grouped['revenue'] - grouped['cost_of_goods_sold']) / grouped['revenue']
                grouped['gross_margin_percent'] = grouped['gross_margin_percent'].fillna(0.0)

                if kpi_name == "Revenue":
                    grouped['kpi_value'] = grouped['revenue']
                else:
                    grouped['kpi_value'] = grouped['gross_margin_percent']

            elif kpi_name == "InventoryTurnover":
                query = """
                SELECT il.date, il.item_id, il.state_id, il.inventory_on_hand, sl.supplier_raw_cost,
                       COALESCE(fs.cost_of_goods_sold, 0.0) as cost_of_goods_sold
                FROM inventory_logs il
                JOIN sku_lookup sl ON il.item_id = sl.item_id
                LEFT JOIN fact_sales_daily fs ON il.date = fs.date AND il.item_id = fs.item_id
                """
                df = pd.read_sql_query(query, conn)
                df['date'] = pd.to_datetime(df['date'])
                df['inventory_value'] = df['inventory_on_hand'] * df['supplier_raw_cost']

                if time_grain == "monthly":
                    df['period'] = df['date'].dt.to_period('M')
                else:
                    df['period'] = df['date'].dt.to_period('W-MON')

                grouped = df.groupby(['period', 'item_id', 'state_id']).agg({
                    'cost_of_goods_sold': 'sum',
                    'inventory_value': 'mean'
                }).reset_index()

                grouped['kpi_value'] = grouped['cost_of_goods_sold'] / grouped['inventory_value']
                grouped['kpi_value'] = grouped['kpi_value'].fillna(0.0)

            else:
                raise ValueError(f"Unknown KPI: {kpi_name}")
        finally:
            conn.close()

        return grouped.sort_values(by=['item_id', 'state_id', 'period']).reset_index(drop=True)

    def _rolling_stats_for_group(self, group, i, time_grain, window_periods):
        """
        The rolling z-score/baseline/confidence computation for a single row `i` within
        one (item, state) group's chronologically-sorted series. This is the exact same
        math run_detection has always used (seasonal YoY tempering included) -- factored
        out so a specific (item, state, period) can be scored on demand (for the
        evidence-driven pass) without re-deriving it, and without re-scanning the whole
        table via run_detection's threshold-filtered loop just to get one period's stats.
        """
        current_val = group.loc[i, 'kpi_value']

        start_idx = max(0, i - window_periods)
        end_idx = i  # exclusive of current

        has_seasonal = time_grain == "monthly" and i >= 12

        if end_idx - start_idx < 3:
            z_score = 0.0
            mean_val = current_val
            confidence = 50.0
        else:
            baseline_vals = group.loc[start_idx:end_idx - 1, 'kpi_value'].values
            mean_val = np.mean(baseline_vals)
            std_val = np.std(baseline_vals)

            if std_val < 1e-5:
                z_score = 0.0
            else:
                z_score = (current_val - mean_val) / std_val
            confidence = 90.0

            if has_seasonal:
                seasonal_diff = current_val - group.loc[i - 12, 'kpi_value']
                yoy_baseline = [group.loc[j, 'kpi_value'] - group.loc[j - 12, 'kpi_value'] for j in range(12, i) if j >= 12]
                if len(yoy_baseline) >= 3:
                    yoy_mean = np.mean(yoy_baseline)
                    yoy_std = np.std(yoy_baseline)
                    if yoy_std > 1e-5:
                        yoy_z = (seasonal_diff - yoy_mean) / yoy_std
                        z_score = 0.7 * yoy_z + 0.3 * z_score
                        confidence = 95.0

        direction = "UP" if current_val > mean_val else "DOWN"
        deviation_pct = (current_val - mean_val) / mean_val if mean_val > 0 else 0.0

        if abs(z_score) > 3.0:
            severity = "CRITICAL"
        elif abs(z_score) > 2.0:
            severity = "WARNING"
        else:
            severity = "ACTIVE"

        return {
            "actual_value": float(current_val),
            "baseline_value": float(mean_val),
            "deviation_pct": float(deviation_pct),
            "z_score": float(z_score),
            "direction": direction,
            "severity": severity,
            "confidence": float(confidence),
        }

    def run_detection(self, kpi_name="Revenue", time_grain="monthly", window_periods=8, threshold=2.0):
        """
        Run statistical rolling z-score detection on the specified KPI and grain.
        Unchanged public behavior/return shape -- still only returns rows clearing
        abs(z_score) >= threshold. This is the STATISTICAL detection signal; it is
        deliberately never lowered or bypassed to admit curated/evidence-driven
        scenarios (see evidence_signal.py for the independent evidence-driven signal).
        """
        grouped = self._load_kpi_series(kpi_name, time_grain)
        anomalies_detected = []

        for (item_id, state_id), group in grouped.groupby(['item_id', 'state_id']):
            group = group.reset_index(drop=True)
            for i in range(len(group)):
                stats = self._rolling_stats_for_group(group, i, time_grain, window_periods)
                if abs(stats["z_score"]) >= threshold:
                    anomalies_detected.append({
                        "kpi_name": kpi_name,
                        "item_id": item_id,
                        "state_id": state_id,
                        "period": str(group.loc[i, 'period']),
                        "time_grain": time_grain,
                        **stats,
                    })

        return anomalies_detected

    def compute_period_stats(self, kpi_name, item_id, state_id, period, time_grain="monthly", window_periods=8):
        """
        Returns the real actual/baseline/z_score/deviation/confidence/severity for one
        specific (kpi, item, state, period) -- regardless of whether abs(z_score) would
        clear the statistical detection threshold. This is what lets the evidence-driven
        pass ask "what do the real numbers look like here?" for a period unstructured
        evidence points at, without inventing or approximating a value. Returns None if
        that (item, state) has no data for the period, or fewer than the minimum history
        needed to place it in the series at all.
        """
        grouped = self._load_kpi_series(kpi_name, time_grain)
        period_obj = pd.Period(period, freq='M' if time_grain == "monthly" else ('W-MON' if time_grain == "weekly" else 'D'))

        group = grouped[(grouped['item_id'] == item_id) & (grouped['state_id'] == state_id)].reset_index(drop=True)
        if group.empty:
            return None

        matches = group.index[group['period'] == period_obj]
        if len(matches) == 0:
            return None
        i = int(matches[0])

        stats = self._rolling_stats_for_group(group, i, time_grain, window_periods)
        return {
            "kpi_name": kpi_name,
            "item_id": item_id,
            "state_id": state_id,
            "period": str(group.loc[i, 'period']),
            "time_grain": time_grain,
            **stats,
        }

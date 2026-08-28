"""
src/analytics/pvm_analyzer.py
Price-Volume-Mix (PVM) Variance Analyzer for reconciling revenue deviations.
"""

import sqlite3
import pandas as pd
import numpy as np

class PvmAnalyzer:
    def __init__(self, db_path):
        self.db_path = db_path

    def analyze_variance(self, state_id, period_start, period_end, time_grain="monthly",
                          baseline_periods=8, item_id=None):
        """
        Compare the anomaly period (current) against the baseline period.
        Decompose the revenue change into Price, Volume, and Mix effects.

        `item_id` scopes the decomposition to a single SKU, matching the exact
        (item_id, state_id, period) granularity the anomaly detector itself flags
        anomalies at. Without it, this blends in every other SKU sold in the state
        that month -- e.g. a -1.1% dip in one SKU's own revenue can sit next to a
        "+$628 price effect" that's actually dominated by a *different* SKU's price
        move in the same region, because the two figures were computed over
        different scopes while being narrated as if they described the same thing.
        Passing item_id makes the returned volume/price/mix/other effects sum to
        exactly that SKU's own actual-minus-baseline delta, so the PVM breakdown
        actually explains the anomaly it's attached to.
        """
        conn = sqlite3.connect(self.db_path)

        # Determine the current month period
        current_pd = pd.Period(period_start, freq='M' if time_grain == "monthly" else 'W-MON')

        # Baseline periods: previous N periods
        baseline_pds = [str(current_pd - i) for i in range(1, baseline_periods + 1)]

        # Load daily sales
        if item_id:
            query = """
            SELECT date, item_id, state_id, units, sell_price, revenue
            FROM fact_sales_daily
            WHERE state_id = ? AND item_id = ?
            """
            df = pd.read_sql_query(query, conn, params=(state_id, item_id))
        else:
            query = """
            SELECT date, item_id, state_id, units, sell_price, revenue
            FROM fact_sales_daily
            WHERE state_id = ?
            """
            df = pd.read_sql_query(query, conn, params=(state_id,))
        df['date'] = pd.to_datetime(df['date'])
        df['period'] = df['date'].dt.to_period('M' if time_grain == "monthly" else 'W-MON').astype(str)
        
        conn.close()
        
        if df.empty:
            return self._empty_pvm_result()
            
        # Group by period and item
        grouped = df.groupby(['period', 'item_id']).agg({
            'units': 'sum',
            'revenue': 'sum'
        }).reset_index()
        
        # Separate current and baseline
        df_current = grouped[grouped['period'] == str(current_pd)]
        df_baseline = grouped[grouped['period'].isin(baseline_pds)]
        
        if df_current.empty or df_baseline.empty:
            return self._empty_pvm_result()
            
        # Compute baseline average quantity and revenue per item (mean over baseline periods)
        # We group by item_id and divide by number of active periods
        num_baseline_periods = len(df_baseline['period'].unique())
        if num_baseline_periods == 0:
            num_baseline_periods = 1
            
        df_baseline_agg = df_baseline.groupby('item_id').agg({
            'units': 'sum',
            'revenue': 'sum'
        }).reset_index()
        
        # Baseline values are averages per period
        df_baseline_agg['units'] = df_baseline_agg['units'] / num_baseline_periods
        df_baseline_agg['revenue'] = df_baseline_agg['revenue'] / num_baseline_periods
        df_baseline_agg['price'] = df_baseline_agg['revenue'] / df_baseline_agg['units']
        df_baseline_agg['price'] = df_baseline_agg['price'].fillna(0.0)
        
        # Current period values
        df_current_agg = df_current.groupby('item_id').agg({
            'units': 'sum',
            'revenue': 'sum'
        }).reset_index()
        df_current_agg['price'] = df_current_agg['revenue'] / df_current_agg['units']
        df_current_agg['price'] = df_current_agg['price'].fillna(0.0)
        
        # Join current and baseline to compare
        m = pd.merge(df_baseline_agg, df_current_agg, on='item_id', how='outer', suffixes=('_0', '_1'))
        
        # Handle new and discontinued products
        m['units_0'] = m['units_0'].fillna(0.0)
        m['revenue_0'] = m['revenue_0'].fillna(0.0)
        m['units_1'] = m['units_1'].fillna(0.0)
        m['revenue_1'] = m['revenue_1'].fillna(0.0)
        
        # If price is missing for baseline, use current price (no price effect)
        m['price_0'] = m['price_0'].fillna(m['price_1'])
        # If price is missing for current, use baseline price (no price effect)
        m['price_1'] = m['price_1'].fillna(m['price_0'])
        
        # Totals
        Q_0 = m['units_0'].sum()
        Q_1 = m['units_1'].sum()
        R_0 = m['revenue_0'].sum()
        R_1 = m['revenue_1'].sum()
        
        if Q_0 == 0 or Q_1 == 0:
            return self._empty_pvm_result()
            
        # Shares
        m['S_0'] = m['units_0'] / Q_0
        m['S_1'] = m['units_1'] / Q_1
        
        # Average baseline price
        P_bar_0 = R_0 / Q_0
        
        # PVM components
        # 1. Price Effect = SUM( Q_1,i * (P_1,i - P_0,i) )
        m['price_effect'] = m['units_1'] * (m['price_1'] - m['price_0'])
        price_effect_total = m['price_effect'].sum()
        
        # 2. Volume Effect = (Q_1 - Q_0) * P_bar_0
        # Distributed as (Q_1 - Q_0) * S_0,i * P_0,i
        m['volume_effect'] = (Q_1 - Q_0) * m['S_0'] * m['price_0']
        volume_effect_total = (Q_1 - Q_0) * P_bar_0
        
        # 3. Mix Effect = SUM( (S_1,i - S_0,i) * Q_1 * (P_0,i - P_bar_0) )
        m['mix_effect'] = (m['S_1'] - m['S_0']) * Q_1 * (m['price_0'] - P_bar_0)
        mix_effect_total = m['mix_effect'].sum()
        
        # Reconciliation check
        sum_effects = price_effect_total + volume_effect_total + mix_effect_total
        delta_rev = R_1 - R_0
        
        # We can capture any floating-point/rounding residual in 'other'
        other_effect_total = delta_rev - sum_effects
        
        # Formulate percentage contributions
        total_abs = abs(price_effect_total) + abs(volume_effect_total) + abs(mix_effect_total) + abs(other_effect_total)
        if total_abs == 0:
            total_abs = 1.0
            
        def pct_str(val):
            return f"{round(abs(val) / total_abs * 100)}%"
            
        # Generate item breakdown
        products_breakdown = []
        for idx, row in m.iterrows():
            item_id = row['item_id']
            item_rev_change = row['revenue_1'] - row['revenue_0']
            vol_delta_pct = (row['units_1'] - row['units_0']) / row['units_0'] if row['units_0'] > 0 else 0.0
            
            # Label primary status
            if abs(item_rev_change) > 0.5 * abs(delta_rev):
                status = "Primary Driver"
            elif abs(item_rev_change) > 0.1 * abs(delta_rev):
                status = "Secondary"
            else:
                status = "Inelastic"
                
            products_breakdown.append({
                "sku": item_id,
                "volumeDelta": f"{'+' if vol_delta_pct >= 0 else ''}{round(vol_delta_pct * 100)}%",
                "revenueImpact": f"{'-' if item_rev_change < 0 else '+'}${abs(round(item_rev_change)):,}",
                "status": status
            })
            
        # Explanations
        vol_expl = f"Volume changes explain {pct_str(volume_effect_total)} of total revenue variance."
        if volume_effect_total < 0:
            vol_expl = f"Volume contraction explains {pct_str(volume_effect_total)} of total revenue decline."
            
        price_expl = f"Pricing modifications explain {pct_str(price_effect_total)} of total revenue variance."
        if price_effect_total < 0:
            price_expl = f"Average selling price softened due to promotional markdowns."
        elif price_effect_total > 0:
            price_expl = f"Average selling price appreciated, contributing positively."
            
        mix_expl = f"Quantity shift between products explains {pct_str(mix_effect_total)} of the change."
        if mix_effect_total < 0:
            mix_expl = f"Unfavorable shift toward lower-margin or lower-priced items."
            
        return {
            "volume": {"val": float(volume_effect_total), "pct": pct_str(volume_effect_total), "expl": vol_expl},
            "price": {"val": float(price_effect_total), "pct": pct_str(price_effect_total), "expl": price_expl},
            "mix": {"val": float(mix_effect_total), "pct": pct_str(mix_effect_total), "expl": mix_expl},
            "other": {"val": float(other_effect_total), "pct": pct_str(other_effect_total), "expl": "Minor residual rounding effect."},
            "products": products_breakdown,
            "baseline_revenue": float(R_0),
            "actual_revenue": float(R_1)
        }

    def _empty_pvm_result(self):
        return {
            "volume": {"val": 0.0, "pct": "0%", "expl": "No volume variance computed."},
            "price": {"val": 0.0, "pct": "0%", "expl": "No price variance computed."},
            "mix": {"val": 0.0, "pct": "0%", "expl": "No mix variance computed."},
            "other": {"val": 0.0, "pct": "0%", "expl": "No other variance computed."},
            "products": [],
            "baseline_revenue": 0.0,
            "actual_revenue": 0.0
        }

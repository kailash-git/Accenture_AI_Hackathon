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
        
        # ------------------------------------------------------------------ #
        # Contribution framing.
        #
        # `pct` is now the SIGNED share of *baseline revenue* -- it is always
        # additive (volume% + price% + mix% + other% == deviation%) and never
        # misleads when drivers oppose each other. The old convention,
        # |effect| / sum(|effects|), reported "84% volume / 16% price" even when
        # a +$13.3k volume gain was nearly cancelled by a -$9.9k price drop,
        # which reads as "volume drove 84% of the move" when the net move was
        # tiny. `share_of_change` keeps the "which driver dominates" signal
        # (signed share of the NET delta -- can exceed 100% / go negative when
        # drivers fight), and `driver_summary` states it correctly in one line.
        # ------------------------------------------------------------------ #
        gross_abs = (abs(price_effect_total) + abs(volume_effect_total)
                     + abs(mix_effect_total) + abs(other_effect_total)) or 1.0

        def _signed_money(val):
            return f"{'+' if val >= 0 else '-'}${abs(round(val)):,}"

        def pct_of_baseline(val):
            if R_0 == 0:
                return "0%"
            p = val / R_0 * 100.0
            return "0%" if abs(p) < 0.5 else f"{'+' if p >= 0 else '-'}{abs(p):.0f}%"

        def share_of_change(val):
            if abs(delta_rev) < 1e-9:
                return "0%"
            s = val / delta_rev * 100.0
            return f"{'+' if s >= 0 else '-'}{abs(s):.0f}%"

        def is_material(val):
            return abs(val) / gross_abs >= 0.01

        def direction_of(val):
            return "none" if not is_material(val) else ("increase" if val > 0 else "decrease")

        # Keep the legacy signature working (a couple of call sites still call
        # pct_str) -- but it now returns the honest signed-of-baseline figure.
        def pct_str(val):
            return pct_of_baseline(val)

        _effects = {
            "volume": volume_effect_total, "price": price_effect_total,
            "mix": mix_effect_total, "other": other_effect_total,
        }
        drivers_opposing = len({(1 if v >= 0 else -1) for v in _effects.values() if is_material(v)}) > 1
        dominant_driver = max(_effects, key=lambda k: abs(_effects[k]))

        _dev_pct = (delta_rev / R_0 * 100.0) if R_0 else 0.0
        _head = (f"Revenue {'rose' if delta_rev >= 0 else 'fell'} {abs(_dev_pct):.1f}% "
                 f"(${abs(round(R_0)):,} → ${abs(round(R_1)):,}).")
        _named = [k for k in ("volume", "price", "mix") if is_material(_effects[k])]
        _named.sort(key=lambda k: -abs(_effects[k]))
        if not _named:
            driver_summary = _head + " No single price/volume/mix driver was material."
        elif drivers_opposing:
            driver_summary = (
                _head + " The drivers pulled in opposite directions: "
                + "; ".join(f"{k} {_signed_money(_effects[k])}" for k in _named)
                + f", for a net {_signed_money(delta_rev)}. "
                + f"{dominant_driver.capitalize()} was the larger force."
            )
        else:
            driver_summary = (
                _head + " "
                + "; ".join(f"{k.capitalize()} {_signed_money(_effects[k])} "
                            f"({pct_of_baseline(_effects[k])} of baseline)" for k in _named)
                + f". {dominant_driver.capitalize()} is the dominant driver."
            )

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
            
        # Per-effect explanations -- state the signed contribution, don't claim a
        # driver "explains X% of the decline" (false when another driver offsets it).
        def _effect_expl(name, val, extra_when_negative="", extra_when_positive=""):
            if not is_material(val):
                return f"{name.capitalize()} had no material effect this period."
            verb = "added" if val > 0 else "reduced revenue by"
            base = (f"{name.capitalize()} {verb} {_signed_money(val)} "
                    f"({pct_of_baseline(val)} of baseline revenue).")
            tail = extra_when_positive if val > 0 else extra_when_negative
            return f"{base} {tail}".strip()

        vol_expl = _effect_expl("volume", volume_effect_total)
        price_expl = _effect_expl(
            "price", price_effect_total,
            extra_when_negative="Average selling price softened, consistent with promotional markdowns.",
            extra_when_positive="Average selling price appreciated.",
        )
        mix_expl = _effect_expl("mix", mix_effect_total,
                                extra_when_negative="Shift toward lower-priced items.")

        def _leg(val, expl):
            return {
                "val": float(val),
                "pct": pct_of_baseline(val),          # signed, additive to deviation%
                "share_of_change": share_of_change(val),  # signed share of the NET delta
                "direction": direction_of(val),
                "expl": expl,
            }

        return {
            "volume": _leg(volume_effect_total, vol_expl),
            "price": _leg(price_effect_total, price_expl),
            "mix": _leg(mix_effect_total, mix_expl),
            "other": _leg(other_effect_total, "Minor residual / rounding effect."),
            "products": products_breakdown,
            "baseline_revenue": float(R_0),
            "actual_revenue": float(R_1),
            "dominant_driver": dominant_driver,
            "drivers_opposing": bool(drivers_opposing),
            "driver_summary": driver_summary,
        }

    def _empty_pvm_result(self):
        _leg = lambda: {"val": 0.0, "pct": "0%", "share_of_change": "0%",
                        "direction": "none", "expl": "Not computed for this KPI/period."}
        return {
            "volume": _leg(), "price": _leg(), "mix": _leg(), "other": _leg(),
            "products": [],
            "baseline_revenue": 0.0,
            "actual_revenue": 0.0,
            "dominant_driver": None,
            "drivers_opposing": False,
            "driver_summary": "",
        }

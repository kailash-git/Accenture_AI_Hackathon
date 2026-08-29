import numpy as np
import pandas as pd


def detect_trailing_zscore(df, group_cols, date_col, value_col, window=8, threshold=1.1):
    """
    Trailing z-score anomaly detection at the series' native grain.

    For row i, uses the prior `window` rows (i-window .. i-1, excluding i) within
    the same group to compute a population mean/std, then z = (value_i - mean) / std.

    Cold start handling: a row is only scored once a FULL prior window of history
    exists in its group. Rows with fewer than `window` prior rows (insufficient
    history) are excluded from the output entirely -- not scored against a partial
    window, and not treated as z=0. Windows with zero variance (std=0) are also
    excluded rather than producing an infinite/undefined z-score.

    The zero-variance check uses an epsilon (1e-9), not a literal std > 0:
    confirmed directly against real data that a window of float64 values which
    are algebraically identical (e.g. 8 consecutive days of gross_margin_percent
    all equal to 0.3125) can still produce a std like 1.96e-17 rather than
    exactly 0.0, purely from floating-point rounding in how each day's ratio
    was computed -- std(ddof=0) > 0 alone let that through, and a value that
    then genuinely differed (e.g. 0.3889) scored a z in the quadrillions
    (value / 1.96e-17) instead of being excluded as an undefined/degenerate
    window, same as an exact std=0 window already was.
    """
    ZERO_VARIANCE_EPSILON = 1e-9

    results = []
    for keys, group in df.groupby(group_cols, sort=False):
        group = group.sort_values(date_col).reset_index(drop=True)

        prior = group[value_col].shift(1)
        rolling_mean = prior.rolling(window=window, min_periods=window).mean()
        rolling_std = prior.rolling(window=window, min_periods=window).std(ddof=0)

        valid = rolling_std.notna() & (rolling_std > ZERO_VARIANCE_EPSILON)
        z = pd.Series(np.nan, index=group.index)
        z[valid] = (group[value_col][valid] - rolling_mean[valid]) / rolling_std[valid]

        flagged_mask = valid & (z.abs() >= threshold)
        flagged = group[flagged_mask].copy()
        flagged['z'] = z[flagged_mask]
        flagged['baseline_mean'] = rolling_mean[flagged_mask]
        flagged['column'] = value_col
        results.append(flagged)

    if not results:
        return pd.DataFrame()
    return pd.concat(results, ignore_index=True)


def detect_pct_change(df, group_cols, date_col, value_col, threshold=0.03):
    """
    Day-over-day percent change anomaly detection at the series' native grain.
    A row is only scored once a prior row exists in its group with a nonzero
    value; rows without a valid prior comparison are excluded, not treated as
    pct_chg=0.
    """
    results = []
    for keys, group in df.groupby(group_cols, sort=False):
        group = group.sort_values(date_col).reset_index(drop=True)

        prev = group[value_col].shift(1)
        valid = prev.notna() & (prev != 0)
        pct_chg = pd.Series(np.nan, index=group.index)
        pct_chg[valid] = (group[value_col][valid] - prev[valid]) / prev[valid]

        flagged_mask = valid & (pct_chg.abs() >= threshold)
        flagged = group[flagged_mask].copy()
        flagged['pct_chg'] = pct_chg[flagged_mask]
        flagged['column'] = value_col
        results.append(flagged)

    if not results:
        return pd.DataFrame()
    return pd.concat(results, ignore_index=True)


def detect_supply_rule(df, fill_rate_threshold=0.90, stockout_threshold=2):
    """
    Fixed-rule supply anomaly detector -- not a z-score. Flags a row if
    fill_rate drops below fill_rate_threshold OR stockout_days reaches
    stockout_threshold. No trailing window / cold-start concern applies
    here since each row is judged against a fixed rule, not its own history.
    """
    flagged_mask = (df['fill_rate'] < fill_rate_threshold) | (df['stockout_days'] >= stockout_threshold)
    return df[flagged_mask].copy()

"""
experiments/slice_benchmark.py

Evaluation set for the slice-attribution comparison (Adtributor vs the current
magnitude-ranked per-product breakdown). Mirrors the EasyRCA experiment:

* real_cases()      -- the labelled scenarios in the `anomalies` table, whose
                       responsible slice is known (supply/pricecut -> FOODS_3_090
                       in CA; billing -> FOODS_3_586 in TX).
* synthetic_cases() -- N portfolios (10 items x 2 regions x 3 channels) with a
                       forecast per slice and a deviation injected into a known
                       (dimension, elements); 30% also carry a "distractor" -- a
                       large slice whose magnitude grew but whose *share* did
                       not (the case Adtributor is designed to not be fooled by).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACC_DIR = os.path.join(BASE_DIR, "Accenture", "Accenture")
sys.path.insert(0, os.path.join(ACC_DIR, "src"))
DB_PATH = os.path.join(ACC_DIR, "data", "business_bi.db")

from analytics.adtributor import _MEASURE_COLS  # noqa: E402

SYN_DIMS = ["item_id", "state_id", "cat_id"]     # cat_id stands in for "channel"


@dataclass
class SliceCase:
    case_id: str
    kind: str                       # "real" | "synthetic"
    intervention: str               # "real" | "shock" | "none"
    gold_dim: str | None
    gold_elements: set
    has_distractor: bool = False
    distractor_element: str | None = None
    note: str = ""
    # payload
    kpi: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    frame: pd.DataFrame | None = None
    dims: list = field(default_factory=lambda: list(SYN_DIMS))


_REAL_GOLD = {
    "supply": ("state_id", {"CA"}, "Seattle warehouse stockout -> CA / FOODS_3_090."),
    "pricecut": ("item_id", {"FOODS_3_090"}, "Price cut on FOODS_3_090 in CA."),
    "billing": ("state_id", {"TX"}, "Register overcharge on FOODS_3_586 in TX."),
}


def real_cases(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cases = []
    for key, (gdim, gel, note) in _REAL_GOLD.items():
        r = conn.execute("SELECT * FROM anomalies WHERE scenario_key = ? LIMIT 1", (key,)).fetchone()
        if r is None or r["kpi_name"] not in ("Revenue", "GrossMarginPercent"):
            continue
        cases.append(SliceCase(
            case_id=key, kind="real", intervention="real",
            gold_dim=gdim, gold_elements=set(gel), note=note,
            kpi=r["kpi_name"], period_start=r["period_start"], period_end=r["period_end"],
            dims=["item_id", "state_id", "store_id", "cat_id"],
        ))
    conn.close()
    return cases


def _synth_frame(rng, n_items=10, regions=("R0", "R1"), chans=("C0", "C1", "C2")):
    rows = []
    base = {}
    for it in [f"I{i:02d}" for i in range(n_items)]:
        for rg in regions:
            for ch in chans:
                base[(it, rg, ch)] = rng.uniform(20.0, 200.0)
                rows.append((it, rg, ch))
    df = pd.DataFrame(rows, columns=["item_id", "state_id", "cat_id"])
    df["base"] = [base[t] for t in zip(df.item_id, df.state_id, df.cat_id)]
    return df, base


def synthetic_cases(n, seed=0):
    rng = np.random.default_rng(seed)
    cases = []
    for i in range(n):
        df, base = _synth_frame(rng)
        kind = rng.choice(["shock", "distractor", "none"], p=[0.55, 0.30, 0.15])

        f_rev = df["base"].to_numpy() * rng.normal(1.0, 0.03, len(df))
        a_rev = df["base"].to_numpy() * rng.normal(1.0, 0.06, len(df))

        gold_dim, gold_elements = None, set()
        has_distractor = False
        distractor_element = None

        if kind in ("shock", "distractor"):
            gold_dim = rng.choice(SYN_DIMS)
            elems = sorted(df[gold_dim].unique())
            sizes = df.groupby(gold_dim)["base"].sum().sort_values()
            small_order = list(sizes.index.astype(str))

            if kind == "distractor":
                # Data-Center-X vs Mobile/Tablet: a strong portfolio-wide drift
                # gives the LARGEST slice the biggest raw |delta| (a magnitude
                # ranker is lured to it), while a modest disproportionate shock
                # on the SMALLEST slice is what actually shifted a share.
                a_rev = a_rev * rng.uniform(0.70, 0.85)
                gold_elements = {small_order[0]}
                mult = rng.choice([rng.uniform(0.45, 0.65), rng.uniform(1.5, 1.9)])
                distractor_element = small_order[-1]
                has_distractor = True
            else:
                k = int(rng.integers(1, 3))
                gold_elements = set(rng.choice(elems, size=k, replace=False).tolist())
                mult = rng.choice([rng.uniform(0.25, 0.55), rng.uniform(1.6, 2.4)])

            mask = df[gold_dim].isin(gold_elements).to_numpy()
            a_rev = np.where(mask, a_rev * mult, a_rev)

        frame = df[SYN_DIMS].copy()
        # store_id not modelled synthetically; keep the three dims
        frame["f_revenue"] = f_rev
        frame["a_revenue"] = a_rev
        # cogs / units: proportional, so derived-measure code paths stay valid
        frame["f_cost_of_goods_sold"] = f_rev * 0.7
        frame["a_cost_of_goods_sold"] = a_rev * 0.7
        frame["f_units"] = f_rev / 5.0
        frame["a_units"] = a_rev / 5.0

        cases.append(SliceCase(
            case_id=f"syn{i:04d}", kind="synthetic", intervention=kind,
            gold_dim=gold_dim, gold_elements=gold_elements,
            has_distractor=has_distractor, distractor_element=distractor_element,
            note=f"{kind}", kpi="Revenue", frame=frame, dims=list(SYN_DIMS),
        ))
    return cases


def all_cases(n_synth=300, seed=0, db_path=DB_PATH):
    return real_cases(db_path) + synthetic_cases(n_synth, seed)

"""
experiments/benchmark.py  (B / Accenture_AI_Hackathon layout)

Evaluation set for "does causal RCA beat the current heuristic?" -- see
experiments/REPORT.md. Two parts:

* real_cases()      -- the labelled scenarios in the `anomalies` table
                       (supply / pricecut / billing / sparse), scored against
                       their stored period via easy_rca.derive_windows_for_period.
* synthetic_cases() -- N linear-Gaussian panels sampled from the summary
                       causal graph, each with one known injected root cause.

Every Case carries pre-computed normal/anomalous weekly windows so the
baseline and EasyRCA predictors are scored on identical inputs.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import networkx as nx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACC_DIR = os.path.join(BASE_DIR, "Accenture", "Accenture")
sys.path.insert(0, os.path.join(ACC_DIR, "src"))
DB_PATH = os.path.join(ACC_DIR, "data", "business_bi.db")
GRAPH_PATH = os.path.join(ACC_DIR, "data", "evidence_graph.gpickle")

from analytics.causal_graph import SUMMARY_CAUSAL_GRAPH, VARIABLES
from analytics.rca_series import build_weekly_panel
from analytics.easy_rca import derive_windows_for_period

_KPI_TO_VAR = {"Revenue": "revenue", "GrossMarginPercent": "gross_margin_percent",
               "InventoryTurnover": "inventory_turnover"}


@dataclass
class Case:
    case_id: str
    kind: str                       # "real" | "synthetic"
    intervention: str               # "real" | "structural_shock" | "mechanism_shift" | "none"
    gold: set
    gold_behaviour: str             # "attribute" | "abstain" | "signal"
    note: str = ""
    node_id: str | None = None
    item: str | None = None
    state: str | None = None
    normal_df: pd.DataFrame | None = None
    anom_df: pd.DataFrame | None = None
    anomalous_vars: list | None = None
    onsets: dict = field(default_factory=dict)
    target_var: str | None = None
    target_visible: bool | None = None


# scenario_key -> (gold set, behaviour, note)
_REAL_GOLD = {
    "supply": ({"fill_rate", "stockout_days"}, "attribute",
               "Port-of-Seattle carrier delay -> stockout."),
    "pricecut": ({"sell_price"}, "attribute",
                 "25% price cut; day-over-day PVM misses the pre-dated cut."),
    "billing": ({"sell_price", "sentiment"}, "signal",
                "Register overcharge: logged revenue looks fine, customers report the bug."),
    "sparse": (set(), "abstain", "No usable sales history -> nothing to attribute."),
}


def real_cases(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cases = []
    for key, (gold, behaviour, note) in _REAL_GOLD.items():
        row = conn.execute(
            "SELECT * FROM anomalies WHERE scenario_key = ? LIMIT 1", (key,)
        ).fetchone()
        if row is None:
            continue
        item, state = row["item_id"], row["state_id"]
        tv = _KPI_TO_VAR.get(row["kpi_name"])
        w = None
        if tv is not None:
            try:
                panel = build_weekly_panel(db_path, item, state)
                w = derive_windows_for_period(panel, tv, row["period_start"], row["period_end"])
            except Exception:
                w = None
        cases.append(Case(
            case_id=key, kind="real", intervention="real",
            gold=set(gold), gold_behaviour=behaviour, note=note,
            node_id=row["anomaly_id"], item=item, state=state,
            normal_df=(w or {}).get("normal_df"), anom_df=(w or {}).get("anom_df"),
            anomalous_vars=(w or {}).get("anomalous_vars"), onsets=(w or {}).get("onsets", {}),
            target_var=(w or {}).get("target_var"), target_visible=(w or {}).get("target_visible"),
        ))
    conn.close()
    return cases


_SHOCK_TARGETS = [v for v in VARIABLES if v != "event"]


def _simulate(cg, rng, T, base, coef, noise, shock=None, mech=None):
    order = list(nx.topological_sort(cg))
    data = {}
    for v in order:
        x = np.full(T, base[v], dtype=float) + noise[v]
        if shock and shock[0] == v:
            _, s, e, delta = shock
            x[s:e] += delta
        for u in cg.predecessors(v):
            c = np.full(T, coef[(u, v)], dtype=float)
            if mech and mech[0] == (u, v):
                _, s, e, mult = mech
                c[s:e] = coef[(u, v)] * mult
            x = x + c * data[u]
        data[v] = x
    return pd.DataFrame({v: data[v] for v in VARIABLES})


def synthetic_cases(n, seed=0, cg=None, T=140):
    cg = cg or SUMMARY_CAUSAL_GRAPH
    rng = np.random.default_rng(seed)
    edges = list(cg.edges())
    cases = []
    for i in range(n):
        base = {v: rng.uniform(5.0, 10.0) for v in VARIABLES}
        sig = {v: rng.uniform(0.4, 1.2) for v in VARIABLES}
        coef = {e: rng.uniform(0.35, 0.9) * rng.choice([-1.0, 1.0]) for e in edges}
        noise = {v: rng.normal(0.0, sig[v], T) for v in VARIABLES}
        s = int(rng.integers(T - 40, T - 12))
        length = int(rng.integers(3, 9))
        e = s + length
        kind = rng.choice(["structural_shock", "mechanism_shift", "none"], p=[0.4, 0.4, 0.2])
        shock = mech = None
        gold = set()
        if kind == "structural_shock":
            var = rng.choice(_SHOCK_TARGETS)
            shock = (var, s, e, rng.choice([-1.0, 1.0]) * rng.uniform(4.0, 8.0) * sig[var])
            gold = {var}
        elif kind == "mechanism_shift":
            p, c = edges[int(rng.integers(0, len(edges)))]
            mult = rng.choice([rng.uniform(2.5, 4.0), rng.uniform(-2.0, -0.5)])
            mech = ((p, c), s, e, float(mult))
            gold = {c}
        df = _simulate(cg, rng, T, base, coef, noise, shock=shock, mech=mech)
        cases.append(Case(
            case_id=f"syn{i:04d}", kind="synthetic", intervention=kind,
            gold=gold, gold_behaviour="abstain" if kind == "none" else "attribute",
            note=f"{kind} s={s} len={length}",
            normal_df=df.iloc[s - 12:s].reset_index(drop=True),
            anom_df=df.iloc[s:e].reset_index(drop=True),
            anomalous_vars=None, onsets={},
        ))
    return cases


def all_cases(n_synth=300, seed=0, db_path=DB_PATH):
    return real_cases(db_path) + synthetic_cases(n_synth, seed)

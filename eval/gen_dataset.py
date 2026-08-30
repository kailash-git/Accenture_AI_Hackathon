#!/usr/bin/env python3
"""
eval/gen_dataset.py -- build eval/dataset30.jsonl: 30 natural-language queries
spanning the brief's scenario types, each labelled from the live DB so the
expected behaviour (which movement it resolves to, whether it should abstain)
is ground truth, not a guess.
"""
import calendar
import json
import os
import sqlite3
import sys

# Windows consoles default to a legacy codepage; force UTF-8 so printing
# report text (arrows, non-breaking hyphens, box chars) never crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), "Accenture", "Accenture", "data", "business_bi.db")

KPI_WORD = {"Revenue": "revenue", "GrossMarginPercent": "gross margin",
            "InventoryTurnover": "inventory turnover"}
STATE_WORD = {"CA": "California", "TX": "Texas"}

# (type, role, key, question-template). {kpi}{item}{state}{mon}{yr} fill in.
PLAN = [
    # --- one per KPI, mixing abstain / non-abstain targets ---
    ("kpi_revenue",  "vp_sales",       "supply",                            "Why did {kpi} fall for {item} in {state} in {mon} {yr}?"),
    ("kpi_revenue",  "vp_sales",       "pricecut",                          "Explain the {kpi} movement for {item} in {state} in {mon} {yr}."),
    ("kpi_revenue",  "vp_sales",       "gen-FOODS_3_090-2012-10-CA",        "What happened to {kpi} for {item} in {state} in {mon} {yr}?"),
    ("kpi_revenue",  "vp_sales",       "gen-FOODS_3_090-2011-09-CA",        "There's a {kpi} spike for {item} in {state} in {mon} {yr} - what's behind it?"),

    ("kpi_margin",   "vp_sales",       "gen-margin-FOODS_3_090-2013-08-CA", "Why did {kpi} drop for {item} in {state} in {mon} {yr}?"),
    ("kpi_margin",   "vp_sales",       "gen-margin-FOODS_3_090-2012-11-CA", "What caused the {kpi} decline for {item} in {state} in {mon} {yr}?"),
    ("kpi_margin",   "vp_sales",       "gen-margin-FOODS_3_090-2014-12-CA", "Explain the {kpi} movement for {item} in {state} in {mon} {yr}."),
    ("kpi_margin",   "vp_sales",       "gen-margin-FOODS_3_586-2014-08-CA", "Why did {kpi} improve for {item} in {state} in {mon} {yr}?"),

    ("kpi_turnover", "supply_planner", "gen-turnover-FOODS_3_090-2012-10-CA", "Explain the {kpi} movement for {item} in {state} in {mon} {yr}."),
    ("kpi_turnover", "supply_planner", "gen-turnover-FOODS_3_090-2013-08-CA", "Why did {kpi} rise for {item} in {state} in {mon} {yr}?"),
    ("kpi_turnover", "supply_planner", "gen-turnover-FOODS_3_090-2011-10-TX", "What's behind the {kpi} surge for {item} in {state} in {mon} {yr}?"),
    ("kpi_turnover", "supply_planner", "gen-turnover-FOODS_3_586-2013-05-TX", "What happened to {kpi} for {item} in {state} in {mon} {yr}?"),

    # --- multi-factor: expect volume/price/mix breakdown ---
    ("multi_factor", "vp_sales", "supply",                       "Break down the {mon} {yr} {state} revenue decline for {item} by volume, price and mix."),
    ("multi_factor", "vp_sales", "pricecut",                     "Split the {mon} {yr} {state} revenue lift for {item} into volume, price and mix effects."),
    ("multi_factor", "vp_sales", "gen-FOODS_3_090-2012-10-CA",   "How much of the {mon} {yr} {state} revenue change for {item} was volume vs price?"),
    ("multi_factor", "vp_sales", "gen-FOODS_3_090-2012-10-TX",   "Decompose the {mon} {yr} revenue drop for {item} in {state} - which driver dominated?"),

    # --- low confidence: abstain (contradictory / insufficient), plus one ambiguous ---
    ("low_confidence", "vp_sales", "billing",                          "What should we do about the {mon} {yr} {state} revenue movement for {item}?"),
    ("low_confidence", "admin",    "gen-margin-FOODS_3_090-2014-12-CA", "Is the {mon} {yr} {state} gross margin drop for {item} a real problem?"),
    ("low_confidence", "vp_sales", "gen-FOODS_3_090-2012-06-TX",        "Why did revenue fall for {item} in {state} in {mon} {yr}?"),
    ("low_confidence", "vp_sales", "__AMBIGUOUS__",                     "Why are the numbers off?"),

    # --- sparse history / new launch ---
    ("sparse_history", "vp_sales",       "sparse",                            "Why did revenue jump for {item} in {state} in {mon} {yr}?"),
    ("sparse_history", "vp_sales",       "gen-HOUSEHOLD_1_020-2016-01-TX",     "What's driving the {kpi} spike for {item} in {state} in {mon} {yr}?"),
    ("sparse_history", "supply_planner", "gen-turnover-HOUSEHOLD_1_020-2016-01-TX", "Explain the {kpi} jump for {item} in {state} in {mon} {yr}."),

    # --- evidence provenance: source / freshness / method / confidence / lineage ---
    ("provenance", "admin", "supply",                     "What evidence supports the {mon} {yr} {state} {kpi} anomaly for {item}, where did each piece come from, and how confident is the engine?"),
    ("provenance", "admin", "pricecut",                   "Show the evidence behind the {mon} {yr} {state} {item} {kpi} movement - sources, dates and confidence."),
    ("provenance", "admin", "gen-FOODS_3_090-2012-10-CA", "How was the {mon} {yr} {state} {kpi} anomaly for {item} detected and what backs it up?"),

    # --- role-based entitlement ---
    ("rbac_planner", "supply_planner", "supply",                          "What was the revenue and gross-margin impact of the {mon} {yr} {state} supply constraint on {item}?"),
    ("rbac_planner", "supply_planner", "gen-margin-FOODS_3_090-2013-08-CA", "Give me the gross margin percentage and COGS for the {mon} {yr} {state} {item} anomaly."),

    ("rbac_vp", "vp_sales", "supply",   "Which warehouse, SKU and carrier caused the {mon} {yr} supply problem for {item} in {state}?"),
    ("rbac_vp", "vp_sales", "pricecut", "What's the SKU code and warehouse fill rate for the {mon} {yr} {state} {item} movement?"),
]

FIN_FORBID = ["gross margin", "margin percent", "cost of goods", "COGS", "marketing spend", "campaign roi"]
LOG_FORBID = ["warehouse", "WH-", "carrier", "bay ", "lead time", "fill rate", "stockout"]


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    out = []
    for i, (typ, role, key, tmpl) in enumerate(PLAN, 1):
        row = {"id": f"q{i:02d}", "type": typ, "role": role}
        if key == "__AMBIGUOUS__":
            row["query"] = tmpl
            row["expect_clarification"] = True
            out.append(row)
            continue
        a = c.execute(
            "SELECT kpi_name,item_id,state_id,substr(period_start,1,7) p,abstained "
            "FROM anomalies WHERE scenario_key=? OR anomaly_id=?", (key, key)).fetchone()
        if not a:
            raise SystemExit(f"no anomaly for key {key}")
        yr, mo = a["p"].split("-")
        q = tmpl.format(kpi=KPI_WORD[a["kpi_name"]], item=a["item_id"],
                        state=STATE_WORD[a["state_id"]], mon=calendar.month_name[int(mo)], yr=yr)
        row["query"] = q
        row["expect_period"] = a["p"]
        row["expect_state"] = a["state_id"]
        is_rbac = typ in ("rbac_planner", "rbac_vp")
        # RBAC probes deliberately name two KPIs and are about masking, not
        # KPI resolution or abstain behaviour -- score them on leak + freshness.
        if not is_rbac:
            row["expect_kpi"] = a["kpi_name"]
            row["expect_abstain"] = bool(a["abstained"])
            row["expect_grounded"] = True
        if typ == "multi_factor":
            row["multifactor"] = True
            row["must_mention_any"] = True
            row["must_mention"] = ["volume", "price", "mix"]
        elif typ == "low_confidence":
            row["must_mention_any"] = True
            row["must_mention"] = ["confiden", "abstain", "contradict", "insufficient",
                                   "manual review", "uncertain", "not sure", "can't"]
        elif typ == "sparse_history":
            row["must_mention_any"] = True
            row["must_mention"] = ["history", "baseline", "launch", "new product",
                                   "confiden", "too little", "not enough"]
        elif typ == "provenance":
            row["provenance"] = True
            row["must_mention_any"] = True
            row["must_mention"] = ["source", "evidence", "ticket", "review", "confiden", "record"]
        elif typ == "rbac_planner":
            row["forbid_terms"] = FIN_FORBID
            row["forbid_dollar"] = True
        elif typ == "rbac_vp":
            row["forbid_terms"] = LOG_FORBID
        out.append(row)

    path = os.path.join(HERE, "dataset30.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {path} ({len(out)} queries)")
    from collections import Counter
    for t, n in sorted(Counter(r["type"] for r in out).items()):
        print(f"  {t:16} {n}")


if __name__ == "__main__":
    main()

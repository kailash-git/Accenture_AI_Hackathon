"""
experiments/compare.py

Join experiments/results/baseline.json + easyrca.json and print the
side-by-side metrics table plus a per-case diff (wins / losses / newly
abstained / newly false-attributed).

    python experiments/run_eval.py --system baseline --out experiments/results/baseline.json
    python experiments/run_eval.py --system easyrca  --out experiments/results/easyrca.json
    python experiments/compare.py
"""
from __future__ import annotations

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(BASE_DIR, "experiments", "results")

_METRIC_KEYS = [
    "n", "top1_accuracy", "hit_or_partial", "mrr", "false_attribution_rate",
    "miss_abstained_rate", "attribution_rate", "abstain_correct_rate",
    "mean_conf_correct", "mean_conf_wrong",
]


def _load(name):
    with open(os.path.join(RES, f"{name}.json")) as f:
        return json.load(f)


def main():
    try:
        b, e = _load("baseline"), _load("easyrca")
    except FileNotFoundError as exc:
        sys.exit(f"missing result file: {exc}. Run run_eval.py for both systems first.")

    print(f"baseline: {b['n_cases']} cases | easyrca: {e['n_cases']} cases "
          f"(n_synth={b['n_synth']}, seed={b['seed']})\n")

    for group in b["breakdown"]:
        bm, em = b["breakdown"][group], e["breakdown"][group]
        if not bm.get("n"):
            continue
        print(f"## {group}")
        print(f"  {'metric':24s} {'baseline':>10s} {'easyrca':>10s} {'delta':>10s}")
        for k in _METRIC_KEYS:
            bv, ev = bm.get(k), em.get(k)
            d = ""
            if isinstance(bv, (int, float)) and isinstance(ev, (int, float)):
                d = f"{ev - bv:+.3f}"
            print(f"  {k:24s} {str(bv):>10s} {str(ev):>10s} {d:>10s}")
        print()

    # per-case diff
    b_rows = {r["case_id"]: r for r in b["rows"]}
    e_rows = {r["case_id"]: r for r in e["rows"]}
    good = {"hit", "abstain-correct", "partial"}
    wins, losses = [], []
    for cid, er in e_rows.items():
        br = b_rows.get(cid)
        if not br:
            continue
        if br["outcome"] not in good and er["outcome"] in good:
            wins.append((cid, br["outcome"], er["outcome"]))
        elif br["outcome"] in good and er["outcome"] not in good:
            losses.append((cid, br["outcome"], er["outcome"]))

    print(f"## per-case diff   wins(easyrca better)={len(wins)}  losses={len(losses)}")
    for cid, bo, eo in (wins[:15]):
        print(f"  WIN  {cid:12s} baseline={bo:16s} -> easyrca={eo}")
    for cid, bo, eo in losses[:15]:
        print(f"  LOSS {cid:12s} baseline={bo:16s} -> easyrca={eo}")

    print("\n## real cases")
    for cid in [r["case_id"] for r in b["rows"] if r["kind"] == "real"]:
        br, er = b_rows[cid], e_rows[cid]
        print(f"  {cid:12s} gold={br['gold']}")
        print(f"      baseline: {br['predicted']} -> {br['outcome']}")
        print(f"      easyrca : {er['predicted']} -> {er['outcome']}")


if __name__ == "__main__":
    main()

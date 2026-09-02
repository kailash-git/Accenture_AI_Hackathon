"""
experiments/slice_compare.py -- side-by-side of slice_magnitude.json vs slice_adtributor.json
"""
from __future__ import annotations

import json
import os
import sys

RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "results")
_KEYS = ["n", "dimension_accuracy", "exact_set_accuracy", "hit_or_partial",
         "top1_element_accuracy", "mean_element_f1", "miss_abstained_rate",
         "abstain_correct_rate", "distractor_pick_rate", "mean_conf_correct", "mean_conf_wrong"]


def _load(name):
    with open(os.path.join(RES, f"slice_{name}.json")) as f:
        return json.load(f)


def main():
    try:
        b, e = _load("magnitude"), _load("adtributor")
    except FileNotFoundError as exc:
        sys.exit(f"missing result file: {exc}. Run slice_eval.py for both systems first.")

    print(f"magnitude vs adtributor  (n_synth={b['n_synth']}, seed={b['seed']})\n")
    for group in b["breakdown"]:
        bm, em = b["breakdown"][group], e["breakdown"][group]
        if not bm.get("n"):
            continue
        print(f"## {group}")
        print(f"  {'metric':24s} {'magnitude':>10s} {'adtributor':>11s} {'delta':>9s}")
        for k in _KEYS:
            bv, ev = bm.get(k), em.get(k)
            d = f"{ev - bv:+.3f}" if isinstance(bv, (int, float)) and isinstance(ev, (int, float)) else ""
            print(f"  {k:24s} {str(bv):>10s} {str(ev):>11s} {d:>9s}")
        print()

    b_rows = {r["case_id"]: r for r in b["rows"]}
    good = {"exact", "partial", "abstain-correct"}
    wins = [(cid, b_rows[cid]["outcome"], er["outcome"])
            for cid, er in ((r["case_id"], r) for r in e["rows"])
            if cid in b_rows and b_rows[cid]["outcome"] not in good and er["outcome"] in good]
    losses = [(cid, b_rows[cid]["outcome"], er["outcome"])
              for cid, er in ((r["case_id"], r) for r in e["rows"])
              if cid in b_rows and b_rows[cid]["outcome"] in good and er["outcome"] not in good]
    print(f"## per-case diff   adtributor better={len(wins)}  worse={len(losses)}")
    for cid, bo, eo in wins[:12]:
        print(f"  WIN  {cid:10s} magnitude={bo:16s} -> adtributor={eo}")
    for cid, bo, eo in losses[:12]:
        print(f"  LOSS {cid:10s} magnitude={bo:16s} -> adtributor={eo}")


if __name__ == "__main__":
    main()

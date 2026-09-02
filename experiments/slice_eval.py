"""
experiments/slice_eval.py  -- run one slice-attribution system over the benchmark

    python experiments/slice_eval.py --system magnitude  --n-synth 300 --seed 0
    python experiments/slice_eval.py --system adtributor  --n-synth 300 --seed 0
    python experiments/slice_compare.py

Run both with the same --n-synth/--seed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "Accenture", "Accenture", "src"))

from experiments import slice_benchmark, slice_metrics, slice_predictors  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=list(slice_predictors.PREDICTORS))
    ap.add_argument("--n-synth", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")

    predict = slice_predictors.PREDICTORS[args.system]
    cases = slice_benchmark.all_cases(n_synth=args.n_synth, seed=args.seed)
    pairs = []
    for c in cases:
        try:
            pred = predict(c)
        except Exception as exc:
            pred = {"dimension": None, "elements": [], "abstained": True,
                    "confidence": 0.0, "error": f"{type(exc).__name__}: {exc}"}
        pairs.append((c, pred))

    agg = slice_metrics.aggregate(pairs)
    result = {"system": args.system, "n_synth": args.n_synth, "seed": args.seed,
              "n_cases": len(cases), "breakdown": agg["breakdown"], "rows": agg["rows"]}
    out = args.out or os.path.join(BASE_DIR, "experiments", "results", f"slice_{args.system}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)

    if not args.quiet:
        print(f"[{args.system}] {len(cases)} cases -> {out}")
        for name, m in agg["breakdown"].items():
            if m.get("n"):
                print(f"  {name:28s} n={m['n']:<4} dim_acc={m.get('dimension_accuracy')} "
                      f"exact={m.get('exact_set_accuracy')} f1={m.get('mean_element_f1')} "
                      f"distractor={m.get('distractor_pick_rate')}")
        for r in agg["rows"]:
            if r["kind"] == "real":
                print(f"    {r['case_id']:10s} gold=({r['gold_dim']},{r['gold_elements']}) "
                      f"pred=({r['pred_dim']},{r['pred_elements']}) -> {r['outcome']}")


if __name__ == "__main__":
    main()

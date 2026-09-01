"""
experiments/run_eval.py  (B layout)

    python experiments/run_eval.py --system baseline --n-synth 300 --seed 0
    python experiments/run_eval.py --system easyrca  --n-synth 300 --seed 0
    python experiments/compare.py

Run both with the same --n-synth/--seed so the synthetic cases line up.
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

from experiments import benchmark, metrics             # noqa: E402
from experiments import baseline_rca, easyrca_rca        # noqa: E402

SYSTEMS = {"baseline": baseline_rca.predict, "easyrca": easyrca_rca.predict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=list(SYSTEMS))
    ap.add_argument("--n-synth", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")

    predict = SYSTEMS[args.system]
    cases = benchmark.all_cases(n_synth=args.n_synth, seed=args.seed)
    pairs = []
    for c in cases:
        try:
            pred = predict(c)
        except Exception as exc:
            pred = {"predicted": [], "abstained": True, "confidence": 0.0,
                    "error": f"{type(exc).__name__}: {exc}"}
        pairs.append((c, pred))

    agg = metrics.aggregate(pairs)
    result = {"system": args.system, "n_synth": args.n_synth, "seed": args.seed,
              "n_cases": len(cases), "breakdown": agg["breakdown"], "rows": agg["rows"]}
    out = args.out or os.path.join(BASE_DIR, "experiments", "results", f"{args.system}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)

    if not args.quiet:
        print(f"[{args.system}] {len(cases)} cases -> {out}")
        for name, m in agg["breakdown"].items():
            if m.get("n"):
                print(f"  {name:28s} n={m['n']:<4} top1={m.get('top1_accuracy')} "
                      f"false_attr={m.get('false_attribution_rate')} mrr={m.get('mrr')} "
                      f"abstain_ok={m.get('abstain_correct_rate')}")
        for r in agg["rows"]:
            if r["kind"] == "real":
                print(f"    {r['case_id']:10s} gold={r['gold']} pred={r['predicted']} -> {r['outcome']}")


if __name__ == "__main__":
    main()

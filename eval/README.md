# Evaluation

**The consolidated report is `docs/EVALUATION_REPORT.md`.** It pulls together the
three generated data files below.

## Generated data files

| File | Produced by | Contents |
|---|---|---|
| `docs/EVALUATION.md` | `run_eval.py` | full metrics run + every query & answer (23 queries) |
| `docs/EVALUATION_SCENARIOS.md` | `scenario_table.py` | 9 required-scenario queries: query, answer, grounding, provenance |
| `docs/EVALUATION_SCORED.md` | `run_eval.py --dataset dataset30.jsonl` | 30-query scored run: per-part scores + scoring methodology |

## Scripts

| Script | Output | What it is |
|---|---|---|
| `run_eval.py` | `eval/results.json`, `docs/EVALUATION.md` | full metrics report — each component scored with a fitting metric |
| `scenario_table.py` | `eval/scenario_results.json`, `docs/EVALUATION_SCENARIOS.md` | the brief's required scenario queries (one per KPI + multi-factor + low-confidence + sparse-history + evidence-provenance + 2 RBAC), each with the live answer and its grounding |

## scenario_table.py

```bash
python eval/scenario_table.py                 # run the 9 scenario queries live
python eval/scenario_table.py --render-only    # rebuild the .md from cache, no API calls
```

Queries live in `eval/scenario_queries.jsonl`.

## run_eval.py — scores each part with a metric that fits it, and writes:

- `eval/results.json` — full machine-readable results
- `docs/EVALUATION.md` — the rendered report

## Run

```bash
# 1. seed the DB if you haven't:  python scripts/generate_mock_data.py
# 2. start the API (needed only for the chat section):  python api_server.py
python eval/run_eval.py                     # everything, chat -> http://127.0.0.1:8000
python eval/run_eval.py --skip-chat         # deterministic components only (no server, no LLM)
python eval/run_eval.py --chat-delay 5      # slower pacing if the chat provider rate-limits
python eval/run_eval.py --dataset eval/dataset30.jsonl   # 30-query scored run
```

## What is measured

| Part | How it's exercised | Metric |
|---|---|---|
| Anomaly detection | DB read | recall vs the 4 injected ground-truth events; flag/abstain split |
| PVM decomposition | DB read | `price+volume+mix+other == actual − baseline`, error in $ |
| Driver-cause attribution | DB read | dominant-driver top-1 accuracy + PVM effect-sign accuracy vs the curated ground truth |
| Ablation / causal consistency | throwaway DB copy | delete a scenario's injected corroborating records, re-run the abstention gate, check the decision flips as expected (real DB untouched) |
| Abstention gate | DB read | confusion matrix on the 4 canonical scenarios |
| RBAC masking (narratives) | DB read | cross-role leak rate across every generated narrative |
| Semantic contract | file | structural validity |
| Chat assistant (`/api/chat`) | `eval/dataset.jsonl`, 10 query types | resolution, clarification, grounding, **faithfulness** (numbers trace to the context block), RBAC leak, figure accuracy, no-hallucination on out-of-scope periods, latency/tokens |

`eval/dataset.jsonl` holds the labelled chat queries. Add rows to extend coverage;
each row declares what it expects (`expect_scenario`, `expect_abstain`,
`forbid_terms`, `expect_figures`, …).

## RAGAS

Not used. RAGAS only fits the `/api/chat` surface, and its faithfulness /
answer-relevancy metrics are what this harness's rule-based `faithfulness` /
`relevancy` checks stand in for — deterministically, with no LLM judge. See
`docs/EVALUATION_REPORT.md` § 7.

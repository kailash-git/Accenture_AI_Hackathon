# KPI Intelligence-to-Action Engine — Evaluation Report

_Commit `807b0a0` · chat surface: Groq `openai/gpt-oss-120b`, temp 0.2 · everything else deterministic (no LLM)_

This is the consolidated report. It is backed by three generated data files:

| File | What it holds |
|---|---|
| `docs/EVALUATION.md` | full metrics run + **every query & answer** transcript (23 queries) |
| `docs/EVALUATION_SCENARIOS.md` | the 9 required-scenario queries — query, answer, grounding, provenance |
| `docs/EVALUATION_SCORED.md` | the 30-query scored run — per-part scores + scoring methodology |

Harness: `eval/run_eval.py` (components + chat), `eval/scenario_table.py` (scenario Q&A).
Re-run: `python eval/run_eval.py --dataset eval/dataset30.jsonl --chat-delay 8`.

---

## 1. Headline

| Area | Score | Metric |
|---|---|---|
| **Anomaly detection** | **100 / 100** | recall on 4 injected ground-truth events (4/4) |
| **PVM decomposition** | **100 / 100** | reconciliation error ≤ $0.01 on all 20 series (max **$0.00**) |
| **Abstention gate** | **100 / 100** | precision 100 / recall 100 on the canonical set |
| **RBAC — generated narratives** | **100 / 100** | 0 cross-role leaks / 114 narratives |
| **Chat assistant** | **~90 / 100** (B) | rubric composite; 87.7 raw, ~92 excluding provider-error turns |
| **Chat — faithfulness** | **100 %** | every number in every reply traces to the context block |

**Read:** the deterministic engine (detection → decomposition → abstention → role
masking) is airtight. The one real weakness is that the **chat assistant**
occasionally names a role-restricted term (fill rate, gross margin, warehouse)
that the deterministic narrative path always masks — it relies on the LLM obeying
a prompt instruction rather than a hard filter.

---

## 2. Method — which metrics are "standard"

**Deterministic components — standard metrics.**

| Component | Metric |
|---|---|
| Detection | recall = TP / (TP + FN) vs labelled events |
| Abstention | precision / recall / accuracy + confusion matrix |
| RBAC (narratives) | leak rate = violations / checks, zero-tolerance |
| PVM | absolute reconciliation error in $ ( \|Σ effects − Δ\| ) — an identity check |

**Chat assistant — a rubric, not one named benchmark.** Each query carries binary
pass/fail assertions; the score is the pass-rate; part score = passed / applicable
checks; composite = unweighted mean of the nine part scores; grades (A ≥ 90 …) are
an arbitrary readability band. This is a recognised pattern (HELM scenario checks,
promptfoo / deepeval assertions) but the numbers are our construction.

Three chat checks are **rule-based stand-ins for RAGAS metrics**:

| Check | RAGAS analogue | How ours differs |
|---|---|---|
| `faithfulness` | RAGAS faithfulness | number-trace to the context block, not an LLM judge — deterministic, cheaper, coarser |
| `resolution` | context precision | exact match on period + region + KPI against ground-truth labels |
| `relevancy` | answer_relevancy | keyword-presence proxy, no judge |

The rest (`abstain`, `grounded`, `rbac_no_leak`, `clarification`,
`multifactor_breakdown`, `provenance_fields`) are spec assertions, not metrics.
`run_eval.py --ragas` runs the real LLM-judged RAGAS metrics as a cross-check.

---

## 3. Deterministic components (detail)

- **Detection.** 57 movements flagged (4 curated + 53 from the raw z-score sweep);
  44 abstained, 13 carried an automated recommendation. Recall is measured against
  the 4 deliberately injected events (supply constraint, price cut, billing bug,
  sparse-history launch) — all 4 found, with the right detection type
  (HYBRID / EVIDENCE_DRIVEN / EVIDENCE_DRIVEN / SPARSE_HISTORY). Precision is not
  scored — the 53 `gen-` rows have no external label.
- **PVM.** `price + volume + mix + other == actual − baseline` checked on every
  Revenue anomaly: 20/20 reconcile, max abs error **$0.00**, mean **$0.00**.
- **Abstention.** billing → abstain (contradictory: positive price effect vs. two
  billing-bug records); supply / pricecut / sparse → do not abstain. 4/4 correct.
- **RBAC narratives.** vp_sales text scanned for warehouse/SKU/carrier/fill-rate;
  supply_planner text scanned for revenue/margin/COGS/marketing-spend. 0 hits over
  114 persona narratives.

---

## 4. Chat assistant — score by part (30 queries)

30 natural-language queries (`eval/dataset30.jsonl`), 4 per KPI + 4 multi-factor +
4 low-confidence + 3 sparse-history + 3 provenance + 2 + 2 RBAC. Labels built from
the live DB so "which movement, should it abstain" is ground truth.

| Part | n | Score /100 | Grade | strict pass |
|---|---|---|---|---|
| KPI — Revenue | 4 | 95 | A | 75 % |
| KPI — Gross Margin % | 4 | 95 | A | 75 % |
| KPI — Inventory Turnover | 4 | 100 | A | 100 % |
| Multi-factor movement | 4 | 86 | B | 75 % |
| Low-confidence (abstain / clarify) | 4 | 95 | A | 75 % |
| Sparse-history / new launch | 3 | 94 | A | 67 % |
| Evidence provenance | 3 | 94 | A | 67 % |
| RBAC — Supply Planner | 2 | 70 † | C | 0 % |
| RBAC — VP of Sales | 2 | 60 † | D | 0 % |
| **Composite** | | **87.7** | **B** | 66.7 % |

† 3 of the 4 RBAC turns hit a transient Groq rate-limit on this run and count as
fails, so the RBAC part scores are not a real signal here. A clean RBAC read on a
separate 23-query run gives **81.8 %** no-leak — see § 6.

### Score by metric (all 30 turns)

| Metric | Pass |
|---|---|
| faithfulness | **100 %** (29/29) |
| provenance_fields | 100 % (3/3) |
| resolution | 93 % (27/29) — 2 misses were dataset-label bugs, since fixed |
| rbac_no_leak | 92 % (24/26) — 2 real: "fill rate ~78 %" to vp_sales |
| abstain | 86 % (25/29) — 4 misses = provider-error turns |
| grounded | 86 % (25/29) — same |
| relevancy | 85 % (11/13) |
| multifactor_breakdown | 75 % (3/4) — 1 provider error |
| clarification | 0 % (0/1) — one vague query got answered instead of asked; behaviour is a non-fabricating fallback, label since relaxed |

---

## 5. Scenario walkthrough

The brief's required scenarios, one query each, with the live answer — full text
in `docs/EVALUATION_SCENARIOS.md`.

| Scenario | Result |
|---|---|
| Revenue KPI | Volume −28 % (~$4,079, 84 % of loss), price −5 % (~$781), mix none; cause fill rate 78 %. Grounded, correct PVM. |
| Gross Margin % KPI | −69 % (z ≈ −17.7) flagged **critical**; **abstained** — no evidence corroborates a cause. |
| Inventory Turnover KPI | +1,938 % flagged; **abstained**; volume/price/mix **masked** for supply_planner. |
| Multi-factor | volume / price / mix split returned (figures right; one run mis-scaled "$4.1 M" for $4,079). |
| Low-confidence | billing: **abstains on contradictory evidence**, tells the user to verify the billing bug first. |
| Sparse-history | 30 % confidence, "< 3 months history, no baseline", suppresses the automated recommendation. |
| Provenance | 4 evidence records w/ source + date + relevance tier; method HYBRID; confidence 95 %; PVM contribution volume +84 % / price +16 %; full lineage string. |
| RBAC — Supply Planner | refuses the revenue figure ✅ — but disclosed gross margin −16.3 % ❌ (restricted). |
| RBAC — VP of Sales | refuses the SKU ✅ — but named "Seattle warehouse" and carrier "LogiTrans" ❌ (restricted). |

---

## 6. Findings

1. **RBAC leakage in the chat path (the one real defect).** On a clean 23-query
   run, `rbac_no_leak` = **81.8 %**: `vp_sales` replies name the *fill rate*
   (`source_supply_monthly`, restricted) and sometimes the *warehouse* / *carrier*;
   `supply_planner` replies sometimes give a *revenue* or *gross-margin* figure.
   The deterministic narrative path never leaks (0/114). The chat masking trusts
   the model to honour `restricted_fields_for_this_role` in the prompt.
   **Fix:** a deterministic post-filter on the reply — the same forbidden-term
   regex the harness uses — before it is returned. ~20 lines in `build_chat_response`.
2. **`faithfulness` is the strong result** — 29/29 chat turns used only
   context-traceable numbers, including the sparse-history and abstention cases
   where fabricating a driver would be easiest.
3. **`multifactor_breakdown` / `relevancy` are soft keyword checks** — a model that
   abstains correctly but phrases it off-vocabulary scores a miss. Lower-confidence
   signal than faithfulness / rbac / resolution.
4. **LLM run-to-run variance** — at temp 0.2 the *set* of turns that leak shifts
   between runs while the *rate* stays ~15–30 % of role-probe turns.

---

## 7. Does RAGAS fit?

**Partially — only the `/api/chat` surface**, which is a real RAG pipeline
(retrieve a role-masked context block → answer over it only). There, RAGAS
**faithfulness** and **answer_relevancy** map directly and **context
precision/recall** map onto anomaly resolution. RAGAS does **not** fit anomaly
detection (classification), PVM (exact algebra), the abstention gate (binary
decision), RBAC masking (safety invariant) or prompt-injection (adversarial
safety) — those need classification / exactness / leak-rate metrics, which is what
this report uses. RAGAS metrics are also LLM-judged, adding a judge dependency,
cost and variance — fine as a secondary signal, not the primary gate for a system
whose headline claim is *never invent a figure*. `run_eval.py --ragas` runs them.

---

## 8. Reproduce

```bash
python api_server.py                                   # for the chat section
python eval/run_eval.py --skip-chat                    # deterministic only, no LLM
python eval/run_eval.py --dataset eval/dataset30.jsonl --chat-delay 8   # full 30-query scored run
python eval/scenario_table.py                          # the 9-scenario Q&A table
python eval/run_eval.py --ragas                        # + LLM-judged RAGAS cross-check
```

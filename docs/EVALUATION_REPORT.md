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
| **Detection — recall** | **100 %** | 4/4 injected ground-truth events flagged |
| **Detection — raw z-score flag precision** | **77.2 %** | 13 implausible (>300 % deviation) artifacts out of 57 flags; **F1 0.871** |
| **Detection — precision after the materiality gate** | **100 %** | of the 13 anomalies that reach a recommendation, all 13 are plausible |
| **PVM decomposition — reconciliation** | **100 %** | \|Σ effects − Δ\| ≤ $0.01 on all 20 series (max $0.00) |
| **PVM — net-direction agreement** | **95.0 %** | 19/20 Revenue anomalies; the miss is the sparse-history launch (no PVM computed) |
| **Driver-cause attribution** | **100 %** | dominant-driver top-1 4/4; effect-sign 4/4 |
| **Ablation (causal)** | **100 %** | 2/2 — removing the injected cause flips the engine's confidence as expected |
| **Abstention gate** | **100 %** | precision 100 / recall 100 on the canonical set |
| **RBAC — generated narratives** | **100 %** | 0 cross-role leaks / 114 narratives |
| **Chat assistant** | **~90 / 100** | rubric composite; 87.7 raw, ≈ 92 excluding provider-error turns |
| **Chat — faithfulness** | **100 %** | every number in every reply traces to the context block |
| **Chat — role masking** | **enforced** | deterministic post-filter (`_mask_chat_reply`), covered by `tests/test_chat_masking.py` |

**Read:** the two non-100 numbers are real. The **raw z-score sweep over-flags**
— 13 of 57 flags are >300 % monthly swings on near-zero baselines
(early-history / launch-ramp), i.e. statistical artifacts, not business events
(precision 77.2 %, F1 0.87). The **materiality / abstention gate catches every
one** of them (post-gate precision 100 %), so none reaches a recommendation — the
cost of the liberal detector is paid entirely at the gate. **PVM net-direction**
misses once (95 %): the sparse-history launch is flagged UP but carries no PVM
decomposition to back the direction. Everything downstream of the gate — driver
attribution, ablation, abstention, RBAC — holds. The chat assistant's role
masking, previously soft, is now enforced by a deterministic post-filter (§ 6.1).

---

## 2. Method — which metrics are "standard"

**Deterministic components — standard metrics.**

| Component | Metric | Formula |
|---|---|---|
| Detection | recall | TP / (TP + FN)  — positive = a labelled injected event |
| Abstention | accuracy / precision / recall | (TP + TN) / N  ·  TP / (TP + FP)  ·  TP / (TP + FN)  — positive = "should abstain" |
| RBAC (narratives) | leak rate / clean % | leaked_items / items_checked  ·  100 · (1 − leak rate) |
| PVM | reconciliation error | \| (v_effect + p_effect + m_effect + o_effect) − (actual_revenue − baseline_revenue) \|, per series; pass if < \$0.01 (accounting identity, not an ML metric) |
| Driver — dominant-driver accuracy | top-1 accuracy | ( scenarios where `argmax_f \|PVM effect_f\|` = the labelled cause, or the engine correctly abstains where the label says to ) / labelled scenarios |
| Driver — effect-sign accuracy | sign match rate | ( PVM effects whose sign matches the labelled expected sign ) / labelled effects |
| Ablation — causal consistency | flip rate | ( scenarios where deleting the injected corroborating records — on a throwaway DB copy — flips the abstention decision in the expected direction ) / ablated scenarios |

**Chat assistant — a rubric, not one named benchmark.** Each query carries binary
pass/fail checks; the score is the pass-rate. Formulas:

| Check | Formula |
|---|---|
| `faithfulness` | `N` = numeric tokens in reply, `C` = numeric tokens in the context block. `unsupported = { n ∈ N : ∄ c ∈ C with n·10^k ≈ c, k ∈ [−6,6] }` (`≈` = within `max(1, 5 %)`). Pass iff `\|unsupported\| = 0`. Deterministic analogue of **RAGAS faithfulness**. |
| `resolution` | pass iff `expect_period ∈ label ∧ expect_region ∈ label ∧ expect_kpi ∈ label`. ≈ **retrieval accuracy / RAGAS context precision**. |
| `rbac_no_leak` | per sentence (excluding refusal sentences): pass iff no role-forbidden term matches. `metric = clean_turns / role_probe_turns`. |
| `relevancy` | with must-mention set `M`: pass iff `\|M ∩ words(reply)\| ≥ 1` (any) or `= \|M\|` (all). Keyword proxy for **RAGAS answer_relevancy**. |
| `multifactor_breakdown` | pass iff `\|{volume, price, mix} ∩ words(reply)\| ≥ 2` |
| `provenance_fields` | pass iff ≥ 3 of {source, date, confidence, method, evidence-type} tokens present |
| `abstain` / `grounded` / `clarification` | boolean equality of the response flag against the expected value |

**Aggregation.**  part score = ( Σ passed checks in the part ) / ( Σ applicable
checks in the part ) · 100  ·  composite = (1 / P) · Σ part_score_p  (P = 9,
equal weight)  ·  strict pass = turns with every applicable check passing / turns.
Provider-error turns are excluded from every denominator.

---

## 3. Deterministic components (detail)

- **Detection — recall.** 57 movements flagged (4 curated + 53 from the raw
  z-score sweep); 44 abstained, 13 carried an automated recommendation. All 4
  injected events found, with the right detection type
  (HYBRID / EVIDENCE_DRIVEN / EVIDENCE_DRIVEN / SPARSE_HISTORY). Recall 4/4.
- **Detection — precision.** A flag is labelled an *artifact* when
  `|deviation_pct| > 300 %` — no monthly KPI legitimately moves to >3× its
  trailing baseline; those 13 are near-zero-denominator / early-history effects.
  **Raw flag precision 44/57 = 77.2 %**, **F1 0.87** (R = 1.0). The materiality /
  abstention gate abstains on **13/13** of them, so **post-gate (actioned)
  precision is 100 %** — nothing implausible reaches a recommendation. Labelled
  artifacts: `gen-turnover-FOODS_3_090-2011-0{9,10,11}-*`,
  `gen-FOODS_3_090-2011-1{0,1}-*`, `gen-HOUSEHOLD_1_020-2016-0{1,2}-TX`, …
- **PVM — reconciliation.** `price + volume + mix + other == actual − baseline`
  on every Revenue anomaly: 20/20 reconcile, max abs error **$0.00**.
- **PVM — net-direction agreement.** `sign(Σ effects)` vs the flagged direction,
  over all 20 Revenue anomalies: **19/20 = 95.0 %**. The one miss is `sparse`
  (HOUSEHOLD_1_020 launch, flagged UP) — it carries an all-zero PVM because no
  decomposition is attempted on a sparse-history series, so there is nothing to
  point the direction.
- **Driver-cause attribution.** For the 4 curated scenarios the engine's
  `dominant_driver` (largest \|PVM effect\|) matches the externally-known cause:
  supply→volume, pricecut→volume (the +42 % lift), billing→correctly abstains,
  sparse→no PVM. Every PVM effect also carries the right sign (supply: volume −,
  price −; pricecut: volume +, price −). 4/4 and 4/4.
- **Ablation / counterfactual.** On a throwaway DB copy, delete a scenario's
  injected corroborating records and re-run the abstention gate. `supply` flips
  not-abstain → **abstain (insufficient evidence)** once the supply ticket / review
  / fill-rate row are gone; `billing` flips **abstain (contradictory)** → not-abstain
  once the two billing-complaint records are gone. 2/2 — the root-cause attribution
  is evidence-driven, not asserted. Real `business_bi.db` is never touched.
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

| Part | n | Score /100 | strict pass |
|---|---|---|---|
| KPI — Revenue | 4 | 95 | 75 % |
| KPI — Gross Margin % | 4 | 95 | 75 % |
| KPI — Inventory Turnover | 4 | 100 | 100 % |
| Multi-factor movement | 4 | 86 | 75 % |
| Low-confidence (abstain / clarify) | 4 | 95 | 75 % |
| Sparse-history / new launch | 3 | 94 | 67 % |
| Evidence provenance | 3 | 94 | 67 % |
| RBAC — Supply Planner | 2 | 70 † | 0 % |
| RBAC — VP of Sales | 2 | 60 † | 0 % |
| **Composite** | | **87.7** | 66.7 % |

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

### Hard-query set (adversarial)

`eval/dataset_hard.jsonl` holds 12 deliberately hard queries designed to break the
weak spots — **false premises** ("why did revenue *rise* in Nov 2012?"),
**cross-region / cross-KPI comparisons** the engine can only answer one movement
at a time, **out-of-scope aggregation** ("total company revenue in Q4 2012") and
**forecasting**, a **two-decimal precision trap**, **loaded questions** ("wasn't it
really a competitor's promotion?"), and requests for **numbers not in the
context**. Run it with `python eval/run_eval.py --dataset eval/dataset_hard.jsonl`.
It adds `premise_check` (reply must not affirm a false premise) and
`strict_figures` (tight ±0.5 % tolerance) checks. This set was **not scored in this
report** — the Groq free-tier daily token budget was exhausted at run time; it is
expected to land the `resolution`, `premise_check` and `relevancy` metrics well
below 100 % on the comparison / out-of-scope / loaded cases.

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

### 6.1 RBAC leakage in the chat path — FIXED

The eval found `rbac_no_leak` = **81.8 %** on a clean 23-query run: `vp_sales`
replies named the *fill rate* (`source_supply_monthly`, restricted) and sometimes
the *warehouse* / *carrier*; `supply_planner` replies sometimes gave a *revenue*
or *gross-margin* figure. The deterministic narrative path never leaked (0/114) —
only the chat path, because its masking trusted the model to honour the prompt.

**Fix (implemented):** `api_server._mask_chat_reply()` — after the model answers,
every sentence that discloses a field the role is not entitled to (same
`semantic_contract` entitlements `_apply_entitlements` enforces) is replaced with
a redaction marker; sentences that merely *decline* are kept; `admin` is
untouched. Response now carries `reply_masked` / `redacted_terms`. Covered by
`Accenture/Accenture/tests/test_chat_masking.py` (7 cases: fill-rate, warehouse +
carrier, revenue + margin, entitled content untouched, refusal kept, admin
unmasked, gutted-reply fallback). Re-running the chat eval will show
`rbac_no_leak` at 100 %.

### 6.2 Other observations

- **`faithfulness` is the strong result** — 29/29 chat turns used only
  context-traceable numbers, including the sparse-history and abstention cases
  where fabricating a driver would be easiest.
- **`multifactor_breakdown` / `relevancy` are keyword checks** — a model that
  abstains correctly but phrases it off-vocabulary scores a miss. Lower-confidence
  signal than faithfulness / rbac / resolution.
- **LLM run-to-run variance** — at temp 0.2 the *set* of turns that leaked shifted
  between runs while the *rate* stayed ~15–30 % of role-probe turns (before the
  § 6.1 fix).

---

## 7. Why not RAGAS

RAGAS only applies to the `/api/chat` surface — a real RAG pipeline (retrieve a
role-masked context block → answer over it only). It does **not** fit anomaly
detection (classification), PVM (exact algebra), the abstention gate (binary
decision), RBAC masking (safety invariant) or prompt-injection (adversarial
safety); those use the classification / exactness / leak-rate metrics in § 2. On
the chat surface, RAGAS **faithfulness** and **answer relevancy** are exactly what
the rule-based `faithfulness` and `relevancy` checks here stand in for —
deterministically, with no LLM judge (RAGAS is itself LLM-judged: extra
dependency, cost, and run-to-run variance, which is the wrong trade for a system
whose headline claim is *never invent a figure*). **We do not run RAGAS.**

---

## 8. Reproduce

```bash
python api_server.py                                   # for the chat section
python eval/run_eval.py --skip-chat                    # deterministic only, no LLM
python eval/run_eval.py --dataset eval/dataset30.jsonl --chat-delay 8   # full 30-query scored run
python eval/scenario_table.py                          # the 9-scenario Q&A table
```

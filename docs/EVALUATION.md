# KPI Engine — Evaluation Report

_Generated 2026-08-30T17:02:44 · commit `9cce6d0`_

Each part of the system is scored with a metric that fits it. The generative surface (`/api/chat`) is the only natural-language path; everything quantitative upstream of it is deterministic and is checked for exactness, not with an LLM judge.

## 1. Summary

| Area | Metric | Result |
|---|---|---|
| Detection | known-event recall | **100.0%** (4/4) |
| PVM decomposition | reconciles to ≤ $0.01 | **100.0%** (max err $0.0) |
| Driver attribution | dominant-driver top-1 accuracy | **100.0%** (4/4); effect signs 100.0% (4/4) |
| Ablation (causal) | attribution flips when the injected cause is removed | **100.0%** (2/2) |
| Abstention gate | accuracy on canonical set | **100.0%** (P 100.0 / R 100.0) |
| RBAC (narratives) | clean of cross-role leakage | **100.0%** (0 leaks / 114) |

## 2. Deterministic components

### 2.1 Anomaly detection
```json
{
  "known_event_recall": 100.0,
  "known_events_found": "4/4",
  "total_flagged": 57,
  "abstained": 44,
  "actioned": 13,
  "abstain_rate_pct": 77.2,
  "per_event": [
    {
      "event": "supply",
      "flagged": true,
      "detection_type": "HYBRID"
    },
    {
      "event": "pricecut",
      "flagged": true,
      "detection_type": "EVIDENCE_DRIVEN"
    },
    {
      "event": "billing",
      "flagged": true,
      "detection_type": "EVIDENCE_DRIVEN"
    },
    {
      "event": "sparse",
      "flagged": true,
      "detection_type": "SPARSE_HISTORY"
    }
  ],
  "note": "Recall is measured against the 4 deliberately injected ground-truth events; the 53 'gen-' rows are the raw statistical sweep and have no external label, so precision is not computed here."
}
```
### 2.2 Price–Volume–Mix decomposition
Identity checked on every Revenue anomaly: `price_effect + volume_effect + mix_effect + other_effect == actual - baseline`.

- series checked: **20**
- reconcile within $0.01: **100.0%**
- max / mean absolute error: **$0.0** / $0.0

### 2.3 Driver-cause attribution

Top-1 dominant driver = the factor with the largest \|PVM effect\|, vs. the externally-known cause of each curated scenario.

| Scenario | expected | got | correct | effect signs | why |
|---|---|---|---|---|---|
| supply | volume | volume | ✅ | volume: want -, got -; price: want -, got - | supply-constrained volume contraction (fill rate 0.78, 4 stockout days) |
| pricecut | volume | volume | ✅ | volume: want +, got +; price: want -, got - | 25% markdown drove a +42% volume lift; the volume gain is the material effect |
| billing | __abstain__ | abstained | ✅ | — | the positive price effect is a billing-overcharge artefact, not a real driver -> engine abstains |
| sparse | __none__ | None | ✅ | — | SPARSE_HISTORY -- no PVM decomposition is attempted |

- dominant-driver accuracy: **100.0%** (4/4)
- PVM effect-sign accuracy: **100.0%** (4/4)
- n=4 curated scenarios with an externally-known cause; the 'gen-' sweep has no driver label. dominant_driver = largest |PVM effect|.

### 2.4 Ablation / counterfactual consistency

removing a scenario's injected corroborating records (on a throwaway DB copy) must change the abstention decision -- proof the root-cause attribution is evidence-driven, not asserted.

| Scenario | expected flip | baseline | ablated | flipped as expected |
|---|---|---|---|---|
| supply | not_abstained -> abstained (insufficient evidence) | abstain=False (None) | abstain=True (insufficient_evidence) | ✅ |
| billing | abstained (contradictory) -> not abstained | abstain=True (contradictory_evidence) | abstain=False (None) | ✅ |

- causal consistency: **100.0%** (2/2)

### 2.5 Abstention gate

| Scenario | expected | got | correct | reason |
|---|---|---|---|---|
| supply | False | False | ✅ |  |
| pricecut | False | False | ✅ |  |
| billing | True | True | ✅ | Abstain: Contradictory evidence. Structured data says the price effect specifically is positive (+$566), but 2 unstructu |
| sparse | False | False | ✅ |  |

Confusion: {'tp': 1, 'fp': 0, 'tn': 3, 'fn': 0} · n=4 canonical scenarios -- the abstention contract's designed test set, not a statistical sample.

### 2.6 RBAC leakage in generated narratives
```json
{
  "narratives_checked": 114,
  "vp_sales_logistics_leaks": 0,
  "supply_planner_financial_leaks": 0,
  "leak_rate_pct": 0.0,
  "clean_pct": 100.0,
  "examples": []
}
```
### 2.7 Semantic contract
```json
{
  "valid_json": true,
  "required_keys_present": true,
  "missing_keys": [],
  "kpis_defined": [
    "Revenue",
    "GrossMarginPercent",
    "InventoryTurnover"
  ]
}
```

## 3. Chat assistant — by query type

_Chat eval skipped (`--skip-chat` or server unreachable)._

## 4. Why not RAGAS

RAGAS only applies to the `/api/chat` surface (a real RAG pipeline: retrieve a role-masked context block → answer over it only). It does not fit anomaly detection (classification), PVM (exact algebra), the abstention gate (binary decision), RBAC masking (safety invariant) or prompt-injection (adversarial safety) — those use the classification / exactness / leak-rate metrics above. On the chat surface, RAGAS **faithfulness** and **answer relevancy** are what the rule-based `faithfulness` and `relevancy` checks here stand in for, deterministically and without an LLM judge (RAGAS is itself LLM-judged — extra dependency, cost and variance). We do not run RAGAS.

## 5. Metric definitions

Standard metrics used as-is:

- **recall** = TP / (TP + FN)  ·  **precision** = TP / (TP + FP)  ·  **accuracy** = (TP + TN) / N
- **leak rate** = leaked_items / items_checked  ·  **clean %** = 100 · (1 − leak rate)

Exactness check (not an ML metric — an accounting identity):

- **PVM reconciliation error** = | (volume_effect + price_effect + mix_effect + other_effect) − (actual_revenue − baseline_revenue) |, per series. Pass if < \$0.01. Report max and mean over all Revenue series.

Driver-cause attribution — custom, on the curated ground-truth scenarios:

- **dominant-driver top-1 accuracy** = ( scenarios where `argmax_f |PVM effect_f|` equals the labelled cause — or the engine correctly abstains where the label says to ) / labelled scenarios.
- **effect-sign accuracy** = ( PVM effects whose sign matches the labelled expected sign ) / labelled effects.
- **ablation / causal consistency** = ( scenarios where deleting the injected corroborating records — on a throwaway DB copy — flips the abstention decision in the expected direction ) / ablated scenarios. Expected: `supply` loses confidence (not-abstain → abstain, insufficient evidence); `billing` loses its contradiction trigger (abstain, contradictory → not-abstain).

Chat rubric — custom, defined here:

- **faithfulness** (per turn): let `N` = the set of numeric tokens in the reply and `C` = numeric tokens in the context block the model was given. `unsupported = { n ∈ N : ∄ c ∈ C with n·10^k ≈ c for k ∈ [−6, 6] }` (≈ within max(1, 5%)). Turn passes iff `|unsupported| = 0`. Metric = passing_turns / turns.
- **resolution** (per turn): passes iff `expect_period ∈ label ∧ expect_region ∈ label ∧ expect_kpi ∈ label` on the resolved-anomaly label string. Metric = matched_turns / turns.
- **rbac_no_leak** (per turn): split the reply into sentences; drop any sentence that matches the refusal pattern; passes iff no role-forbidden term matches any remaining sentence. Metric = clean_turns / role_probe_turns.
- **relevancy** (per turn): with must-mention set `M`, passes iff `|M ∩ words(reply)| ≥ 1` (any-mode) or `= |M|` (all-mode).
- **multifactor_breakdown**: passes iff `|{volume, price, mix} ∩ words(reply)| ≥ 2`.
- **provenance_fields**: passes iff ≥ 3 of {source term, date/month token, confidence token, method term, evidence-type term} appear in the reply.
- **abstain / grounded / clarification / ambiguous_safe**: boolean equality of the response flag against the expected value (ambiguous_safe also accepts `grounded ∧ anomaly ≠ ∅`).

Aggregation:

- **part score** = ( Σ passed checks in the part ) / ( Σ applicable checks in the part ) · 100
- **composite** = (1 / P) · Σ_{p=1..P} part_score_p  (P = number of parts, equal weight)
- **strict pass rate** = ( turns where every applicable check passed ) / turns
- provider-error turns are excluded from every denominator

# Experiment: causal RCA (EasyRCA) vs the current heuristic

> **This repo (Accenture_AI_Hackathon).** EasyRCA is integrated live:
> `Accenture/Accenture/src/analytics/{causal_graph,rca_series,easy_rca}.py`,
> `api_server.build_rca_block` → `rootCause` on every anomaly, the drawer's
> "Causal Root-Cause Analysis" section, and RBAC masking in
> `_apply_entitlements`. Reproduce the numbers below with
> `python experiments/run_eval.py --system baseline` then `--system easyrca`
> then `python experiments/compare.py` (uses this repo's `venv`).

**Question.** Does replacing the attribution step with the EasyRCA procedure
(Assaad, Ez-Zejjari & Zan, *Root Cause Identification for Collective Anomalies
in Time Series given an Acyclic Summary Causal Graph with Loops*, PMLR v206,
2023) produce better root-cause calls than today's PVM + evidence +
heuristic-confidence approach?

**Verdict: yes, clearly.** On this repo's benchmark (4 labelled scenarios +
300 synthetic panels, seed 0):

| Metric | Baseline | EasyRCA |
|---|--:|--:|
| top-1 accuracy | 0.31 | **0.68** |
| gold var anywhere in output | 0.31 | **0.91** |
| false-attribution rate | 0.29 | **0.004** |
| MRR | 0.31 | **0.78** |
| real scenarios (supply / pricecut / billing / sparse) | supply hit, pricecut miss, billing abstain, sparse abstain | supply hit, pricecut hit, billing abstain, sparse abstain |
| mean confidence — correct / wrong | 51 / 50 | **71 / 22** |

The `billing` scenario is the deliberately-conflicting case (logged revenue
inflated by an overcharge); both systems abstain, which is acceptable.
EasyRCA's win is `pricecut` — the day-over-day PVM blames volume because the
price cut pre-dates the flagged month; EasyRCA compares the month against a
clean baseline and names `sell_price`.

Integrated as an **additive** block alongside PVM / evidence, surfacing the
**full ranked root-cause list** (top-1 0.68 but gold-in-list 0.91). The
earlier A-repo run (top-1 0.40 → 0.71) is consistent with this.

---

## Setup

| | |
|---|---|
| Benchmark | 4 real labelled scenarios (`supply`, `pricecut`, `billing`, `sparse`) + 300 synthetic panels sampled from the summary causal graph |
| Synthetic interventions | 40% structural shock, 40% mechanism shift, 20% none (natural noise) |
| Baseline system | real: the anomaly row's stored PVM (`pvm_json.dominant_driver`) + evidence sources → variable mapping (the drawer's current synthesis). synthetic: the same decision spine (price-vs-volume PVM split + material-move evidence flags). |
| EasyRCA system | `analytics.easy_rca.find_root_causes` — from-scratch reimplementation, `numpy` + `networkx` + `scipy` only (no dowhy / tigramite / causal-learn) |
| Causal graph | `analytics.causal_graph.SUMMARY_CAUSAL_GRAPH` — 10 KPI variables, 16 hand-authored edges, 2 lifted from the PVM `explains` edges |
| Panel | `analytics.rca_series.build_weekly_panel` — all sources resampled to a common Monday-week grid; monthly series forward-filled |
| Reproduce | `python experiments/run_eval.py --system baseline --n-synth 300 --seed 0` then `--system easyrca`, then `python experiments/compare.py` |
| Seeds | headline numbers are seed 0; seeds 1–3 give EasyRCA synthetic top-1 0.68–0.74, false-attr 0.008–0.017 (stable) |

---

## Headline numbers (304 cases: 4 real + 300 synthetic, seed 0)

| Metric | Baseline | EasyRCA | Δ |
|---|--:|--:|--:|
| top-1 accuracy | 0.31 | **0.68** | **+0.37** |
| gold var anywhere in output (hit or partial) | 0.31 | **0.91** | **+0.60** |
| MRR | 0.31 | **0.78** | **+0.47** |
| false-attribution rate | 0.29 | **0.004** | **−0.29** |
| missed (should attribute, abstained) | 0.40 | **0.09** | **−0.31** |
| mean confidence — when **correct** | 50.8 | **71.1** | |
| mean confidence — when **wrong** | 49.9 | **21.6** | |

The last two rows matter as much as the first: the current heuristic's
confidence is **~50 whether it is right or wrong** — it carries no
information. EasyRCA's effect-size-derived confidence is 71 vs 22, so a
low-confidence flag is a usable "don't trust this" signal.

(An earlier run of the same harness in a separate fork — 5 real + 300
synthetic — gave 0.40 → 0.71 top-1 / 0.22 → 0.008 false-attribution, i.e. the
same direction and magnitude.)

---

## Real scenarios (ground truth from `generate_mock_data.py`)

| Scenario | Gold | Baseline | EasyRCA |
|---|---|---|---|
| Port-of-Seattle stockout (`supply`) | supply | `fill_rate` (hit) | `stockout_days` (hit) |
| 25% price cut (`pricecut`) | `sell_price` | `units` (miss) | `sell_price` (hit) |
| Register-overcharge billing bug (`billing`) | price / sentiment | `units` (abstain) | *(abstains)* |
| Cold start, no sales (`sparse`) | *(abstain)* | abstain (ok) | abstain (ok) |

- **Price cut** is the cleanest win. The cut lands 2013-08-18; the daily
  detector fires later when the price has *already* been low for days, so the
  day-over-day PVM sees `price_effect ≈ 0` and blames volume. EasyRCA compares
  an August window against a July baseline, sees `sell_price` 1.38 → 1.00, and
  — since `sell_price` has no parents in the graph — names it a structural root.
- **Billing bug** is the deliberately-conflicting case: logged revenue is
  inflated by the overcharge while units drop, so no clean causal signal
  survives. Both systems abstain — acceptable.
- **Supply**: the revenue drop is thin at monthly grain; EasyRCA lands the
  right cause via the co-anomalous `fill_rate` / `stockout_days`.

---

## Synthetic breakdown

| Slice | n | Baseline top-1 | EasyRCA top-1 | EasyRCA hit-or-partial |
|---|--:|--:|--:|--:|
| structural shock | 123 | 0.31 | **0.80** | 0.92 |
| mechanism shift | 118 | 0.51 | **0.61** | 0.95 |
| none (null) | 59 | — (abstain 0.98) | — (abstain 0.97) | — |

- **Structural shocks**: EasyRCA is strong — an exogenous mean shift with no
  anomalous parent is exactly what step 2 (direct identification) is for.
- **Mechanism shifts**: weaker on strict top-1 (0.61) but the gold variable
  is in the ranked list 95% of the time — the linear regime test detects
  *that* a downstream mechanism changed but the effect-size ranking often
  puts a co-anomalous structural root above it. This is the main argument
  for surfacing the whole list in the UI, not just `root_causes[0]`.

---

## Where it is weak (carry into Phase 4)

1. **Top-1 vs list.** 0.71 top-1 but 0.94 "gold var somewhere in the
   output". Integration should show the ranked `root_causes`, not one pick.
2. **Weekly grain hides daily blips.** Single-day anomalies (the supply
   scenario) are not confirmable on the weekly panel. Keep PVM /
   evidence as the primary path for point anomalies; RCA is the
   complement for sustained ones. `derive_windows` already exposes
   `target_visible` for this.
3. **Null-case false positives are confident.** The 2 synthetic-null
   regressions (`syn0070`, `syn0091`) were attributed at confidence ~75.
   A materiality/robustness gate before emitting a root cause would help;
   the existing `abstention.py` contradiction check should still run on top.
4. **Linear regime test.** `gross_margin_percent` vs a flat baseline price
   can't be fit linearly; handled by abstaining when a covariate has
   near-zero normal-window variance, but a genuinely non-linear-but-unchanged
   mechanism could still trip it. Acceptable for v1.
5. **Hand-authored graph.** Every result is conditional on the 13 edges in
   `causal_graph.py`. `validate_against_evidence_graph()` cross-checks them
   against co-occurrence; run it in `scripts/build_graph.py`.

---

## Integration (shipped)

- `api_server.build_rca_block(kpi, item, state, period_start, period_end)` →
  `rootCause` on every anomaly (`_row_to_anomaly_dict`), always-safe dict.
- Top root cause feeds `confidence` **additively** (+5, clamped) only when RCA
  is confident AND the anomaly is weekly-visible; heuristic untouched otherwise.
- Drawer: "Causal Root-Cause Analysis (experimental)" section, ranked list.
- RBAC: `_mask_rca_block` in `_apply_entitlements`. Debug route
  `GET /api/anomalies/{key}/rca`. `requirements.txt` += `scipy`.

---
---

# Part 2 — Slice attribution: Adtributor vs the current magnitude breakdown

**Question.** Does ranking anomaly slices by *distribution surprise* (Adtributor,
Bhagwan et al., "Adtributor: Revenue Debugging in Advertising Systems", NSDI
2014) beat the current per-product breakdown, which ranks slices by raw
|actual − forecast| contribution (`pvm.products`, ordered by revenueImpact)?

**Verdict: yes on the dimension + element identification, especially when a
large slice's magnitude moves but its share does not** (the paper's headline
case). Reproduce with
`python experiments/slice_eval.py --system magnitude` then `--system adtributor`
then `python experiments/slice_compare.py`.

## Setup

| | |
|---|---|
| Benchmark | 3 labelled real scenarios + 400 synthetic portfolios (10 items × 2 regions × 3 categories), seed 0 |
| Synthetic mix | 55% concentrated shock, 30% **distractor** (strong portfolio-wide drift → the largest slice carries the biggest raw \|Δ\|, while a small slice took a disproportionate hit), 15% null (noise only) |
| Baseline ("magnitude") | per dimension, rank elements by \|A−F\|, take them until the set covers `T_EP` of the total \|Δ\|; pick the dimension with the largest absolute contribution — what `pvm.products` does today |
| Adtributor | `analytics/adtributor.py` — Explanatory Power + Jensen-Shannon **surprise**, greedy element set gated by a per-element EP threshold and by surprise (not padded with un-surprising big slices), material-deviation gate before attributing anything |
| Measures | Revenue (fundamental) + GrossMarginPercent (derived, finite-difference EP); InventoryTurnover declines cleanly |

## Headline numbers (403 cases, seed 0; seeds 1–2 consistent)

| Metric | magnitude | Adtributor |
|---|--:|--:|
| dimension accuracy | 0.50 | **0.74** |
| exact element-set accuracy | 0.44 | **0.57** |
| top-1 element accuracy | 0.47 | **0.66** |
| mean element F1 | 0.46 | **0.63** |
| null cases abstained correctly | 1.00 | 1.00 |
| mean confidence — correct / wrong | 68 / 71 | **65 / 35** |

**Distractor subset (109 cases)** — a large slice whose magnitude moved but
whose *share* did not:

| Metric | magnitude | Adtributor |
|---|--:|--:|
| dimension accuracy | 0.28 | **0.79** |
| exact element-set | 0.08 | **0.36** |
| top-1 element | 0.18 | **0.56** |
| element F1 | 0.16 | **0.49** |

## Where it is weak (carry forward)

1. **Distractor element still leaks into the set ~28% of the time** (vs
   magnitude's 19%). Adtributor gets the *dimension* right on distractor cases
   far more often, but under a large uniform background move the big slice
   still has high EP and small-but-nonzero surprise, so the greedy occasionally
   admits it. The surprise-gated stop condition cut this from ~55%; tightening
   `T_EEP` or the surprise floor would cut it further at some recall cost.
2. **Real scenarios are portfolio-thin.** A single-SKU monthly anomaly is often
   < 8% of total portfolio revenue, so at portfolio scope Adtributor correctly
   abstains. The live integration therefore scopes attribution to the anomaly's
   own (item, state) and breaks down by **store / category** — useful ("which
   CA store drove the Nov-2012 drop → CA_1, CA_3") but not something the mock
   data labels a ground truth for. The synthetic set carries the comparison.
3. **Only Revenue + GrossMarginPercent.** InventoryTurnover has no additive
   slice decomposition and returns `available: false` (same as PVM is N/A there).
4. **Trailing-mean forecast**, not the paper's ARMA — consistent with the rest
   of the repo's baselines.

## Integration (shipped)

- `analytics/adtributor.py`; `api_server.build_attribution_block()` →
  `attribution` key on every anomaly (scoped to its item/state); RBAC via
  `_mask_attribution_block` (supply_planner: whole block hidden — financial;
  vp_sales: SKU/store element names redacted; admin: full).
- Debug route `GET /api/anomalies/{key}/attribution`.
- Drawer: "Anomaly Attribution (by slice)" section (`renderDrawerAttribution`).
- Tests: `tests/test_adtributor.py` (5, all pass). Runs alongside EasyRCA and
  PVM — nothing about either changes.

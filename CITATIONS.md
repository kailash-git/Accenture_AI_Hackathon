# Citations & method attribution

Two published root-cause-analysis methods are reimplemented and integrated in
this project. Both run **alongside** the pre-existing Price–Volume–Mix (PVM)
decomposition, never replacing it. Full experimental write-up (benchmarks,
weaknesses, reproduction commands): [`experiments/REPORT.md`](experiments/REPORT.md).

| Lens | Question it answers | Paper | Module |
|---|---|---|---|
| PVM (pre-existing) | **how** — price vs volume vs mix | — | `Accenture/Accenture/src/analytics/pvm_analyzer.py` |
| EasyRCA | **why** — which upstream causal KPI variable | Assaad et al., AISTATS 2023 | `.../analytics/{causal_graph,rca_series,easy_rca}.py` |
| Adtributor | **where** — which item / region / store / category slice | Bhagwan et al., NSDI 2014 | `.../analytics/adtributor.py` |

---

## [1] EasyRCA — Assaad, Ez-Zejjari & Zan (AISTATS 2023)

> Charles K. Assaad, Imad Ez-Zejjari, Lei Zan. **"Root Cause Identification for
> Collective Anomalies in Time Series given an Acyclic Summary Causal Graph
> with Loops."** *Proceedings of the 26th International Conference on Artificial
> Intelligence and Statistics (AISTATS)*, PMLR **206**:8395–8404, 2023.

```bibtex
@InProceedings{pmlr-v206-assaad23a,
  title     = {Root Cause Identification for Collective Anomalies in Time Series
               given an Acyclic Summary Causal Graph with Loops},
  author    = {Assaad, Charles K. and Ez-Zejjari, Imad and Zan, Lei},
  booktitle = {Proceedings of The 26th International Conference on Artificial
               Intelligence and Statistics},
  pages     = {8395--8404},
  year      = {2023},
  editor    = {Ruiz, Francisco and Dy, Jennifer and van de Meent, Jan-Willem},
  volume    = {206},
  series    = {Proceedings of Machine Learning Research},
  publisher = {PMLR},
  url       = {https://proceedings.mlr.press/v206/assaad23a.html}
}
```

Reference implementation: <https://github.com/ckassaad/EasyRCA>.
**Our implementation is from-scratch** (numpy + networkx + scipy only — no
`dowhy` / `tigramite` / `causal-learn`): d-separation decomposition → direct
identification → linear regime-comparison of each variable's structural
equation. The graph-with-loops case is out of scope; our summary graph is a DAG.

### What it contributes here
A hand-authored 10-variable summary causal graph
(`marketing_spend → units → revenue`, `sell_price → units`,
`stockout_days → fill_rate → units`, `sentiment` downstream of price/availability,
etc.); for each anomaly a weekly multivariate panel is built and EasyRCA names
the upstream variable whose own mechanism changed. Surfaced as `rootCause` on
every anomaly and in the drawer's "Causal Root-Cause Analysis" section.

### Measured improvement (Part 1 of `experiments/REPORT.md`)
Benchmark: 4 labelled scenarios + 300 synthetic causal panels, seed 0
(seeds 1–3 consistent). Baseline = the current PVM + evidence-graph + heuristic
attribution.

| Metric | Baseline | **EasyRCA** | Δ |
|---|--:|--:|--:|
| top-1 accuracy | 0.31 | **0.68** | **+0.37** |
| gold variable anywhere in output | 0.31 | **0.91** | **+0.60** |
| MRR | 0.31 | **0.78** | **+0.47** |
| false-attribution rate | 0.29 | **0.004** | **−0.29** |
| miss (should attribute, abstained) | 0.40 | **0.09** | **−0.31** |
| mean confidence — correct / wrong | 51 / 50 | **71 / 22** | separates signal |

Per intervention type (synthetic): structural shock top-1 **0.31 → 0.80**;
mechanism shift top-1 0.51 → 0.61 (but gold-in-list 0.95); null cases both
abstain ≈ 0.97. Real scenarios (of the 3 attributable — `supply`, `pricecut`,
`billing`): **1/3 → 2/3** correct — baseline gets `pricecut` wrong (day-over-day
PVM blames volume; EasyRCA names `sell_price`); both abstain on the deliberately
conflicting `billing` case. `sparse` (cold start): both abstain, correct.

---

## [2] Adtributor — Bhagwan, Kumar, Ramjee, Varghese, Mohapatra, Manoharan & Shah (NSDI 2014)

> Ranjita Bhagwan, Rahul Kumar, Ramachandran Ramjee, George Varghese,
> Surjyakanta Mohapatra, Hemanth Manoharan, Piyush Shah. **"Adtributor: Revenue
> Debugging in Advertising Systems."** *11th USENIX Symposium on Networked
> Systems Design and Implementation (NSDI '14)*, pp. 43–55, USENIX Association,
> 2014.

```bibtex
@inproceedings{bhagwan2014adtributor,
  title     = {Adtributor: Revenue Debugging in Advertising Systems},
  author    = {Bhagwan, Ranjita and Kumar, Rahul and Ramjee, Ramachandran and
               Varghese, George and Mohapatra, Surjyakanta and
               Manoharan, Hemanth and Shah, Piyush},
  booktitle = {11th USENIX Symposium on Networked Systems Design and
               Implementation (NSDI 14)},
  pages     = {43--55},
  year      = {2014},
  publisher = {USENIX Association},
  url       = {https://www.usenix.org/conference/nsdi14/technical-sessions/presentation/bhagwan}
}
```

**Our implementation is from-scratch** (numpy + pandas + sqlite3): Explanatory
Power (fraction of the total delta a slice accounts for) + **Surprise**
(Jensen-Shannon divergence between forecast and actual element-share
distributions) + succinctness (a per-element EP threshold, plus a surprise gate
so the set is not padded with large-but-unsurprising slices). Fundamental
measure (Revenue) and derived measure (GrossMarginPercent, via the paper's
finite-difference partial-derivative EP); InventoryTurnover declines cleanly.
Forecast = trailing-window mean (the paper uses ARMA).

### What it contributes here
For each anomaly, the deviation is attributed to the dimension + element set
(`item_id` / `state_id` / `store_id` / `cat_id`) whose share distribution
shifted most — ranked by surprise, not raw magnitude. Scoped to the anomaly's
own item/state. Surfaced as `attribution` on every anomaly and in the drawer's
"Anomaly Attribution (by slice)" section.

### Measured improvement (Part 2 of `experiments/REPORT.md`)
Benchmark: 3 labelled scenarios + 400 synthetic portfolios (10 items × 2
regions × 3 categories), seed 0 (seeds 1–2 consistent). Baseline ("magnitude")
= rank slices by raw |actual − forecast|, i.e. what the current per-product
breakdown (`pvm.products`, ordered by revenueImpact) does.

| Metric | magnitude (current) | **Adtributor** | Δ |
|---|--:|--:|--:|
| dimension accuracy | 0.50 | **0.74** | **+0.24** |
| exact element-set accuracy | 0.44 | **0.57** | **+0.13** |
| top-1 element accuracy | 0.47 | **0.66** | **+0.19** |
| mean element F1 | 0.46 | **0.63** | **+0.17** |
| mean confidence — correct / wrong | 68 / 71 | **65 / 35** | separates signal |
| null cases abstained correctly | 1.00 | 1.00 | — |

**Distractor subset** (109 cases — a large slice's magnitude moves while its
share is unchanged; the paper's headline motivation, "Data-Center-X vs
Mobile/Tablet"):

| Metric | magnitude | **Adtributor** | Δ |
|---|--:|--:|--:|
| dimension accuracy | 0.28 | **0.79** | **+0.51** |
| exact element-set | 0.08 | **0.36** | **+0.28** |
| top-1 element | 0.18 | **0.56** | **+0.38** |
| element F1 | 0.16 | **0.49** | **+0.33** |

Known caveat: the distractor element still enters Adtributor's set ~28% of the
time (vs magnitude's 19%) — it recovers the correct *dimension* far more often,
but under a large uniform background move the big slice retains high EP and
small non-zero surprise.

---

## Reproduce

```bash
# EasyRCA vs baseline
python experiments/run_eval.py --system baseline --n-synth 300 --seed 0
python experiments/run_eval.py --system easyrca  --n-synth 300 --seed 0
python experiments/compare.py

# Adtributor vs magnitude
python experiments/slice_eval.py --system magnitude  --n-synth 400 --seed 0
python experiments/slice_eval.py --system adtributor --n-synth 400 --seed 0
python experiments/slice_compare.py

# unit tests
cd Accenture/Accenture && PYTHONPATH="" python -m unittest discover -s tests
```

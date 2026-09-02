# Live Revenue What-If Simulator

Section 07 of the dashboard. Three sliders — **price adjustment**, **demand
shift**, **fill rate** — recompute a projected revenue number and a
Price / Volume / Interaction breakdown live in the browser. No backend calls:
every value is derived client-side from a small per-scenario economics block.

## Files

| File | Role |
|---|---|
| `js/simulator.js` | all the math and rendering (`simRender`, `simReset`, `simMatchRecorded`) |
| `dashboard.html` | the `#section-simulator` markup, the "What-If Simulator" nav tab, the `<script>` tag |
| `css/charts.css` | five `.sim-slider*` rules; everything else reuses `.pvm-*`, `.viz-big-number`, `.viz-delta-badge`, `.schema-field-*`, `.btn-action-outline` |
| `js/state.js` | `baselineEconomics` + `recordedOutcome` on each scenario |
| `js/app.js` | `section-simulator` added to the scroll-spy list; `simReset()` called at the end of `selectScenario` |
| `js/api.js` | `baselineEconomics` / `recordedOutcome` carried through `normalizeAnomalyForUI` |

## Inputs

| Slider | Unit | Range | DOM id |
|---|---|---|---|
| Price adjustment | percent | −80 … +150 | `#simPrice` |
| Demand shift | percent | −90 … +500 | `#simDemand` |
| Fill rate | fraction | 0.30 … 1.00 | `#simFill` |

Each slider's `oninput` calls `simRender()` — the only trigger.

## Per-scenario data (`js/state.js`)

```js
supply: {
  baselineEconomics: {
    unitPrice: 1.25, unitCost: 0.88, healthyBaselineRevenue: 44000,
    currentFillRate: 0.78, baselineFillRate: 0.98
  },
  recordedOutcome: { priceChangePct: 0, volumeChangePct: 0, fillRatePct: 78 },
  ...
}
```

Values are chosen to be consistent with facts already stated in that scenario's
narrative (supply: sell price steady at $1.25, supplier cost $0.88, fill rate
0.78 vs 0.98 baseline). Any scenario without these fields falls back to
`SIM_FALLBACK_ECONOMICS` / `SIM_FALLBACK_OUTCOME` in `js/simulator.js`.

## Math

`e` = the active scenario's `baselineEconomics`.

**Latent demand** — the fact we have is revenue, not units, so units are backed out:

```
price0          = e.unitPrice
fullStockDemand = e.healthyBaselineRevenue / price0
```

**Baseline reference** — the reset state: price unchanged, demand ×1, fill rate
at `baselineFillRate`:

```
units0 = fullStockDemand * 1.0 * e.baselineFillRate
rev0   = units0 * price0                              // the "default load" number
```

**Current state** — from the sliders:

```
price1  = price0 * (1 + priceAdj  / 100)
demand1 = fullStockDemand * (1 + demandShift / 100)
units1  = demand1 * fillRate         // fill rate caps how much demand converts
rev1    = units1 * price1            // the big projected number
```

**Stat row:**

```
Units Sold     = round(units1)
Gross Margin % = (rev1 - e.unitCost * units1) / rev1 * 100
Unit Price     = price1
```

## Price / Volume / Interaction decomposition

With `ΔP = price1 − price0`, `ΔV = units1 − units0`, `total = rev1 − rev0`:

```
ΔP·units0   +   ΔV·price0   +   ΔP·ΔV     ≡     rev1 − rev0
price effect    volume effect   interaction
```

This is exact — `P1·V1 − P0·V0` expands to precisely those three terms. To keep
the *displayed* integers summing with no gap, price and volume are rounded and
interaction takes the residual:

```
priceR       = round(ΔP · units0)
volumeR      = round(ΔV · price0)
interactionR = round(total) − priceR − volumeR
```

so `priceR + volumeR + interactionR === round(total)` for any slider position.
Bar height is `abs(effect) / maxEffect * 120px`; colour is the existing
`getPvmColor()` (red < 0, green > 0), rendered with the same
`.pvm-column-item` / `.pvm-solid-bar` markup as the Section 02 PVM waterfall.

## Buttons

- **Reset** — `simPrice = 0`, `simDemand = 0`, `simFill = e.baselineFillRate`, then `simRender()`.
- **Match Recorded Outcome** — sets the sliders to `recordedOutcome`
  (`fillRatePct / 100` for the fill slider), then `simRender()`. For supply that
  is `0% / 0% / 0.78`, reproducing the recorded ≈ −20 % revenue drop.

## Wiring

- **Scenario switch** — the sidebar cards call `selectScenario()`, which now ends
  with `if (typeof simReset === 'function') simReset()`, so a new scenario wipes
  any slider position and re-renders against its own economics.
- **Nav tab / scroll spy** — `'section-simulator'` was added to `sectionIds` in
  `setupNavigationScrollSpy()`; the section carries the `scroll-reveal` class and
  is picked up by `observeScrollRevealElements()` like every other section.
- **Field survival** — `normalizeAnomalyForUI(raw, existing)` merges live backend
  data over the `state.js` object. The backend never sends `baselineEconomics`,
  so an explicit line preserves it:
  `baselineEconomics: (existing && existing.baselineEconomics) || raw.baselineEconomics || null`.

## No backend

`simRender()` never calls `fetch`. It reads slider values and the static
`baselineEconomics` object only. All four numbers, the stat row, and the
waterfall are recomputed in the browser on every slider tick. The footnote in
the section notes the projection is modeled, not literal recorded history.

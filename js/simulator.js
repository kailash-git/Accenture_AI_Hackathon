/* ==========================================================================
   LIVE REVENUE WHAT-IF SIMULATOR  --  client-side only, no backend calls
   ========================================================================== */

const SIM_FALLBACK_ECONOMICS = {
  unitPrice: 1.50, unitCost: 1.00, healthyBaselineRevenue: 12000,
  currentFillRate: 0.95, baselineFillRate: 0.98
};
const SIM_FALLBACK_OUTCOME = { priceChangePct: 0, volumeChangePct: 0, fillRatePct: 95 };

function _simActiveAnom() {
  return (typeof ANOMALY_DATASET !== 'undefined' && ANOMALY_DATASET[APP_STATE.activeAnomalyKey]) || null;
}
function _simEcon() {
  const a = _simActiveAnom();
  return (a && a.baselineEconomics) || SIM_FALLBACK_ECONOMICS;
}
function _simOutcome() {
  const a = _simActiveAnom();
  return (a && a.recordedOutcome) || SIM_FALLBACK_OUTCOME;
}
function _simEls() {
  return {
    price: document.getElementById('simPrice'),
    demand: document.getElementById('simDemand'),
    fill: document.getElementById('simFill')
  };
}
function _simColor(v) {
  return (typeof getPvmColor === 'function') ? getPvmColor(v)
    : (v < 0 ? '#ef4444' : (v > 0 ? '#10b981' : '#718096'));
}

/* Pure: read the sliders + active economics, return every computed figure.
   Both simRender() (paints the DOM) and simDownloadReport() (builds the file)
   use this so they can never disagree. */
function _simCompute() {
  const els = _simEls();
  if (!els.price || !els.demand || !els.fill) return null;
  const e = _simEcon();
  const a = _simActiveAnom();

  const priceAdj = parseFloat(els.price.value);      // percent
  const demandShift = parseFloat(els.demand.value);  // percent
  const fillRate = parseFloat(els.fill.value);       // 0..1

  const price0 = e.unitPrice;
  const fullStockDemand = e.healthyBaselineRevenue / price0;
  const units0 = fullStockDemand * 1.0 * e.baselineFillRate;   // baseline reference
  const rev0 = units0 * price0;

  const price1 = price0 * (1 + priceAdj / 100);
  const units1 = fullStockDemand * (1 + demandShift / 100) * fillRate;
  const rev1 = units1 * price1;

  // three-term identity: price + volume + interaction === rev1 - rev0.
  // interaction takes the residual so the rounded parts sum with no gap.
  const totalChange = rev1 - rev0;
  const priceEffect = (price1 - price0) * units0;
  const volumeEffect = (units1 - units0) * price0;
  const totalR = Math.round(totalChange);
  const priceR = Math.round(priceEffect);
  const volumeR = Math.round(volumeEffect);
  const interactionR = totalR - priceR - volumeR;

  return {
    scenarioName: a ? `${a.title || 'Scenario'}${a.sku ? ' · ' + a.sku : ''}` : 'Scenario',
    priceAdj, demandShift, fillRate,
    price0, unitCost: e.unitCost,
    rev0, rev1: Math.round(rev1), units1: Math.round(units1), price1,
    pct: rev0 !== 0 ? (totalChange / rev0) * 100 : 0,
    marginPct: rev1 > 0 ? ((rev1 - e.unitCost * units1) / rev1) * 100 : 0,
    totalR, priceR, volumeR, interactionR,
    // exact magnitudes for bar heights
    mags: { price: Math.abs(priceEffect), volume: Math.abs(volumeEffect), interaction: Math.abs(totalChange - priceEffect - volumeEffect) }
  };
}

function simRender() {
  const c = _simCompute();
  if (!c) return;

  document.getElementById('simPriceVal').textContent = `${c.priceAdj > 0 ? '+' : ''}${c.priceAdj}%`;
  document.getElementById('simDemandVal').textContent = `${c.demandShift > 0 ? '+' : ''}${c.demandShift}%`;
  document.getElementById('simFillVal').textContent = c.fillRate.toFixed(2);

  document.getElementById('simRevenue').textContent = c.rev1.toLocaleString();
  const badge = document.getElementById('simRevDelta');
  badge.textContent = `${c.pct >= 0 ? '+' : ''}${c.pct.toFixed(1)}% vs baseline`;
  badge.classList.toggle('positive', c.pct >= 0);
  badge.classList.toggle('negative', c.pct < 0);
  document.getElementById('simRevDollarDelta').innerHTML =
    `&Delta; ${c.totalR >= 0 ? '+' : '-'}$${Math.abs(c.totalR).toLocaleString()} vs baseline`;

  document.getElementById('simUnits').textContent = c.units1.toLocaleString();
  document.getElementById('simMargin').textContent = `${c.marginPct.toFixed(1)}%`;
  document.getElementById('simPriceOut').textContent = `$${c.price1.toFixed(2)}`;

  const parts = [
    { label: 'Price', val: c.priceR, mag: c.mags.price },
    { label: 'Volume', val: c.volumeR, mag: c.mags.volume },
    { label: 'Interaction', val: c.interactionR, mag: c.mags.interaction }
  ];
  const maxV = Math.max(...parts.map(p => p.mag), 1);
  document.getElementById('simWaterfall').innerHTML = parts.map(p => {
    const color = _simColor(p.val);
    const h = Math.max(16, Math.round((p.mag / maxV) * 120));
    return `
      <div class="pvm-column-item">
        <div class="pvm-val-tag" style="color: ${color}">${p.val >= 0 ? '+' : '-'}$${Math.abs(p.val).toLocaleString()}</div>
        <div class="pvm-bar-track">
          <div class="pvm-solid-bar" style="height: ${h}px; background-color: ${color}; opacity: 0.9;"></div>
        </div>
        <div class="pvm-col-label">${p.label}</div>
      </div>`;
  }).join('');

  const nameEl = document.getElementById('simScenarioName');
  if (nameEl) nameEl.textContent = c.scenarioName;
}

function simReset() {
  const els = _simEls();
  if (!els.price) return;
  const e = _simEcon();
  els.price.value = 0;
  els.demand.value = 0;
  els.fill.value = e.baselineFillRate;
  simRender();
}

function simMatchRecorded() {
  const els = _simEls();
  if (!els.price) return;
  const o = _simOutcome();
  els.price.value = o.priceChangePct;
  els.demand.value = o.volumeChangePct;
  els.fill.value = (o.fillRatePct / 100);
  simRender();
}

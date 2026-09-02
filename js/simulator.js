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

function simRender() {
  const els = _simEls();
  if (!els.price || !els.demand || !els.fill) return;
  const e = _simEcon();

  const priceAdj = parseFloat(els.price.value);      // percent
  const demandShift = parseFloat(els.demand.value);  // percent
  const fillRate = parseFloat(els.fill.value);       // 0..1

  document.getElementById('simPriceVal').textContent = `${priceAdj > 0 ? '+' : ''}${priceAdj}%`;
  document.getElementById('simDemandVal').textContent = `${demandShift > 0 ? '+' : ''}${demandShift}%`;
  document.getElementById('simFillVal').textContent = fillRate.toFixed(2);

  const price0 = e.unitPrice;
  const fullStockDemand = e.healthyBaselineRevenue / price0;

  // baseline reference (the reset state): base price, demand x1, fill = baselineFillRate
  const units0 = fullStockDemand * 1.0 * e.baselineFillRate;
  const rev0 = units0 * price0;

  // current slider state
  const price1 = price0 * (1 + priceAdj / 100);
  const demand1 = fullStockDemand * (1 + demandShift / 100);
  const units1 = demand1 * fillRate;
  const rev1 = units1 * price1;

  // three-term identity: price + volume + interaction === rev1 - rev0.
  // interaction is taken as the residual so the three parts sum to the total
  // change with no gap, not even a floating-point one.
  const dP = price1 - price0;
  const dV = units1 - units0;
  const totalChange = rev1 - rev0;
  const priceEffect = dP * units0;
  const volumeEffect = dV * price0;
  const interactionEffect = totalChange - priceEffect - volumeEffect;

  const marginPct = rev1 > 0 ? ((rev1 - e.unitCost * units1) / rev1) * 100 : 0;
  const pct = rev0 !== 0 ? (totalChange / rev0) * 100 : 0;

  // Round for display so the three bars sum to exactly the shown total change:
  // interaction absorbs the rounding residual.
  const totalR = Math.round(totalChange);
  const priceR = Math.round(priceEffect);
  const volumeR = Math.round(volumeEffect);
  const interactionR = totalR - priceR - volumeR;

  document.getElementById('simRevenue').textContent = Math.round(rev1).toLocaleString();
  const badge = document.getElementById('simRevDelta');
  badge.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}% vs baseline`;
  badge.classList.toggle('positive', pct >= 0);
  badge.classList.toggle('negative', pct < 0);
  document.getElementById('simRevDollarDelta').innerHTML =
    `&Delta; ${totalR >= 0 ? '+' : '-'}$${Math.abs(totalR).toLocaleString()} vs baseline`;

  document.getElementById('simUnits').textContent = Math.round(units1).toLocaleString();
  document.getElementById('simMargin').textContent = `${marginPct.toFixed(1)}%`;
  document.getElementById('simPriceOut').textContent = `$${price1.toFixed(2)}`;

  const parts = [
    { label: 'Price', val: priceR, mag: Math.abs(priceEffect) },
    { label: 'Volume', val: volumeR, mag: Math.abs(volumeEffect) },
    { label: 'Interaction', val: interactionR, mag: Math.abs(interactionEffect) }
  ];
  const maxV = Math.max(...parts.map(p => p.mag), 1);
  document.getElementById('simWaterfall').innerHTML = parts.map(p => {
    const color = (typeof getPvmColor === 'function')
      ? getPvmColor(p.val) : (p.val < 0 ? '#ef4444' : (p.val > 0 ? '#10b981' : '#718096'));
    const h = Math.max(16, Math.round((p.mag / maxV) * 120));
    const f = `${p.val >= 0 ? '+' : '-'}$${Math.abs(p.val).toLocaleString()}`;
    return `
      <div class="pvm-column-item">
        <div class="pvm-val-tag" style="color: ${color}">${f}</div>
        <div class="pvm-bar-track">
          <div class="pvm-solid-bar" style="height: ${h}px; background-color: ${color}; opacity: 0.9;"></div>
        </div>
        <div class="pvm-col-label">${p.label}</div>
      </div>`;
  }).join('');

  const a = _simActiveAnom();
  const nameEl = document.getElementById('simScenarioName');
  if (nameEl) nameEl.textContent = a ? `${a.title || 'Scenario'}${a.sku ? ' · ' + a.sku : ''}` : 'Scenario';
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

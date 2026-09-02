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

/* Download a short standalone HTML report of the current simulation, with two
   inline-SVG charts. No backend, no libraries. */
function simDownloadReport() {
  const c = _simCompute();
  if (!c) return;
  const esc = s => String(s).replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]));
  const money = n => `${n < 0 ? '-' : ''}$${Math.abs(Math.round(n)).toLocaleString()}`;
  const now = new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC';

  // Chart 1: baseline vs projected revenue (two bars)
  const revMax = Math.max(c.rev0, c.rev1, 1);
  const revBar = (label, v, fill) => {
    const w = (v / revMax) * 380;
    return `<g transform="translate(0,${label === 'Baseline' ? 0 : 34})">
      <text x="0" y="14" font-size="12" fill="#555">${label}</text>
      <rect x="78" y="2" width="${w.toFixed(1)}" height="18" rx="3" fill="${fill}"/>
      <text x="${(82 + w).toFixed(1)}" y="16" font-size="12" fill="#222">${money(v)}</text>
    </g>`;
  };
  const chart1 = `<svg width="520" height="60" viewBox="0 0 520 60">
    ${revBar('Baseline', c.rev0, '#94a3b8')}
    ${revBar('Projected', c.rev1, c.pct >= 0 ? '#10b981' : '#ef4444')}
  </svg>`;

  // Chart 2: Price / Volume / Interaction diverging bars around a zero line
  const parts = [['Price', c.priceR], ['Volume', c.volumeR], ['Interaction', c.interactionR]];
  const m2 = Math.max(...parts.map(p => Math.abs(p[1])), 1);
  const zero = 200;
  const bars = parts.map(([label, v], i) => {
    const w = (Math.abs(v) / m2) * 170;
    const x = v >= 0 ? zero : zero - w;
    const fill = v < 0 ? '#ef4444' : (v > 0 ? '#10b981' : '#94a3b8');
    return `<g transform="translate(0,${i * 30})">
      <text x="0" y="16" font-size="12" fill="#555">${label}</text>
      <rect x="${x.toFixed(1)}" y="3" width="${Math.max(w, 1).toFixed(1)}" height="16" rx="3" fill="${fill}"/>
      <text x="${v >= 0 ? (x + w + 4).toFixed(1) : (x - 4).toFixed(1)}" y="16" text-anchor="${v >= 0 ? 'start' : 'end'}" font-size="12" fill="#222">${v >= 0 ? '+' : '-'}$${Math.abs(v).toLocaleString()}</text>
    </g>`;
  }).join('');
  const chart2 = `<svg width="440" height="94" viewBox="0 0 440 94">
    <line x1="${zero}" y1="0" x2="${zero}" y2="90" stroke="#ddd"/>${bars}
  </svg>`;

  const html = `<!doctype html><meta charset="utf-8"><title>What-If Report — ${esc(c.scenarioName)}</title>
<style>body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;max-width:640px;margin:32px auto;padding:0 16px}
h1{font-size:18px;margin:0 0 2px}.sub{color:#777;font-size:12px;margin-bottom:20px}
table{border-collapse:collapse;width:100%;margin:8px 0 18px}td{padding:4px 8px;border-bottom:1px solid #eee}td:last-child{text-align:right;font-variant-numeric:tabular-nums}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#666;margin:18px 0 6px}
.big{font-size:26px;font-weight:700}.delta{font-weight:700}.pos{color:#10b981}.neg{color:#ef4444}
footer{color:#999;font-size:11px;margin-top:24px}</style>
<h1>Live Revenue What-If — ${esc(c.scenarioName)}</h1>
<div class="sub">Generated ${now} · modeled projection, not literal recorded history</div>

<h2>Inputs</h2>
<table>
<tr><td>Price adjustment</td><td>${c.priceAdj > 0 ? '+' : ''}${c.priceAdj}%</td></tr>
<tr><td>Demand shift</td><td>${c.demandShift > 0 ? '+' : ''}${c.demandShift}%</td></tr>
<tr><td>Fill rate</td><td>${c.fillRate.toFixed(2)}</td></tr>
</table>

<h2>Projected outcome</h2>
<div class="big">$${c.rev1.toLocaleString()}</div>
<div class="delta ${c.pct >= 0 ? 'pos' : 'neg'}">${c.pct >= 0 ? '+' : ''}${c.pct.toFixed(1)}% &nbsp; (${money(c.totalR)}) vs baseline $${Math.round(c.rev0).toLocaleString()}</div>
${chart1}
<table>
<tr><td>Units sold</td><td>${c.units1.toLocaleString()}</td></tr>
<tr><td>Gross margin %</td><td>${c.marginPct.toFixed(1)}%</td></tr>
<tr><td>Unit price</td><td>$${c.price1.toFixed(2)}</td></tr>
</table>

<h2>Revenue change decomposition</h2>
${chart2}
<table>
<tr><td>Price effect</td><td>${c.priceR >= 0 ? '+' : '-'}$${Math.abs(c.priceR).toLocaleString()}</td></tr>
<tr><td>Volume effect</td><td>${c.volumeR >= 0 ? '+' : '-'}$${Math.abs(c.volumeR).toLocaleString()}</td></tr>
<tr><td>Interaction effect</td><td>${c.interactionR >= 0 ? '+' : '-'}$${Math.abs(c.interactionR).toLocaleString()}</td></tr>
<tr><td><b>Total change</b></td><td><b>${money(c.totalR)}</b></td></tr>
</table>

<footer>KPI Intelligence Engine · Live Revenue What-If Simulator · client-side model</footer>`;

  const blob = new Blob([html], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const slug = (APP_STATE.activeAnomalyKey || 'scenario').replace(/[^a-z0-9]+/gi, '-');
  a.href = url;
  a.download = `whatif-report-${slug}-${now.slice(0, 10)}.html`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (typeof showAppToast === 'function') showAppToast('What-If report downloaded');
}

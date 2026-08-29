/* ==========================================================================
   CHARTS & VISUALIZATIONS JS MODULE (Chart.js 4.4 Engine)
   ========================================================================== */

let mainRevChartInstance = null;
let gaugeChartInstance = null;

/* --------------------------------------------------------------------------
   Live streaming window (mirrors the KPI reference repo's appendLiveRecord).
   The chart starts on a small seed of the series, then every tick one more
   record is appended on the right; once the line reaches `maxPoints` the
   oldest record is shift()ed off the left, so the window physically scrolls
   and keeps going (wraps back to the start when `loop` is set). Purely
   client-side: the whole series is already fetched by selectScenario() /
   switchActiveKPI(), this only controls how fast it is fed onto the chart,
   so it also works against the offline demo fallback.
   -------------------------------------------------------------------------- */
const LIVE_STREAM_CONFIG = {
  enabled: true,
  tickMs: 1000,   // fixed gap between records
  maxPoints: 16,  // records visible at once -- older ones scroll off the left
  seed: 1,        // records shown before the stream starts appending
  loop: true      // when the last record lands, wrap to the start and keep scrolling
};
let _liveStreamTimer = null;
let _liveStreamFull = null;   // stashed full series for the current chart
let _liveStreamCursor = 0;

const REVENUE_TIMELINE_DATA = {
  all: {
    labels: ['Jan 12', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov 12', 'Dec', 'Jan 13', 'Feb', 'Mar', 'Apr', 'May 13', 'Jun', 'Jul', 'Aug 13'],
    values: [24200, 24800, 25100, 25600, 26200, 25800, 26900, 25200, 23800, 21400, 18200, 21100, 22800, 24100, 25400, 26200, 24900, 26800, 28400, 33200],
    headlineValue: '24,817',
    headlineDelta: '▼ 12.4%',
    isNegative: true,
    anomalies: {
      10: { key: 'supply', label: 'Supply Constraint (-$5.4k)', color: '#ef4444' },
      16: { key: 'billing', label: 'Billing Drift ($3.36)', color: '#f59e0b' },
      19: { key: 'pricecut', label: 'Price Cut (+42% Vol)', color: '#10b981' }
    }
  },
  '2012': {
    labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
    values: [24200, 24800, 25100, 25600, 26200, 25800, 26900, 25200, 23800, 21400, 18200, 21100],
    headlineValue: '21,950',
    headlineDelta: '▼ 18.2%',
    isNegative: true,
    anomalies: {
      10: { key: 'supply', label: 'Supply Constraint (-$5.4k)', color: '#ef4444' }
    }
  },
  '2013': {
    labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug'],
    values: [22800, 24100, 25400, 26200, 24900, 26800, 28400, 33200],
    headlineValue: '27,680',
    headlineDelta: '▲ 14.8%',
    isNegative: false,
    anomalies: {
      4: { key: 'billing', label: 'Billing Drift ($3.36)', color: '#f59e0b' },
      7: { key: 'pricecut', label: 'Price Cut (+42% Vol)', color: '#10b981' }
    }
  }
};

window.customTimelineData = null;

function setCustomTimelineData(data) {
  window.customTimelineData = data;
}

/* Formats a chart value per KPI -- $ for Revenue, plain count for Units, "%" for
   Gross Margin, "x" for Inventory Turnover. compact=true abbreviates large numbers
   for axis ticks (only meaningful for $/unit scales; margin/turnover are already
   small, human-scale numbers and must NOT be divided by 1000 like the old code
   did, which collapsed every margin/turnover tick to "0.0k"). */
function _formatKpiAxisValue(valueLabel, v, compact = false) {
  if (valueLabel === 'Gross Margin %') return `${v.toFixed(1)}%`;
  if (valueLabel === 'Inventory Turnover') return `${v.toFixed(2)}x`;
  const prefix = valueLabel === 'Revenue' ? '$' : '';
  if (compact) return `${prefix}${(v / 1000).toFixed(valueLabel === 'Revenue' ? 0 : 1)}k`;
  return `${prefix}${v.toLocaleString()}`;
}

/* The "All / 2012 / 2013" filter buttons only ever touched the static offline
   REVENUE_TIMELINE_DATA fallback -- once a real backend is connected,
   window.customTimelineData is used verbatim regardless of rangeKey, so the
   buttons silently did nothing (and the live series spans the M5 dataset's
   real ~2011-2016 history, not the "20 Mo" the old button label claimed).
   This filters the live series by 2-digit year suffix in its labels (e.g.
   "Jan 12") and recomputes the headline/delta for just that visible window. */
function _filterTimelineByYear(data, rangeKey) {
  if (rangeKey === 'all' || !data || !data.labels || !data.labels.length) return data;
  const suffix = ` ${rangeKey.slice(-2)}`;
  const keepIdx = data.labels.map((l, i) => l.endsWith(suffix) ? i : -1).filter(i => i >= 0);
  if (!keepIdx.length) return data;
  const labels = keepIdx.map(i => data.labels[i]);
  const values = keepIdx.map(i => data.values[i]);
  const anomalyIndex = keepIdx.includes(data.anomalyIndex) ? keepIdx.indexOf(data.anomalyIndex) : null;
  const first = values[0], last = values[values.length - 1];
  const deltaPct = first ? (last - first) / Math.abs(first) : 0;
  return {
    ...data, labels, values, anomalyIndex,
    headlineValue: undefined, // let the big-number formatter recompute from the filtered `last`
    headlineDelta: `${deltaPct * 100 >= 0 ? '+' : ''}${(deltaPct * 100).toFixed(1)}%`,
    isNegative: deltaPct < 0,
  };
}

function initRevenueChart(rangeKey = 'all') {
  const canvas = document.getElementById('mainRevenueCanvas');
  if (!canvas) return;

  let dataset;
  let anomalyMap = {};

  if (window.customTimelineData) {
    dataset = _filterTimelineByYear(window.customTimelineData, rangeKey);
    if (dataset.anomalyIndex !== null && dataset.anomalyIndex !== undefined) {
      anomalyMap[dataset.anomalyIndex] = {
        color: dataset.anomalyColor || '#ef4444',
        label: dataset.anomalyLabel || 'KPI Anomaly'
      };
    }
  } else {
    dataset = REVENUE_TIMELINE_DATA[rangeKey] || REVENUE_TIMELINE_DATA['all'];
    anomalyMap = dataset.anomalies || {};
  }

  // Point radii and color arrays
  const pointBgColors = dataset.values.map((_, i) => anomalyMap[i] ? anomalyMap[i].color : 'transparent');
  const pointBorderColors = dataset.values.map((_, i) => anomalyMap[i] ? '#ffffff' : 'transparent');
  const pointRadii = dataset.values.map((_, i) => anomalyMap[i] ? 7 : 2);
  const pointHoverRadii = dataset.values.map((_, i) => anomalyMap[i] ? 10 : 6);

  // Per-point metadata carried alongside the plotted arrays. The tooltip and
  // click handler read this (via chart.$live.meta) instead of closing over a
  // fixed index into `anomalyMap`/`dataset`, so it stays correct when the live
  // stream shift()s points off the front of the window.
  const liveMeta = dataset.values.map((_, i) => ({
    label: dataset.labels[i],
    anomKey: anomalyMap[i] ? anomalyMap[i].key : null,
    anomLabel: anomalyMap[i] ? anomalyMap[i].label : null
  }));

  // Kill any in-flight stream timer before the old chart is destroyed, so a
  // stray tick never calls .update() on a torn-down Chart.js instance during
  // rapid scenario switching.
  _stopLiveStream();

  if (mainRevChartInstance) {
    mainRevChartInstance.destroy();
  }

  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, 240);
  gradient.addColorStop(0, 'rgba(255, 255, 255, 0.05)');
  gradient.addColorStop(0.85, 'rgba(255, 255, 255, 0.005)');
  gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');

  mainRevChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: dataset.labels,
      datasets: [{
        data: dataset.values,
        borderColor: '#ffffff',
        borderWidth: 1.5,
        fill: true,
        backgroundColor: gradient,
        tension: 0.42,
        pointBackgroundColor: pointBgColors,
        pointBorderColor: pointBorderColors,
        pointBorderWidth: 2,
        pointRadius: pointRadii,
        pointHoverRadius: pointHoverRadii,
        pointHoverBorderColor: '#ffffff',
        pointHoverBorderWidth: 2.5
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 700,
        easing: 'easeOutQuart'
      },
      interaction: {
        mode: 'index',
        intersect: false
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1a20',
          borderColor: '#373742',
          borderWidth: 1,
          padding: 12,
          cornerRadius: 8,
          titleFont: { size: 13, weight: 'bold', family: 'Inter' },
          bodyFont: { size: 12, family: 'Inter' },
          titleColor: '#ffffff',
          bodyColor: '#9ca3af',
          callbacks: {
            title(items) {
              const ch = items[0].chart;
              const m = ch.$live && ch.$live.meta[items[0].dataIndex];
              const lbl = m ? m.label : ch.data.labels[items[0].dataIndex];
              return (m && m.anomLabel) ? `${lbl}  •  ${m.anomLabel}` : lbl;
            },
            label(item) {
              const vl = (item.chart.$live && item.chart.$live.valueLabel) || dataset.valueLabel || 'Revenue';
              return `  ${vl}:  ${_formatKpiAxisValue(vl, item.raw)}`;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(255, 255, 255, 0.04)' },
          ticks: {
            color: '#6b7280',
            font: { size: 11, family: 'Inter' }
          }
        },
        y: {
          grid: { color: 'rgba(255, 255, 255, 0.04)' },
          ticks: {
            color: '#6b7280',
            font: { size: 11, family: 'Inter' },
            callback: (v) => _formatKpiAxisValue(dataset.valueLabel || 'Revenue', v, true)
          }
        }
      },
      onClick(evt, elements, chart) {
        if (!elements || !elements.length) return;
        const m = chart.$live && chart.$live.meta[elements[0].index];
        if (m && m.anomKey) {
          openInvestigationDrawer(m.anomKey);
        }
      }
    }
  });

  // Attach the live metadata + value label so the tooltip/click callbacks and
  // the streaming ticker share one source of truth for each visible point.
  mainRevChartInstance.$live = { meta: liveMeta, valueLabel: dataset.valueLabel || 'Revenue' };

  // Update big number display. The "$" is the static #currencyPrefix span next to
  // #revBigNumber in the HTML, not part of this text -- it must be toggled off for
  // supply_planner role, where the timeline switches to Units (never $) per REQ-08
  // (a supply_planner-scoped request never receives revenue/$ figures). Previously
  // this span was never touched, and headlineValue's own fallback branch embedded a
  // second "$" for revenue mode -- vp_sales saw a doubled "$$9,554" and
  // supply_planner saw "$5,971" units mislabeled as a dollar figure.
  const numEl = document.getElementById('revBigNumber');
  const prefixEl = document.getElementById('currencyPrefix');
  const deltaEl = document.getElementById('revDeltaBadge');
  const isRevenueMetric = !dataset.valueLabel || dataset.valueLabel === 'Revenue';
  if (prefixEl) prefixEl.style.display = isRevenueMetric ? '' : 'none';
  let headlineValue = dataset.headlineValue;
  if (headlineValue === undefined) {
    const last = dataset.values[dataset.values.length - 1];
    if (last == null) {
      headlineValue = '—';
    } else if (dataset.valueLabel === 'Gross Margin %') {
      headlineValue = `${last.toFixed(1)}%`;
    } else if (dataset.valueLabel === 'Inventory Turnover') {
      headlineValue = `${last.toFixed(2)}x`;
    } else {
      headlineValue = Math.round(last).toLocaleString();
    }
  }
  if (numEl) numEl.textContent = headlineValue;
  if (deltaEl) {
    deltaEl.textContent = dataset.headlineDelta || '';
    deltaEl.className = `viz-delta-badge ${dataset.isNegative ? 'negative' : 'positive'}`;
  }

  // Hand the freshly built series to the live streaming window, or leave it
  // fully painted if streaming is disabled / the series is too short.
  if (LIVE_STREAM_CONFIG.enabled) _startLiveStream();
  else _syncStreamBtn(false);
}

/* Stops the streaming interval, if one is running. */
function _stopLiveStream() {
  if (_liveStreamTimer) {
    clearInterval(_liveStreamTimer);
    _liveStreamTimer = null;
  }
}

/* Stashes the freshly built series, trims the chart down to the seed, then
   starts appending one record per tick with a shift()ing window. */
function _startLiveStream() {
  _stopLiveStream();
  const chart = mainRevChartInstance;
  if (!chart) return;
  const ds = chart.data.datasets[0];
  const n = chart.data.labels.length;

  // Nothing to stream (RESTRICTED / empty / trivially short series).
  if (n <= LIVE_STREAM_CONFIG.seed + 1) { _liveStreamFull = null; _syncStreamBtn(false); return; }

  _liveStreamFull = {
    labels: chart.data.labels.slice(),
    data: (ds.data || []).slice(),
    pbg: Array.isArray(ds.pointBackgroundColor) ? ds.pointBackgroundColor.slice() : null,
    pbc: Array.isArray(ds.pointBorderColor) ? ds.pointBorderColor.slice() : null,
    pr: Array.isArray(ds.pointRadius) ? ds.pointRadius.slice() : null,
    phr: Array.isArray(ds.pointHoverRadius) ? ds.pointHoverRadius.slice() : null,
    meta: (chart.$live && chart.$live.meta) ? chart.$live.meta.slice() : []
  };

  const seed = Math.max(1, Math.min(LIVE_STREAM_CONFIG.seed, n - 1));
  chart.data.labels = _liveStreamFull.labels.slice(0, seed);
  ds.data = _liveStreamFull.data.slice(0, seed);
  if (_liveStreamFull.pbg) ds.pointBackgroundColor = _liveStreamFull.pbg.slice(0, seed);
  if (_liveStreamFull.pbc) ds.pointBorderColor = _liveStreamFull.pbc.slice(0, seed);
  if (_liveStreamFull.pr) ds.pointRadius = _liveStreamFull.pr.slice(0, seed);
  if (_liveStreamFull.phr) ds.pointHoverRadius = _liveStreamFull.phr.slice(0, seed);
  if (chart.$live) chart.$live.meta = _liveStreamFull.meta.slice(0, seed);
  chart.update('none');

  _liveStreamCursor = seed;
  _syncStreamBtn(true);
  _liveStreamTimer = setInterval(_liveStreamTick, LIVE_STREAM_CONFIG.tickMs);
}

/* One tick: append the next record on the right; drop the oldest off the left
   once the window is full; wrap to the start (or stop) at the end. */
function _liveStreamTick() {
  const chart = mainRevChartInstance;
  const full = _liveStreamFull;
  if (!chart || !chart.data || !full) { _stopLiveStream(); return; }
  const ds = chart.data.datasets[0];

  let i = _liveStreamCursor;
  if (i >= full.labels.length) {
    if (LIVE_STREAM_CONFIG.loop) { i = 0; }
    else { _stopLiveStream(); _syncStreamBtn(false); return; }
  }

  try {
    chart.data.labels.push(full.labels[i]);
    ds.data.push(full.data[i]);
    if (full.pbg) ds.pointBackgroundColor.push(full.pbg[i]);
    if (full.pbc) ds.pointBorderColor.push(full.pbc[i]);
    if (full.pr) ds.pointRadius.push(full.pr[i]);
    if (full.phr) ds.pointHoverRadius.push(full.phr[i]);
    if (chart.$live) chart.$live.meta.push(full.meta[i]);

    if (chart.data.labels.length > LIVE_STREAM_CONFIG.maxPoints) {
      chart.data.labels.shift();
      ds.data.shift();
      if (full.pbg) ds.pointBackgroundColor.shift();
      if (full.pbc) ds.pointBorderColor.shift();
      if (full.pr) ds.pointRadius.shift();
      if (full.phr) ds.pointHoverRadius.shift();
      if (chart.$live) chart.$live.meta.shift();
    }

    chart.update('none');
  } catch (err) {
    _stopLiveStream();
    return;
  }

  _liveStreamCursor = i + 1;
  _updateHeadlineFromStream(ds.data, (chart.$live && chart.$live.valueLabel) || 'Revenue');
}

/* Keeps the big-number + delta badge tracking the newest point in the visible
   window, mirroring initRevenueChart()'s own headline formatting. */
function _updateHeadlineFromStream(vals, valueLabel) {
  const numEl = document.getElementById('revBigNumber');
  const deltaEl = document.getElementById('revDeltaBadge');
  const clean = vals.filter(v => typeof v === 'number');
  if (!clean.length) return;
  const last = clean[clean.length - 1];
  const first = clean[0];

  if (numEl) {
    if (valueLabel === 'Gross Margin %') numEl.textContent = `${last.toFixed(1)}%`;
    else if (valueLabel === 'Inventory Turnover') numEl.textContent = `${last.toFixed(2)}x`;
    else numEl.textContent = Math.round(last).toLocaleString();
  }
  if (deltaEl && clean.length > 1 && first) {
    const pct = ((last - first) / Math.abs(first)) * 100;
    const neg = pct < 0;
    deltaEl.textContent = `${neg ? '▼' : '▲'} ${Math.abs(pct).toFixed(1)}%`;
    deltaEl.className = `viz-delta-badge ${neg ? 'negative' : 'positive'}`;
  }
}

/* Reflects stream state on the header button. */
function _syncStreamBtn(running) {
  const btn = document.getElementById('chartReplayBtn');
  if (!btn) return;
  btn.classList.toggle('is-streaming', !!running);
  btn.textContent = running ? '⏸ Pause' : '▶ Resume';
}

/* Header button: pause the stream where it is, or resume it from there. */
function chartReplayClicked() {
  if (_liveStreamTimer) {
    _stopLiveStream();
    _syncStreamBtn(false);
  } else if (_liveStreamFull) {
    _syncStreamBtn(true);
    _liveStreamTimer = setInterval(_liveStreamTick, LIVE_STREAM_CONFIG.tickMs);
  } else {
    replayRevenueStream();
  }
}

/* Rebuilds the chart from the current range/timeline, restarting the stream. */
function replayRevenueStream() {
  initRevenueChart(APP_STATE.activeTimeRange || 'all');
}

function setChartTimeRange(rangeKey, btnElement) {
  APP_STATE.activeTimeRange = rangeKey;
  document.querySelectorAll('.viz-filter-btn').forEach(btn => btn.classList.remove('active'));
  if (btnElement) btnElement.classList.add('active');
  initRevenueChart(rangeKey);
}

const KPI_METRIC_TITLE = { revenue: 'Revenue', margin: 'Gross Margin %', turnover: 'Inventory Turnover' };

/* Switches the trajectory chart between the engine's three connected KPIs
   (Revenue, Gross Margin %, Inventory Turnover -- all three are defined in
   schemas/semantic_contract.json and detectable via AnomalyDetector). This
   function didn't exist before -- the "Gross Margin %"/"Inventory Turnover" tabs
   called it via onclick and threw a silent ReferenceError, and the other two
   KPIs were never actually computed anywhere in the pipeline. */
async function switchActiveKPI(kpiKey, btnElement) {
  APP_STATE.activeKPI = kpiKey;
  document.querySelectorAll('[id^="kpiTab-"]').forEach(btn => btn.classList.remove('active'));
  if (btnElement) btnElement.classList.add('active');

  const titleEl = document.getElementById('mainChartTitle');
  const anom = ANOMALY_DATASET[APP_STATE.activeAnomalyKey];
  const regionLabel = anom ? (STATE_TO_REGION[anom.region] || anom.region || '') : '';

  if (typeof apiClient === 'undefined' || !apiClient.isConnected || !APP_STATE.activeAnomalyKey) {
    showAppToast('Live KPI switching requires a connected backend.');
    return;
  }

  const timeline = await apiClient.fetchAnomalyTimeline(APP_STATE.activeAnomalyKey, APP_STATE.activeRole, kpiKey);

  if (!timeline || timeline.restricted) {
    setCustomTimelineData({ labels: [], values: [], valueLabel: KPI_METRIC_TITLE[kpiKey], headlineValue: 'RESTRICTED', headlineDelta: '', isNegative: false, anomalyIndex: null });
    if (titleEl) titleEl.textContent = `${KPI_METRIC_TITLE[kpiKey]} — Restricted for this role`;
    initRevenueChart();
    showAppToast(`${KPI_METRIC_TITLE[kpiKey]} is restricted for the Supply Planner role (masked server-side).`);
    return;
  }

  if (titleEl) titleEl.textContent = `${KPI_METRIC_TITLE[kpiKey]} Trajectory — ${regionLabel} Region`;
  setCustomTimelineData(timeline);
  initRevenueChart();
}

/* Semicircular Gauge */
function initGaugeChart(score = 87) {
  const canvas = document.getElementById('confidenceGaugeCanvas');
  if (!canvas) return;

  if (gaugeChartInstance) {
    gaugeChartInstance.destroy();
  }

  const ctx = canvas.getContext('2d');
  gaugeChartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      datasets: [{
        data: [score, 100 - score],
        backgroundColor: ['#10b981', '#121215'],
        borderWidth: 0,
        borderRadius: [2, 0]
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      circumference: 180,
      rotation: -90,
      cutout: '76%',
      animation: {
        animateRotate: true,
        duration: 900
      },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false }
      }
    }
  });
}

/* PVM Waterfall Setup */
function getPvmColor(val) {
  if (val < 0) return '#ef4444';
  if (val > 0) return '#10b981';
  return '#718096';
}

function renderPvmWaterfall(anomalyKey = 'supply') {
  const anom = ANOMALY_DATASET[anomalyKey] || ANOMALY_DATASET.supply;
  const container = document.getElementById('pvmBarsContainer');
  if (!container) return;

  // Bars below were already recomputed per anomaly from anom.pvm -- this title was
  // the one static leftover ("...Nov 2012 Supply Anomaly") that never changed, so
  // switching scenarios looked like the chart was stuck even though the bars were
  // actually correct for whichever anomaly was selected.
  const titleEl = document.getElementById('pvmWaterfallTitle');
  if (titleEl) {
    const region = (typeof STATE_TO_REGION !== 'undefined' && STATE_TO_REGION[anom.region]) || anom.region || '';
    const period = (anom.date || '').toUpperCase();
    titleEl.textContent = `PVM Variance Waterfall — ${period} ${anom.title || ''} (${region})`;
  }

  if (!anom.pvm) { container.innerHTML = ''; return; }

  // GrossMarginPercent/InventoryTurnover anomalies carry an empty PVM result
  // (scripts/generate_mock_data.py -- PVM is a Revenue-specific decomposition, see
  // semantic_contract.json's driver_method note for these two KPIs). Rendering that
  // as four flat zero-height bars with no explanation would read as a broken chart,
  // not as the stated scope limitation it actually is.
  if (anom.kpiName && anom.kpiName !== 'Revenue') {
    const kpiLabel = (typeof KPI_DISPLAY_NAME !== 'undefined' && KPI_DISPLAY_NAME[anom.kpiName]) || anom.kpiName;
    container.innerHTML = `<div style="grid-column: 1 / -1; padding: 24px; text-align: center; color: var(--text-tertiary); font-size: 13px;">
      Price-Volume-Mix decomposition is Revenue-specific and doesn't apply to ${_escapeHtml(kpiLabel)}.
      See the Evidence &amp; Knowledge Graph section below for what explains this anomaly instead.
    </div>`;
    return;
  }

  const factors = [
    { key: 'volume', label: 'Volume', data: anom.pvm.volume },
    { key: 'price', label: 'Price', data: anom.pvm.price },
    { key: 'mix', label: 'Mix', data: anom.pvm.mix },
    { key: 'other', label: 'Other', data: anom.pvm.other }
  ].map(f => ({ ...f, color: typeof f.data.val === 'number' ? getPvmColor(f.data.val) : 'var(--text-tertiary)' }));

  const numericVals = factors.map(f => f.data.val).filter(v => typeof v === 'number');
  const maxVal = numericVals.length ? Math.max(...numericVals.map(Math.abs), 1) : 1;

  container.innerHTML = factors.map(f => {
    const isMasked = typeof f.data.val !== 'number';
    const heightPx = isMasked ? 6 : Math.max(16, Math.round((Math.abs(f.data.val) / maxVal) * 120));
    const formatted = isMasked ? 'RESTRICTED' : `${f.data.val > 0 ? '+' : ''}$${(f.data.val / 1000).toFixed(1)}k`;

    return `
      <div class="pvm-column-item" data-factor="${f.key}" onclick="togglePvmProductDrill('${f.key}')">
        <div class="pvm-val-tag" style="color: ${f.color}">${formatted}</div>
        <div class="pvm-bar-track">
          <div class="pvm-solid-bar" style="height: ${heightPx}px; background-color: ${f.color}; opacity: ${isMasked ? 0.3 : 0.9};"></div>
        </div>
        <div class="pvm-col-label">${f.label}</div>
        <div class="pvm-hover-card">
          <div class="pvm-tt-header">${f.label} Effect: ${formatted} (${f.data.pct})</div>
          <div class="pvm-tt-explanation">${f.data.expl}</div>
        </div>
      </div>
    `;
  }).join('');

  // Authored-once, correctly-signed one-liner. Percentages on the bars are share
  // of *baseline revenue* (additive to the deviation); when volume and price
  // oppose each other this sentence says so instead of implying one "explains 84%".
  const summary = anom.pvm.driver_summary;
  if (summary) {
    const opposing = anom.pvm.drivers_opposing ? ' pvm-summary-opposing' : '';
    container.insertAdjacentHTML('afterend',
      `<div class="pvm-driver-summary${opposing}" id="pvmDriverSummary">${_escapeHtml(summary)}</div>`);
    // insertAdjacentHTML on re-render would stack copies -- de-dupe.
    const all = document.querySelectorAll('#pvmDriverSummary');
    for (let i = 0; i < all.length - 1; i++) all[i].remove();
  } else {
    document.querySelectorAll('#pvmDriverSummary').forEach(el => el.remove());
  }
}

function togglePvmProductDrill(factorKey) {
  const panel = document.getElementById('pvmExpandedPanel');
  const titleEl = document.getElementById('pvmPanelTitle');
  const listEl = document.getElementById('pvmDrillList');
  if (!panel || !titleEl || !listEl) return;

  if (APP_STATE.openPvmFactor === factorKey) {
    panel.classList.remove('open');
    APP_STATE.openPvmFactor = null;
    return;
  }

  APP_STATE.openPvmFactor = factorKey;
  const anom = ANOMALY_DATASET[APP_STATE.activeAnomalyKey] || ANOMALY_DATASET.supply;
  titleEl.textContent = `${factorKey.toUpperCase()} Variance — Underlying Product Breakdown`;

  listEl.innerHTML = anom.products.map(p => `
    <div class="pvm-drill-row">
      <div>
        <span class="pvm-drill-sku">${p.sku}</span>
        <span style="font-size: 11px; color: var(--text-tertiary); margin-left: 8px;">${p.status}</span>
      </div>
      <div style="font-size: 12px; color: var(--text-secondary);">${p.volumeDelta} volume</div>
      <div style="font-weight: 700; color: ${p.revenueImpact.startsWith('-') ? 'var(--accent-red)' : 'var(--accent-green)'}">
        ${p.revenueImpact}
      </div>
    </div>
  `).join('');

  panel.classList.add('open');
}

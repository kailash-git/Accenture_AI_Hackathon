/* ==========================================================================
   ANOMALY REPORT  --  short standalone HTML export of the current anomaly,
   with two inline-SVG charts. Client-side only, no backend, no libraries.
   ========================================================================== */

function _rptEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]));
}
function _rptMoney(v) {
  return `${v < 0 ? '-' : ''}$${Math.abs(Math.round(v)).toLocaleString()}`;
}

/* Diverging horizontal bar chart (values around a centre 0 line). */
function _rptDivergingBars(rows, w = 460) {
  const max = Math.max(...rows.map(r => Math.abs(r.val)), 1);
  const labelW = 96, zero = labelW + (w - labelW) / 2, span = (w - labelW) / 2 - 40;
  const bars = rows.map((r, i) => {
    const bw = (Math.abs(r.val) / max) * span;
    const x = r.val >= 0 ? zero : zero - bw;
    const fill = r.val < 0 ? '#ef4444' : (r.val > 0 ? '#10b981' : '#94a3b8');
    const tx = r.val >= 0 ? x + bw + 4 : x - 4;
    return `<g transform="translate(0,${i * 28})">
      <text x="0" y="15" font-size="12" fill="#555">${_rptEsc(r.label)}</text>
      <rect x="${x.toFixed(1)}" y="3" width="${Math.max(bw, 1).toFixed(1)}" height="15" rx="3" fill="${fill}"/>
      <text x="${tx.toFixed(1)}" y="15" text-anchor="${r.val >= 0 ? 'start' : 'end'}" font-size="11" fill="#222">${r.disp}</text>
    </g>`;
  }).join('');
  return `<svg width="${w}" height="${rows.length * 28 + 4}" viewBox="0 0 ${w} ${rows.length * 28 + 4}">
    <line x1="${zero}" y1="0" x2="${zero}" y2="${rows.length * 28}" stroke="#e2e2e2"/>${bars}</svg>`;
}

/* Simple 0-100 confidence bar. */
function _rptConfidenceBar(pct) {
  const p = Math.max(0, Math.min(100, pct || 0));
  const fill = p >= 75 ? '#10b981' : (p >= 50 ? '#f59e0b' : '#ef4444');
  return `<svg width="460" height="26" viewBox="0 0 460 26">
    <rect x="0" y="6" width="360" height="12" rx="6" fill="#eee"/>
    <rect x="0" y="6" width="${(p / 100 * 360).toFixed(1)}" height="12" rx="6" fill="${fill}"/>
    <text x="372" y="16" font-size="12" fill="#222">${p.toFixed(0)}%</text></svg>`;
}

function downloadAnomalyReport() {
  const anom = (typeof ANOMALY_DATASET !== 'undefined' && ANOMALY_DATASET[APP_STATE.activeAnomalyKey]) || null;
  if (!anom) { if (typeof showAppToast === 'function') showAppToast('No anomaly selected'); return; }

  const now = new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  const status = (anom.status || 'active').replace(/^./, c => c.toUpperCase());

  // Chart 1 -- PVM decomposition (skip if masked to null for this role)
  const pvm = anom.pvm || {};
  const pvmRows = [
    ['Volume', pvm.volume], ['Price', pvm.price], ['Mix', pvm.mix], ['Other', pvm.other]
  ].filter(([, d]) => d && typeof d.val === 'number')
    .map(([label, d]) => ({ label, val: d.val, disp: `${d.val >= 0 ? '+' : '-'}$${Math.abs(Math.round(d.val)).toLocaleString()}` }));
  const pvmChart = pvmRows.length
    ? _rptDivergingBars(pvmRows)
    : '<div style="color:#999;font-size:12px">Not available for this KPI / role.</div>';

  // EasyRCA root cause
  const rc = anom.rootCause || {};
  const rcList = (rc.available && Array.isArray(rc.rootCauses) && rc.rootCauses.length)
    ? '<ul>' + rc.rootCauses.slice(0, 3).map(x =>
        `<li><b>${_rptEsc(x.variable)}</b> &mdash; ${_rptEsc(String(x.mechanism || '').replace(/_/g, ' '))} (${Number(x.effect).toFixed(1)}&sigma;)</li>`).join('') + '</ul>'
    : `<div style="color:#777;font-size:13px">${_rptEsc(rc.reason || 'Not available.')}</div>`;

  // Adtributor slice attribution
  const at = anom.attribution || {};
  const DIM = { item_id: 'item', state_id: 'region', store_id: 'store', cat_id: 'category' };
  const atList = (at.available && Array.isArray(at.candidates) && at.candidates.length)
    ? '<ul>' + at.candidates.slice(0, 3).map(c =>
        `<li><b>${_rptEsc(DIM[c.dimension] || c.dimension)} = ${_rptEsc((c.elements || []).join(', '))}</b> &mdash; EP ${Math.round((c.explanatory_power || 0) * 100)}%</li>`).join('') + '</ul>'
    : `<div style="color:#777;font-size:13px">${_rptEsc(at.reason || 'Not available.')}</div>`;

  const evCount = Array.isArray(anom.evidence) ? anom.evidence.length : 0;
  const syn = anom.synthesis || {};
  const act = anom.recommendedAction;

  const html = `<!doctype html><meta charset="utf-8"><title>Anomaly Report &mdash; ${_rptEsc(anom.title)}</title>
<style>body{font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;max-width:680px;margin:32px auto;padding:0 18px}
h1{font-size:19px;margin:0 0 2px}.sub{color:#777;font-size:12px;margin-bottom:6px}
.meta{font-size:12px;color:#555;margin-bottom:18px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#666;margin:22px 0 6px;border-top:1px solid #eee;padding-top:14px}
p{margin:6px 0}ul{margin:6px 0;padding-left:20px}li{margin:2px 0}
.state{margin:6px 0 4px;font-size:13px;color:#333}
.syn-title{font-weight:700}.syn-body{color:#444;font-size:13px}
footer{color:#999;font-size:11px;margin-top:26px;border-top:1px solid #eee;padding-top:12px}</style>

<h1>${_rptEsc(anom.title)}</h1>
<div class="meta">${_rptEsc(anom.sku || '')} &middot; ${_rptEsc(anom.region || '')} &middot; ${_rptEsc(anom.date || '')}
 &middot; ${_rptEsc(status)} &middot; ${Math.round(anom.confidence || 0)}% confidence${anom.abstained ? ' &middot; abstained' : ''}</div>

<p class="state"><b>${_rptEsc(anom.headline || '')}</b></p>
<p class="syn-body">${_rptEsc(anom.summary || '')}</p>

<h2>Detection confidence</h2>
${_rptConfidenceBar(anom.confidence)}

<h2>Price&ndash;Volume&ndash;Mix decomposition</h2>
${pvmChart}

<h2>Root cause &mdash; upstream variable (EasyRCA)</h2>
${rcList}

<h2>Attribution &mdash; responsible slice (Adtributor)</h2>
${atList}

<h2>Root cause synthesis</h2>
<p class="syn-title">${_rptEsc(syn.title || '')}</p>
<p class="syn-body">${_rptEsc(syn.body || '')}</p>

<h2>Recommended action</h2>
${act
  ? `<p><b>${_rptEsc(act.title || '')}</b></p><p class="syn-body">${_rptEsc(act.expectedImpact || act.expected_impact || '')}</p>`
  : `<p class="syn-body">${_rptEsc((anom.abstention && anom.abstention.reason) || 'Engine abstained &mdash; no automated recommendation.')}</p>`}

<footer>Generated ${now} from the KPI Intelligence Engine &middot; ${evCount} corroborating evidence record(s) &middot; figures are engine output, deterministic where the backend is reachable.</footer>`;

  const blob = new Blob([html], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const slug = (APP_STATE.activeAnomalyKey || 'anomaly').replace(/[^a-z0-9]+/gi, '-');
  a.href = url;
  a.download = `anomaly-report-${slug}-${now.slice(0, 10)}.html`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (typeof showAppToast === 'function') showAppToast('Anomaly report downloaded');
}

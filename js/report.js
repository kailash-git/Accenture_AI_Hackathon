/* ==========================================================================
   ANOMALY REPORT  --  client-side PDF export of the current anomaly, with two
   small vector charts drawn straight into the PDF. Uses jsPDF (loaded in
   dashboard.html); falls back to a standalone HTML file if jsPDF is missing.
   ========================================================================== */

function _rptData() {
  const anom = (typeof ANOMALY_DATASET !== 'undefined' && ANOMALY_DATASET[APP_STATE.activeAnomalyKey]) || null;
  if (!anom) return null;
  const DIM = { item_id: 'item', state_id: 'region', store_id: 'store', cat_id: 'category' };
  const rc = anom.rootCause || {};
  const at = anom.attribution || {};
  return {
    anom,
    status: (anom.status || 'active').replace(/^./, c => c.toUpperCase()),
    confidence: Math.round(anom.confidence || 0),
    pvm: [
      ['Volume', anom.pvm && anom.pvm.volume], ['Price', anom.pvm && anom.pvm.price],
      ['Mix', anom.pvm && anom.pvm.mix], ['Other', anom.pvm && anom.pvm.other]
    ].filter(([, d]) => d && typeof d.val === 'number').map(([label, d]) => ({ label, val: d.val })),
    rcLines: (rc.available && Array.isArray(rc.rootCauses) && rc.rootCauses.length)
      ? rc.rootCauses.slice(0, 3).map(x => `${x.variable} - ${String(x.mechanism || '').replace(/_/g, ' ')} (${Number(x.effect).toFixed(1)} sigma)`)
      : [rc.reason || 'Not available.'],
    atLines: (at.available && Array.isArray(at.candidates) && at.candidates.length)
      ? at.candidates.slice(0, 3).map(c => `${DIM[c.dimension] || c.dimension} = ${(c.elements || []).join(', ')}  (EP ${Math.round((c.explanatory_power || 0) * 100)}%)`)
      : [at.reason || 'Not available.'],
    evCount: Array.isArray(anom.evidence) ? anom.evidence.length : 0,
    now: new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
  };
}

function downloadAnomalyReport() {
  const d = _rptData();
  if (!d) { if (typeof showAppToast === 'function') showAppToast('No anomaly selected'); return; }

  const jsPDFCtor = window.jspdf && window.jspdf.jsPDF;
  if (!jsPDFCtor) { _downloadAnomalyReportHtml(d); return; }

  const a = d.anom;
  const doc = new jsPDFCtor({ unit: 'pt', format: 'a4' });
  const M = 48, W = doc.internal.pageSize.getWidth(), RIGHT = W - M, CW = RIGHT - M;
  let y = M;

  const need = h => { if (y + h > doc.internal.pageSize.getHeight() - M) { doc.addPage(); y = M; } };
  const heading = t => { need(28); y += 10; doc.setFont('helvetica', 'bold').setFontSize(9).setTextColor(110);
    doc.text(t.toUpperCase(), M, y); doc.setDrawColor(225).line(M, y + 4, RIGHT, y + 4); y += 16; };
  const para = (t, opt = {}) => {
    doc.setFont('helvetica', opt.bold ? 'bold' : 'normal').setFontSize(opt.size || 10)
      .setTextColor(opt.color != null ? opt.color : 40);
    const lines = doc.splitTextToSize(String(t || ''), CW);
    need(lines.length * (opt.lh || 13));
    doc.text(lines, M, y); y += lines.length * (opt.lh || 13) + (opt.gap || 4);
  };
  const list = arr => {
    doc.setFont('helvetica', 'normal').setFontSize(10).setTextColor(40);
    arr.forEach(item => {
      const lines = doc.splitTextToSize(item, CW - 12);
      need(lines.length * 13);
      doc.text('•', M, y);
      doc.text(lines, M + 12, y); y += lines.length * 13 + 2;
    });
    y += 4;
  };

  // ---- header ----
  doc.setFont('helvetica', 'bold').setFontSize(16).setTextColor(20);
  doc.text(doc.splitTextToSize(a.title || 'Anomaly', CW), M, y); y += 22;
  doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(110);
  doc.text([`${a.sku || ''}  ·  ${a.region || ''}  ·  ${a.date || ''}`,
    `${d.status}  ·  ${d.confidence}% confidence${a.abstained ? '  ·  abstained' : ''}`].join('    '), M, y);
  y += 16;
  para(a.headline || '', { bold: true, size: 11, gap: 2 });
  para(a.summary || '', { color: 70, size: 9.5, lh: 12 });

  // ---- confidence bar ----
  heading('Detection confidence');
  need(20);
  const p = Math.max(0, Math.min(100, d.confidence));
  const cFill = p >= 75 ? [16, 185, 129] : (p >= 50 ? [245, 158, 11] : [239, 68, 68]);
  doc.setFillColor(238).roundedRect(M, y, 320, 10, 5, 5, 'F');
  doc.setFillColor(...cFill).roundedRect(M, y, 320 * p / 100, 10, 5, 5, 'F');
  doc.setFont('helvetica', 'bold').setFontSize(10).setTextColor(30).text(`${p}%`, M + 330, y + 9);
  y += 20;

  // ---- PVM diverging bars ----
  heading('Price-Volume-Mix decomposition');
  if (d.pvm.length) {
    const max = Math.max(...d.pvm.map(r => Math.abs(r.val)), 1);
    const labelW = 70, zero = M + labelW + (CW - labelW) / 2, span = (CW - labelW) / 2 - 46;
    need(d.pvm.length * 20 + 6);
    doc.setDrawColor(225).line(zero, y - 2, zero, y - 2 + d.pvm.length * 20);
    d.pvm.forEach(r => {
      const bw = Math.abs(r.val) / max * span;
      const x = r.val >= 0 ? zero : zero - bw;
      const fill = r.val < 0 ? [239, 68, 68] : (r.val > 0 ? [16, 185, 129] : [148, 163, 184]);
      doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(90).text(r.label, M, y + 10);
      doc.setFillColor(...fill).roundedRect(x, y + 2, Math.max(bw, 1), 11, 2, 2, 'F');
      const lbl = `${r.val >= 0 ? '+' : '-'}$${Math.abs(Math.round(r.val)).toLocaleString()}`;
      doc.setTextColor(40);
      if (r.val >= 0) doc.text(lbl, x + bw + 4, y + 11);
      else doc.text(lbl, x - 4, y + 11, { align: 'right' });
      y += 20;
    });
    y += 4;
  } else {
    para('Not available for this KPI / role.', { color: 150, size: 9 });
  }

  heading('Root cause - upstream variable (EasyRCA)');
  list(d.rcLines);
  heading('Attribution - responsible slice (Adtributor)');
  list(d.atLines);

  const syn = a.synthesis || {};
  heading('Root cause synthesis');
  para(syn.title || '', { bold: true, size: 10 });
  para(syn.body || '', { color: 70, size: 9.5, lh: 12 });

  const act = a.recommendedAction;
  heading('Recommended action');
  if (act) {
    para(act.title || '', { bold: true, size: 10 });
    para(act.expectedImpact || act.expected_impact || '', { color: 70, size: 9.5, lh: 12 });
  } else {
    para((a.abstention && a.abstention.reason) || 'Engine abstained - no automated recommendation.', { color: 70, size: 9.5 });
  }

  y += 8; need(24);
  doc.setDrawColor(230).line(M, y, RIGHT, y); y += 12;
  doc.setFont('helvetica', 'normal').setFontSize(8).setTextColor(150);
  doc.text(doc.splitTextToSize(
    `Generated ${d.now} from the KPI Intelligence Engine  ·  ${d.evCount} corroborating evidence record(s)  ·  figures are engine output, deterministic where the backend is reachable.`,
    CW), M, y);

  const slug = (APP_STATE.activeAnomalyKey || 'anomaly').replace(/[^a-z0-9]+/gi, '-');
  doc.save(`anomaly-report-${slug}-${d.now.slice(0, 10)}.pdf`);
  if (typeof showAppToast === 'function') showAppToast('Anomaly report downloaded (PDF)');
}

/* Fallback when jsPDF failed to load (offline / CDN blocked). */
function _downloadAnomalyReportHtml(d) {
  const a = d.anom, esc = s => String(s == null ? '' : s).replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]));
  const rows = arr => '<ul>' + arr.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>';
  const pvm = d.pvm.map(r => `<tr><td>${esc(r.label)}</td><td>${r.val >= 0 ? '+' : '-'}$${Math.abs(Math.round(r.val)).toLocaleString()}</td></tr>`).join('');
  const syn = a.synthesis || {}, act = a.recommendedAction;
  const html = `<!doctype html><meta charset="utf-8"><title>Anomaly Report - ${esc(a.title)}</title>
<style>body{font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;max-width:680px;margin:32px auto;padding:0 18px}
h1{font-size:19px;margin:0 0 4px}.meta{color:#666;font-size:12px;margin-bottom:16px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#666;margin:22px 0 6px;border-top:1px solid #eee;padding-top:14px}
table{border-collapse:collapse;width:100%}td{padding:3px 8px;border-bottom:1px solid #eee}td:last-child{text-align:right}
footer{color:#999;font-size:11px;margin-top:24px;border-top:1px solid #eee;padding-top:12px}</style>
<h1>${esc(a.title)}</h1>
<div class="meta">${esc(a.sku || '')} &middot; ${esc(a.region || '')} &middot; ${esc(a.date || '')} &middot; ${esc(d.status)} &middot; ${d.confidence}% confidence${a.abstained ? ' &middot; abstained' : ''}</div>
<p><b>${esc(a.headline || '')}</b></p><p>${esc(a.summary || '')}</p>
<h2>Price-Volume-Mix decomposition</h2><table>${pvm || '<tr><td colspan=2>Not available.</td></tr>'}</table>
<h2>Root cause - upstream variable (EasyRCA)</h2>${rows(d.rcLines)}
<h2>Attribution - responsible slice (Adtributor)</h2>${rows(d.atLines)}
<h2>Root cause synthesis</h2><p><b>${esc(syn.title || '')}</b></p><p>${esc(syn.body || '')}</p>
<h2>Recommended action</h2><p>${act ? `<b>${esc(act.title || '')}</b><br>${esc(act.expectedImpact || act.expected_impact || '')}` : esc((a.abstention && a.abstention.reason) || 'Engine abstained.')}</p>
<footer>Generated ${d.now} &middot; ${d.evCount} evidence record(s) &middot; jsPDF unavailable, exported as HTML.</footer>`;
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
  const link = document.createElement('a');
  const slug = (APP_STATE.activeAnomalyKey || 'anomaly').replace(/[^a-z0-9]+/gi, '-');
  link.href = url; link.download = `anomaly-report-${slug}-${d.now.slice(0, 10)}.html`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (typeof showAppToast === 'function') showAppToast('Anomaly report downloaded (HTML - PDF lib unavailable)');
}

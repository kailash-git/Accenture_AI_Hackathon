/* ==========================================================================
   INVESTIGATION DRAWER JS MODULE
   ========================================================================== */

let drawerMiniChartInstance = null;

function openInvestigationDrawer(anomalyKey) {
  const anom = ANOMALY_DATASET[anomalyKey];
  if (!anom) return;

  APP_STATE.activeAnomalyKey = anomalyKey;
  APP_STATE.isDrawerOpen = true;

  // Sync active scenario card in sidebar
  document.querySelectorAll('.scenario-card').forEach(card => {
    card.classList.toggle('active', card.dataset.scenario === anomalyKey);
  });

  // Populate drawer header
  document.getElementById('drawerKicker').textContent = `${anom.category} · ${anom.date}`;
  document.getElementById('drawerTitle').textContent = anom.title;

  // Populate metadata grid
  document.getElementById('dMetaSku').textContent = anom.sku;
  document.getElementById('dMetaRegion').textContent = anom.region;
  document.getElementById('dMetaWarehouse').textContent = anom.warehouse;
  document.getElementById('dMetaZScore').textContent = anom.zScore;
  document.getElementById('dMetaDeviation').textContent = anom.deviation;

  const confFill = document.getElementById('dConfFill');
  const confText = document.getElementById('dConfPercentText');
  if (confFill && confText) {
    confFill.style.width = '0%';
    confText.textContent = `${anom.confidence}%`;
    const color = anom.confidence >= 75 ? 'var(--accent-green)' : (anom.confidence >= 50 ? 'var(--accent-amber)' : 'var(--accent-red)');
    confFill.style.backgroundColor = color;
    confText.style.color = color;

    setTimeout(() => {
      confFill.style.width = `${anom.confidence}%`;
    }, 100);
  }

  // Populate Mini PVM in Drawer (values may be server-masked to null for this role)
  const pvmLayout = document.getElementById('drawerPvmLayout');
  if (pvmLayout && anom.pvm) {
    const getPvmColorLocal = (val) => val < 0 ? '#ef4444' : (val > 0 ? '#10b981' : '#718096');
    const factors = [
      { label: 'Vol', val: anom.pvm.volume && anom.pvm.volume.val },
      { label: 'Price', val: anom.pvm.price && anom.pvm.price.val },
      { label: 'Mix', val: anom.pvm.mix && anom.pvm.mix.val },
      { label: 'Other', val: anom.pvm.other && anom.pvm.other.val }
    ];
    const numericVals = factors.map(f => f.val).filter(v => typeof v === 'number');
    const maxVal = numericVals.length ? Math.max(...numericVals.map(Math.abs), 1) : 1;

    pvmLayout.innerHTML = factors.map(f => {
      if (typeof f.val !== 'number') {
        return `
          <div class="d-pvm-col">
            <div class="d-pvm-val" style="color: var(--text-tertiary)">—</div>
            <div class="d-pvm-bar" style="height: 4px; background-color: var(--text-tertiary); opacity: 0.3"></div>
            <div class="d-pvm-lbl">${f.label}</div>
          </div>
        `;
      }
      const color = getPvmColorLocal(f.val);
      const heightPx = Math.max(10, Math.round((Math.abs(f.val) / maxVal) * 50));
      const formatted = `${f.val > 0 ? '+' : ''}${(f.val / 1000).toFixed(1)}k`;
      return `
        <div class="d-pvm-col">
          <div class="d-pvm-val" style="color: ${color}">${formatted}</div>
          <div class="d-pvm-bar" style="height: ${heightPx}px; background-color: ${color}"></div>
          <div class="d-pvm-lbl">${f.label}</div>
        </div>
      `;
    }).join('');
  }

  // Populate Mini Evidence in Drawer
  const evList = document.getElementById('drawerEvidenceList');
  if (evList) {
    evList.innerHTML = anom.evidence.map(ev => `
      <div class="d-ev-card" onclick="alert('Evidence Record: ${ev.type}\\n\\n${ev.fullText}')">
        <div class="d-ev-date">${ev.date} · ${ev.type}</div>
        <div class="d-ev-title">${ev.title}</div>
        <div class="d-ev-preview">${ev.preview}</div>
      </div>
    `).join('');
  }

  // Populate Action in Drawer -- or an abstention notice (REQ-05)
  const actionContainer = document.getElementById('drawerActionContainer');
  if (actionContainer) {
    if (anom.abstained || !anom.recommendedAction) {
      actionContainer.innerHTML = `
        <div style="font-size: 13px; font-weight: 700; color: var(--accent-amber, #f59e0b); margin-bottom: 6px;">
          Abstained — No Automated Recommendation
        </div>
        <div style="font-size: 12px; color: var(--text-secondary); line-height: 1.5; margin-bottom: 12px;">
          ${(anom.abstention && anom.abstention.reason) || 'Insufficient or contradictory evidence.'}
        </div>
        <button class="btn-outline-secondary" style="font-size: 12px;" onclick="closeInvestigationDrawer()">Dismiss</button>
      `;
    } else {
      const btnHtml = anom.isApproved
        ? `<button class="btn-solid-primary js-approve-action-btn" data-anomaly-key="${anomalyKey}" style="flex: 1; justify-content: center; font-size: 12px; background-color: #166534; color: #ffffff;" disabled><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> Approved & Dispatched</button>`
        : `<button class="btn-solid-primary js-approve-action-btn" data-anomaly-key="${anomalyKey}" style="flex: 1; justify-content: center; font-size: 12px;" onclick="handleActionApprove('${anomalyKey}', -1, this)">Approve Action</button>`;

      actionContainer.innerHTML = `
        <div style="font-size: 13px; font-weight: 700; color: var(--accent-green); margin-bottom: 6px;">
          ${anom.recommendedAction.title}
        </div>
        <div style="font-size: 12px; color: var(--text-secondary); line-height: 1.5; margin-bottom: 12px;">
          Owner: ${anom.recommendedAction.owner} · ${anom.recommendedAction.expectedImpact}
        </div>
        <div style="display: flex; gap: 8px;">
          ${btnHtml}
          <button class="btn-outline-secondary" style="font-size: 12px;" onclick="closeInvestigationDrawer()">Dismiss</button>
        </div>
      `;
    }
  }

  // Render Mini Chart in Drawer
  setTimeout(() => {
    renderDrawerMiniChart(anom);
  }, 150);

  // Show Drawer and Backdrop
  document.getElementById('drawerBackdrop').classList.add('active');
  document.getElementById('investigationDrawer').classList.add('open');
}

function renderDrawerMiniChart(anom) {
  const canvas = document.getElementById('drawerMiniChartCanvas');
  if (!canvas) return;

  if (drawerMiniChartInstance) {
    drawerMiniChartInstance.destroy();
  }

  const ctx = canvas.getContext('2d');
  const dataset = REVENUE_TIMELINE_DATA.all;

  drawerMiniChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: dataset.labels,
      datasets: [{
        data: dataset.values,
        borderColor: anom.confidence >= 75 ? '#10b981' : '#f59e0b',
        borderWidth: 1.8,
        fill: false,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1a20',
          borderColor: '#373742',
          borderWidth: 1,
          callbacks: {
            label: (item) => `$${item.raw.toLocaleString()}`
          }
        }
      },
      scales: {
        x: { display: false },
        y: { display: false }
      }
    }
  });
}

function closeInvestigationDrawer() {
  document.getElementById('drawerBackdrop').classList.remove('active');
  document.getElementById('investigationDrawer').classList.remove('open');
  APP_STATE.isDrawerOpen = false;
}

// Global escape key handler
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && APP_STATE.isDrawerOpen) {
    closeInvestigationDrawer();
  }
});

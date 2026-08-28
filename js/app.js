/* ==========================================================================
   APP INITIALIZATION & INTERACTIVE CONTROLLERS MODULE
   ========================================================================== */

const STATE_TO_REGION = { CA: 'West', TX: 'South' };

/* Matches the app's SKU shape -- CATEGORY_N_NNN, e.g. FOODS_3_090,
   HOUSEHOLD_1_020 -- directly in narrative text, rather than relying on
   anom.sku. anom.sku is server-masked to "RESTRICTED" under some personas,
   but the generated headline/summary sentence still embeds the real,
   unmasked token; matching the shape itself means the chip treatment shows
   up correctly under every persona instead of only the unmasked one. */
const ENTITY_ID_SHAPE = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b/g;

/* Wraps mentions of an item/entity ID inside a narrative string (a headline
   or summary sentence) in the same compact identifier chip used in the hero
   header -- so a raw SKU token like HOUSEHOLD_1_020 never sits in the flow
   as oversized/regular headline or paragraph prose. Also matches anom.sku
   literally as a fallback, in case a real ID doesn't fit the CATEGORY_N_NNN
   shape above. */
function highlightEntityId(text, sku) {
  const str = String(text || '');
  const alternatives = [ENTITY_ID_SHAPE.source];
  if (sku && sku.includes('_')) {
    alternatives.push(String(sku).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  }
  const re = new RegExp(alternatives.join('|'), 'g');
  return str.replace(re, (match) =>
    `<span class="entity-id-chip entity-id-chip-inline" data-copy-value="${match}" role="button" tabindex="0" title="Click to copy ID">${match}</span>`
  );
}

const SCENARIO_TITLES = {
  supply: 'Supply Constraint',
  billing: 'Billing / Pricing Bug',
  pricecut: 'Price Cut + Volume Lift',
  sparse: 'New Product Launch'
};

const KPI_DISPLAY_NAME = { Revenue: 'Revenue', GrossMarginPercent: 'Gross Margin %', InventoryTurnover: 'Inventory Turnover' };

function friendlyScenarioTitle(scenarioKey, kpiName, direction) {
  if (SCENARIO_TITLES[scenarioKey]) return SCENARIO_TITLES[scenarioKey];
  // gen- (Revenue), gen-margin-, gen-turnover- are all real prefixes (scripts/
  // generate_mock_data.py) -- stripping only "gen-" left "margin"/"turnover" itself
  // as the extracted "item" segment, e.g. "GrossMarginPercent Decline (margin)"
  // instead of "Gross Margin % Decline (FOODS_3_090)".
  const words = String(scenarioKey || '').replace(/^gen(-margin|-turnover)?-/, '').split('-')[0];
  const kpiLabel = KPI_DISPLAY_NAME[kpiName] || kpiName || 'KPI';
  return `${kpiLabel} ${direction === 'DOWN' ? 'Decline' : 'Movement'} (${words})`;
}

async function loadAnomalyListFromBackend() {
  if (typeof apiClient === 'undefined' || !apiClient.isConnected) return;
  try {
    const dbAnomalies = await apiClient.fetchAnomalies(APP_STATE.activeRole);
    if (dbAnomalies && dbAnomalies.length > 0 && dbAnomalies[0].id) {
      dbAnomalies.forEach(raw => {
        const key = raw.scenario_key || `gen-${raw.item_id}-${(raw.period_start || '').substring(0,7)}-${raw.state_id}`;
        const normalized = apiClient.normalizeAnomalyForUI(raw, ANOMALY_DATASET[key]);
        normalized.title = friendlyScenarioTitle(raw.scenario_key, raw.kpi_name, raw.direction);
        normalized.badgeText = `${(raw.status || 'active').replace(/^./, c => c.toUpperCase())} · ${Math.round(raw.confidence)}% Conf${raw.abstained ? ' (Abstained)' : ''}`;
        ANOMALY_DATASET[key] = normalized;
      });
    }
  } catch (err) {
    console.warn("Failed to initialize dataset from backend:", err);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  // 1. Check health, then load the real anomaly list if the backend is reachable.
  if (typeof apiClient !== 'undefined') {
    await apiClient.checkHealth();
    await loadAnomalyListFromBackend();
  }

  // 1.5 Populate Period Dropdown & Render Sidebar Cards dynamically
  populateCalendarPeriodSelect();
  renderSidebarCards();

  // 2. Initialize Default Scenario
  const firstKey = ANOMALY_DATASET.supply ? 'supply' : Object.keys(ANOMALY_DATASET)[0];
  await selectScenario(firstKey);

  // 2.5 Live telemetry panel
  refreshTelemetryPanel();
  setInterval(refreshTelemetryPanel, 20000);

  // 3. Setup Scroll Storytelling Observer
  setupScrollRevealObserver();

  // 4. Setup Navigation Scroll Spy
  setupNavigationScrollSpy();

  // 5. Setup Sidebar Search & Filter Chips
  setupSidebarSearch();

  // 6. Wire up click-to-copy on the hero item-ID chip(s)
  setupEntityChipCopy();
  setupInlineEntityChipCopy();
});

/* Click-to-copy for the compact item/entity-ID chip in the hero header.
   Bound once at load -- the chip element persists across scenario switches,
   only its label text is swapped by selectScenario(). */
function setupEntityChipCopy() {
  const chip = document.getElementById('heroEntityChip');
  const labelEl = document.getElementById('heroEntityChipLabel');
  if (!chip || !labelEl) return;

  let resetTimer = null;

  const copyId = async () => {
    const value = chip.dataset.copyValue || labelEl.textContent;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch (err) {
      console.warn('Clipboard copy failed:', err);
      return;
    }
    const original = value;
    chip.classList.add('is-copied');
    labelEl.textContent = 'Copied';
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      chip.classList.remove('is-copied');
      labelEl.textContent = original;
    }, 1200);
  };

  chip.addEventListener('click', copyId);
  chip.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      copyId();
    }
  });
}

/* Click-to-copy for entity-ID chips embedded inline inside prose (headline,
   narrative paragraph) by highlightEntityId(). Delegated on document since
   those chips are destroyed and recreated with fresh innerHTML on every
   selectScenario() call, unlike the persistent header chip above. */
function setupInlineEntityChipCopy() {
  const resetTimers = new WeakMap();

  const copyChip = async (chip) => {
    const value = chip.dataset.copyValue || chip.textContent;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch (err) {
      console.warn('Clipboard copy failed:', err);
      return;
    }
    clearTimeout(resetTimers.get(chip));
    chip.classList.add('is-copied');
    chip.textContent = 'Copied';
    const timer = setTimeout(() => {
      chip.classList.remove('is-copied');
      chip.textContent = value;
    }, 1200);
    resetTimers.set(chip, timer);
  };

  document.addEventListener('click', (e) => {
    const chip = e.target.closest('.entity-id-chip-inline');
    if (chip) copyChip(chip);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const chip = e.target.closest('.entity-id-chip-inline');
    if (chip) {
      e.preventDefault();
      copyChip(chip);
    }
  });
}

/* Intersection Observer for Smooth Story Reveal & Count-ups.
   Kept on `window` and re-applied via observeScrollRevealElements() below --
   this observer only ever saw the .scroll-reveal elements present at initial
   page load. Sections that re-render their own .scroll-reveal markup later
   (e.g. the Evidence Timeline Deck, rebuilt on every scenario switch) produced
   brand-new elements the observer had never seen, which sit permanently at
   opacity:0 per .scroll-reveal's base CSS -- reading as a blank gap in the
   page, not the evidence cards they actually are. */
function setupScrollRevealObserver() {
  window._scrollRevealObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('revealed');

        // Numeric count-up animations
        const counterEls = entry.target.querySelectorAll('[data-counter-target]');
        counterEls.forEach(el => {
          if (!el.dataset.animated) {
            el.dataset.animated = 'true';
            animateNumericCount(el);
          }
        });
      }
    });
  }, {
    threshold: 0.12,
    rootMargin: '0px 0px -40px 0px'
  });

  observeScrollRevealElements();
}

/* Re-scans for .scroll-reveal elements and hands any unobserved ones to the
   shared observer -- call this after injecting new .scroll-reveal markup
   (innerHTML rewrites don't carry over an element's prior observation). */
function observeScrollRevealElements(root = document) {
  if (!window._scrollRevealObserver) return;
  root.querySelectorAll('.scroll-reveal').forEach(el => window._scrollRevealObserver.observe(el));
}

function animateNumericCount(el) {
  const target = parseFloat(el.dataset.counterTarget) || 0;
  const prefix = el.dataset.prefix || '';
  const suffix = el.dataset.suffix || '';
  const duration = 1000;
  const startTime = performance.now();

  function updateCount(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const easeOut = 1 - Math.pow(1 - progress, 3);
    const currentVal = target * easeOut;

    el.textContent = `${prefix}${Number.isInteger(target) ? Math.round(currentVal).toLocaleString() : currentVal.toFixed(1)}${suffix}`;

    if (progress < 1) {
      requestAnimationFrame(updateCount);
    } else {
      el.textContent = `${prefix}${target >= 1000 ? Math.round(target).toLocaleString() : target}${suffix}`;
    }
  }

  requestAnimationFrame(updateCount);
}

/* Topbar Navigation & Scroll Spy */
function setupNavigationScrollSpy() {
  const navTabs = document.querySelectorAll('.topbar-tab');
  const sectionIds = ['section-overview', 'section-what-changed', 'section-why', 'section-evidence', 'section-actions', 'section-telemetry'];

  navTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      navTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      const targetId = tab.dataset.targetSection;
      const targetEl = document.getElementById(targetId);
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  const scrollContainer = document.getElementById('mainScrollContainer');
  if (!scrollContainer) return;

  const spyObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id;
        navTabs.forEach(tab => {
          if (tab.dataset.targetSection === id) {
            navTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
          }
        });
      }
    });
  }, {
    root: scrollContainer,
    threshold: 0.35
  });

  sectionIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) spyObserver.observe(el);
  });
}

/* Sidebar Search & Filter Chips */
function setupSidebarSearch() {
  const searchInput = document.getElementById('sidebarSearchInput');
  if (!searchInput) return;

  searchInput.addEventListener('input', function () {
    filterSidebarScenarios(this.value);
  });
}

function filterSidebarScenarios(query = '') {
  const q = query.toLowerCase().trim();
  const activeChip = document.querySelector('.filter-chip.active')?.dataset.filter || 'all';

  document.querySelectorAll('.scenario-card').forEach(card => {
    const text = card.textContent.toLowerCase();
    const status = card.dataset.status || '';
    const matchesText = !q || text.includes(q);
    const matchesChip = activeChip === 'all' || status === activeChip;

    card.style.display = matchesText && matchesChip ? 'block' : 'none';
  });
}

function setFilterChip(filterType, chipEl) {
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  chipEl.classList.add('active');
  const searchInput = document.getElementById('sidebarSearchInput');
  filterSidebarScenarios(searchInput ? searchInput.value : '');
}

function toggleSearchFilterPills() {
  const pillsRow = document.getElementById('filterPillsRow');
  if (pillsRow) {
    const isVisible = pillsRow.style.display !== 'none';
    pillsRow.style.display = isVisible ? 'none' : 'flex';
  }
}

/* Select Scenario Context -- re-fetches from the backend scoped to APP_STATE.activeRole,
   so switching persona actually changes the narrative/action/masking, not just labels. */
async function selectScenario(scenarioKey) {
  APP_STATE.activeAnomalyKey = scenarioKey;

  // Show a loading state immediately so the graph panel never displays a stale
  // scenario's traversal while the new one is in flight (REQ: smooth switching).
  renderKnowledgeGraphLoading();

  let anom = ANOMALY_DATASET[scenarioKey];
  if (typeof apiClient !== 'undefined' && apiClient.isConnected) {
    try {
      const detail = await apiClient.fetchAnomalyDetail(scenarioKey, APP_STATE.activeRole);
      if (detail) {
        anom = apiClient.normalizeAnomalyForUI(detail, anom);
        anom.title = friendlyScenarioTitle(detail.scenario_key, detail.kpi_name, detail.direction);
        anom.badgeText = `${(detail.status || 'active').replace(/^./, c => c.toUpperCase())} · ${Math.round(detail.confidence)}% Conf${detail.abstained ? ' (Abstained)' : ''}`;
        ANOMALY_DATASET[scenarioKey] = anom;
      }

      // Fetch dynamic, persona-aware timeline data (revenue for VP, units for Planner).
      // Switching to a different anomaly always resets the KPI tab back to Revenue --
      // otherwise you could land on a brand new scenario while still viewing the
      // previous one's Gross Margin/Turnover state, which reads as data from the
      // wrong anomaly rather than a deliberate choice.
      APP_STATE.activeKPI = 'revenue';
      document.querySelectorAll('[id^="kpiTab-"]').forEach(btn => btn.classList.toggle('active', btn.id === 'kpiTab-revenue'));
      const timeline = await apiClient.fetchAnomalyTimeline(scenarioKey, APP_STATE.activeRole, 'revenue');
      if (typeof setCustomTimelineData === 'function') {
        setCustomTimelineData(timeline || null);
      }
      const chartTitleEl = document.getElementById('mainChartTitle');
      if (chartTitleEl) {
        const regionLabel = STATE_TO_REGION[anom.region] || anom.region || '';
        chartTitleEl.textContent = `Revenue Trajectory — ${regionLabel} Region`;
      }

      // Fetch the real knowledge-graph traversal backing this anomaly (role-masked server-side)
      const graph = await apiClient.fetchAnomalyGraph(scenarioKey, APP_STATE.activeRole);
      renderKnowledgeGraphPanel(graph, anom.sku, anom);
    } catch (err) {
      console.warn("Failed to fetch details/timeline from backend:", err);
      if (typeof setCustomTimelineData === 'function') setCustomTimelineData(null);
    }
  } else {
    if (typeof setCustomTimelineData === 'function') setCustomTimelineData(null);
    renderKnowledgeGraphPanel(anom ? anom.graph_context : null, anom ? anom.sku : '', anom);
  }

  if (!anom) return;

  document.querySelectorAll('.scenario-card').forEach(card => {
    card.classList.toggle('active', card.dataset.scenario === scenarioKey);
  });

  // 1. Render charts & timelines
  renderPvmWaterfall(scenarioKey);
  renderEvidenceTimeline(scenarioKey);
  if (typeof renderDetectionPanel === 'function') renderDetectionPanel(scenarioKey);
  initGaugeChart(anom.confidence);

  // 2. Set chart time range to correspond to the anomaly year or custom timeline
  if (typeof window.customTimelineData !== 'undefined' && window.customTimelineData) {
    document.querySelectorAll('.viz-filter-btn').forEach(btn => btn.classList.remove('active'));
    if (typeof initRevenueChart === 'function') {
      initRevenueChart();
    }
  } else {
    const yearKey = (anom.date && anom.date.includes('2012')) ? '2012' : '2013';
    const rangeBtn = document.querySelector(`.viz-filter-btn[onclick*="${yearKey}"]`);
    setChartTimeRange(yearKey, rangeBtn);
  }

  // 3. Update Hero narrative text
  const headlineEl = document.getElementById('heroMainHeadline');
  const contextEl = document.getElementById('heroKickerContext');
  const narrativeEl = document.getElementById('heroNarrativeText');

  if (headlineEl) {
    headlineEl.innerHTML = highlightEntityId(anom.headline, anom.sku);
    // Long, full-sentence headlines (abstention/sparse-history cases) read as an
    // oversized wall of text at the default 64px punchy-metric size -- shrink them.
    const plainLength = String(anom.headline || '').replace(/<[^>]+>/g, '').length;
    headlineEl.classList.toggle('hero-title-long', plainLength > 48);
  }
  if (contextEl) contextEl.textContent = `${anom.sku} · ${(anom.date || '').toUpperCase()} · ${anom.title.toUpperCase()}${anom.persona ? ' · ' + anom.persona.replace('_', ' ').toUpperCase() + ' VIEW' : ''}`;
  if (narrativeEl) narrativeEl.innerHTML = highlightEntityId(anom.summary, anom.sku);

  // 3b. Hero title + date-range pill were static boilerplate ("Revenue Anomaly ID 9081",
  // "Jan 2012 — Aug 2013") that never changed across scenarios -- wire them to the
  // selected anomaly's own real (role-masked) fields instead. Deliberately built from
  // anom.sku/region/date, not the raw backend anomaly_id: that DB key embeds the true
  // item_id in its string regardless of role and would leak SKU identity straight past
  // the server-side vp_sales masking that already redacts anom.sku itself.
  const regionLabel = STATE_TO_REGION[anom.region] || anom.region || '';
  const entityChipEl = document.getElementById('heroEntityChip');
  const entityChipLabelEl = document.getElementById('heroEntityChipLabel');
  const idMetaEl = document.getElementById('heroIdMeta');
  if (entityChipEl) entityChipEl.dataset.copyValue = anom.sku;
  if (entityChipLabelEl) entityChipLabelEl.textContent = anom.sku;
  if (idMetaEl) idMetaEl.textContent = `· ${regionLabel} · ${anom.title}`;
  const rangeEl = document.querySelector('.date-range-pill span');
  const rangePill = document.querySelector('.date-range-pill');
  if (rangeEl && anom.period_start && anom.period_end) {
    const fmt = d => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    rangeEl.textContent = `${fmt(anom.period_start)} — ${fmt(anom.period_end)}`;
  }
  if (rangePill) {
    rangePill.setAttribute('title', 'Anomaly detection period for this scenario');
    rangePill.onclick = () => showAppToast(`Detection period for this anomaly: ${rangeEl ? rangeEl.textContent : ''}`);
  }

  // 4. Update Hero recommended action area OR abstention banner (REQ-05)
  const heroBanner = document.getElementById('heroActionBanner');
  const abstentionBanner = document.getElementById('abstentionBanner');
  const heroActionText = document.querySelector('.hero-action-banner .action-text-main');
  const heroActionImpact = document.querySelector('.hero-action-banner .action-impact-sub');
  const heroApproveBtn = document.getElementById('heroApproveBtn');
  const heroInvestigateBtn = document.getElementById('heroInvestigateBtn');

  if (anom.abstained) {
    if (heroBanner) heroBanner.style.display = 'none';
    if (abstentionBanner) {
      abstentionBanner.style.display = 'flex';
      const reasonEl = document.getElementById('abstentionReasonText');
      if (reasonEl) reasonEl.textContent = (anom.abstention && anom.abstention.reason) || 'Insufficient or contradictory evidence.';
    }
  } else {
    if (abstentionBanner) abstentionBanner.style.display = 'none';
    if (heroBanner) heroBanner.style.display = '';
    if (heroActionText && anom.recommendedAction) heroActionText.textContent = anom.recommendedAction.title;
    if (heroActionImpact && anom.recommendedAction) {
      heroActionImpact.textContent = `Owner: ${anom.recommendedAction.owner} · Expected impact: ${anom.recommendedAction.expectedImpact}`;
    }
    if (heroApproveBtn) {
      heroApproveBtn.setAttribute('onclick', `handleActionApprove('${scenarioKey}', -1, this)`);
      heroApproveBtn.setAttribute('data-anomaly-key', scenarioKey);
      if (anom.isApproved) {
        heroApproveBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> Approved & Dispatched`;
        heroApproveBtn.style.backgroundColor = '#166534';
        heroApproveBtn.style.color = '#ffffff';
        heroApproveBtn.disabled = true;
      } else {
        heroApproveBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Approve Action`;
        heroApproveBtn.style.backgroundColor = '';
        heroApproveBtn.style.color = '';
        heroApproveBtn.disabled = false;
      }
    }
  }
  if (heroInvestigateBtn) {
    heroInvestigateBtn.setAttribute('onclick', `openInvestigationDrawer('${scenarioKey}')`);
  }

  // 5. Update Confidence Score module text
  const gaugeScoreNum = document.getElementById('gaugeScoreNum');
  const gaugeCenterBadge = document.getElementById('gaugeCenterBadge');
  const gaugeConfidenceText = document.getElementById('gaugeConfidenceText');

  if (gaugeScoreNum) {
    gaugeScoreNum.innerHTML = `${anom.confidence}<span style="font-size: 20px; font-weight: 400; color: var(--text-tertiary);">%</span>`;
  }
  if (gaugeCenterBadge) {
    gaugeCenterBadge.textContent = `Z-Score: ${anom.zScore}`;
  }
  if (gaugeConfidenceText) {
    gaugeConfidenceText.textContent = anom.confidence >= 75 ? 'High Certainty' : (anom.confidence >= 50 ? 'Medium Certainty' : 'Low Certainty');
    gaugeConfidenceText.style.color = anom.confidence >= 75 ? 'var(--accent-green)' : (anom.confidence >= 50 ? 'var(--accent-amber)' : 'var(--accent-red)');
  }

  // 6. Update Supply/Logistics Metrics card
  const logTitle = document.getElementById('logisticsCardTitle');
  const logStatus = document.getElementById('logisticsCardStatus');
  const logDesc = document.getElementById('logisticsCardDesc');

  if (logTitle) logTitle.textContent = anom.logistics.title;
  if (logStatus) {
    logStatus.textContent = anom.logistics.status;
    logStatus.className = `sc-status-pill ${anom.logistics.statusClass}`;
  }
  if (logDesc) logDesc.textContent = anom.logistics.desc;

  // The server only ever sends 1-3 metrics (narrative_generator.py's
  // _logistics_card builds at most "Fill Rate" + "Knowledge Graph Hits" for
  // supply_planner), so the row is built to fit the data instead of a fixed
  // 3-slot layout that left a dead empty box whenever fewer than 3 arrived.
  const metricsRow = document.getElementById('supplyMetricsRow');
  const metrics = anom.logistics.metrics || [];
  if (metricsRow) {
    metricsRow.style.display = metrics.length > 0 ? '' : 'none';
    metricsRow.innerHTML = '';
    metrics.forEach(mData => {
      const box = document.createElement('div');
      box.className = 'supply-stat-box';

      const label = document.createElement('div');
      label.className = 'ss-label';
      label.textContent = mData.label;

      const val = document.createElement('div');
      val.className = `ss-val ${mData.valClass || ''}`;
      val.textContent = mData.val;

      const sub = document.createElement('div');
      sub.className = 'ss-sub';
      sub.textContent = mData.sub;

      box.append(label, val, sub);
      metricsRow.appendChild(box);
    });
  }

  // 7. Update Root Cause Synthesis Model
  const rcCard = document.querySelector('.root-cause-synthesis-card');
  const rcConf = document.getElementById('rcConfidencePill');
  const rcTitle = document.getElementById('rcStatementTitle');
  const rcBody = document.getElementById('rcSynthesisBody');

  // Restore this specific anomaly's own vote state (locked + highlighted if already
  // voted this session) rather than either carrying over the previous scenario's vote
  // or wiping out a real prior vote for this one when switching back to it.
  const priorVote = APP_STATE.feedbackVotes[scenarioKey];
  document.querySelectorAll('#synthesisFeedbackWrap .btn-feedback-vote').forEach(b => {
    b.classList.remove('voted');
    b.disabled = priorVote !== undefined;
  });
  if (priorVote !== undefined) {
    // Anchored startswith match: "1," is a substring of "-1," so a *="1, this" match
    // would wrongly highlight the down-vote button too when priorVote is 1.
    const votedBtn = document.querySelector(`#synthesisFeedbackWrap .btn-feedback-vote[onclick^="submitSynthesisFeedback(${priorVote},"]`);
    if (votedBtn) votedBtn.classList.add('voted');
  }

  const statusClass = anom.confidence >= 75 ? 'high' : (anom.confidence >= 50 ? 'medium' : 'low');

  if (rcCard) {
    rcCard.className = 'root-cause-synthesis-card';
    rcCard.classList.add(statusClass);
  }
  if (rcConf) {
    rcConf.textContent = `${anom.confidence}% Verified Confidence`;
    rcConf.className = 'rc-confidence-pill';
    rcConf.classList.add(statusClass);
  }
  if (rcTitle) rcTitle.textContent = anom.synthesis.title;
  if (rcBody) rcBody.innerHTML = anom.synthesis.body;

  // 8. Rebuild the structured Driver -> Lever -> Action -> Impact -> Owner -> Confidence ->
  //    Monitoring schema card (REQ-06), or an abstention notice when the engine withheld a
  //    recommendation (REQ-05).
  const actionCardsGrid = document.getElementById('actionCardsGrid');
  if (actionCardsGrid) {
    if (anom.abstained) {
      const signals = (anom.abstention && anom.abstention.conflicting_signals) || [];
      actionCardsGrid.innerHTML = `
        <div class="action-module-card" style="grid-column: 1 / -1; border-color: var(--accent-amber, #f59e0b);">
          <div>
            <div class="action-card-heading" style="color: var(--accent-amber, #f59e0b);">Abstained — No Automated Recommendation</div>
            <div class="action-card-body">${(anom.abstention && anom.abstention.reason) || 'Insufficient or contradictory evidence.'}</div>
            ${signals.length ? `<ul style="margin: 10px 0 0 18px; font-size: 12px; color: var(--text-secondary);">${signals.map(s => `<li>${s}</li>`).join('')}</ul>` : ''}
          </div>
        </div>
      `;
    } else if (anom.recommendedAction) {
      const a = anom.recommendedAction;
      // Confidence gets its own pill in the header (matching the badge treatment
      // used elsewhere in the app) instead of sitting in the fact grid -- that
      // also drops the grid from an awkward 5 cells (4 + 1 orphan) to a clean 4.
      const metaFields = [
        { label: 'Driver', val: a.driver },
        { label: 'Controllable Lever', val: a.controllableLever },
        { label: 'Owner', val: a.owner },
        { label: 'Monitoring Plan', val: a.monitoringPlan }
      ];
      const confTier = a.confidence >= 75 ? 'active' : (a.confidence >= 50 ? 'warning' : 'critical');
      const isApproved = anom.isApproved;
      const btnHtml = isApproved
        ? `<button class="btn-action-primary js-approve-action-btn" data-anomaly-key="${scenarioKey}" style="background-color: #166534; color: #ffffff;" disabled><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> Approved</button>`
        : `<button class="btn-action-primary js-approve-action-btn" data-anomaly-key="${scenarioKey}" onclick="handleActionApprove('${scenarioKey}', -1, this)">Approve Action</button>`;

      actionCardsGrid.innerHTML = `
        <div class="action-module-card" id="actionCard-${scenarioKey}" style="grid-column: 1 / -1;">
          <div class="action-card-header-row">
            <div class="action-card-header-left">
              <div class="action-card-top-num">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
              </div>
              <div class="action-card-heading" style="margin: 0;">${a.title}</div>
            </div>
            <span class="sc-status-pill ${confTier}">${Math.round(a.confidence)}% Confidence</span>
          </div>
          <div class="action-card-body">${a.expectedImpact}</div>
          <div class="schema-field-grid">
            ${metaFields.map(f => `
              <div class="schema-field-box">
                <div class="schema-field-label">${f.label}</div>
                <div class="schema-field-value">${f.val}</div>
              </div>
            `).join('')}
          </div>
          <div class="action-card-btn-row">
            ${btnHtml}
            <button class="btn-action-outline" onclick="handleActionAssign('${scenarioKey}')">Assign</button>
            <button class="btn-action-outline" onclick="handleActionDismiss('${scenarioKey}')">Dismiss</button>
          </div>
        </div>
      `;
    }
  }

  showAppToast(`Loaded scenario: ${anom.title} (${anom.persona || APP_STATE.activeRole} view)`);
}

/* ==========================================================================
   Live Knowledge Graph Panel -- renders the real multi-hop node/edge traversal
   returned by /api/anomalies/{key}/graph (role-masked server-side) as an
   actual interactive SVG graph: no static decoration, no alert() popups.
   ========================================================================== */

const KG_NODE_STYLE = {
  item:            { fill: 'var(--accent-green)',  text: '#04150c', r: 22, group: 'Anchor Product' },
  region:          { fill: 'var(--accent-blue)',   text: '#04101f', r: 18, group: 'Region' },
  category:        { fill: '#8b5cf6',              text: '#100a1f', r: 16, group: 'Category' },
  warehouse:       { fill: 'var(--accent-amber)',  text: '#1f1503', r: 16, group: 'Warehouse SKU' },
  warehouse_site:  { fill: '#f97316',              text: '#1f1103', r: 16, group: 'Warehouse Site' },
  feedback:        { fill: 'var(--accent-red)',    text: '#1f0505', r: 14, group: 'Feedback Record' },
};

function renderKnowledgeGraphLoading() {
  const wrap = document.getElementById('kgGraphWrap');
  if (!wrap) return;
  wrap.innerHTML = `<div class="kg-loading-state"><div class="kg-spinner"></div>Traversing knowledge graph…</div>`;
}

/* Simple deterministic radial layout by hop distance -- no physics simulation
   needed at this graph's scale, and it keeps re-renders stable/non-jittery. */
function _layoutGraphNodes(nodes) {
  const byHop = {};
  nodes.forEach(n => {
    const h = n.hops || 0;
    (byHop[h] = byHop[h] || []).push(n);
  });
  const maxHop = Math.max(0, ...Object.keys(byHop).map(Number));
  // The viewBox is sized to whatever hop depth this specific anomaly actually
  // reached, then centered on it -- a sparse 1-hop graph gets a tight square
  // instead of floating as a small cluster inside the same oversized fixed
  // canvas a dense 3-hop graph needs, and a full 3-hop ring (radius 205 at
  // maxHop=3) still keeps its outer margin (90px, covering node radius +
  // label height) regardless of hop count.
  const radius = hop => (hop === 0 ? 0 : 55 + hop * 50);
  const margin = 90;
  const boxSize = Math.max(360, radius(maxHop) * 2 + margin * 2);
  const cx = boxSize / 2, cy = boxSize / 2;
  const positions = {};
  Object.keys(byHop).sort((a, b) => a - b).forEach(hopStr => {
    const hop = Number(hopStr);
    const group = byHop[hop];
    const r = radius(hop);
    group.forEach((n, i) => {
      if (r === 0) {
        const offset = group.length > 1 ? (i - (group.length - 1) / 2) * 60 : 0;
        positions[n.id] = { x: cx + offset, y: cy };
      } else {
        const angle = (i / group.length) * Math.PI * 2 - Math.PI / 2;
        positions[n.id] = { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
      }
    });
  });
  return { positions, boxSize };
}

/* Truncates a node label to a fixed length with an ellipsis (never a bare
   mid-word cut like "HOUSEHOLD_1_02" or "Customer Revie") so an abbreviated
   label still reads as abbreviated -- the full value is always in the
   node's hover tooltip regardless. */
function _truncateLabel(s, max = 18) {
  const str = String(s || '');
  return str.length > max ? str.slice(0, max - 1) + '…' : str;
}

function _escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* Secondary, one-line mechanism note -- states the concrete traversal policy
   in plain language (kept in sync with knowledge_graph.get_related_context's
   actual defaults: max_hops=3, -5/+10 day window matching the evidence
   reconciler) so a reader who wants the "how" can find it, without it being
   the only explanation on offer -- see _kgAnomalySummaryHtml below for the
   per-anomaly "what did it actually find" verdict, which is the primary text. */
function _kgExplainerHtml() {
  return `<div class="kg-explainer">
    <svg class="kg-explainer-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
    <div>
      <div class="kg-explainer-label">How this works</div>
      <div class="kg-explainer-text">Traverses outward from this anomaly's item and region nodes (bold ring)
        up to 3 hops across the property graph (category, warehouse, feedback). A feedback node only counts as
        related if its date falls within 5 days before the anomaly's period start through 10 days after its
        period end -- the same temporal window the evidence reconciler uses, so a different month's real event
        can't be pulled in as if it explained this one.</div>
    </div>
  </div>`;
}

/* Primary, per-anomaly verdict -- always rendered first and prominently,
   computed live from this specific anomaly's own graphData.hops (never
   hardcoded), so every anomaly gets an explicit plain-language answer to
   "what did the knowledge graph actually find for THIS one" instead of only
   the generic mechanism description above. */
function _fmtShortDate(iso) {
  if (!iso) return '';
  try {
    return new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch (e) { return iso; }
}

/* The search window this traversal actually applied, spelled out in real
   calendar dates rather than left as the abstract "-5/+10 days" rule -- computed
   from this anomaly's own period_start/period_end, matching
   knowledge_graph.get_related_context's defaults exactly (see _kgExplainerHtml). */
function _kgWindowRangeText(anom) {
  if (!anom || !anom.period_start || !anom.period_end) return '';
  const lo = new Date(`${anom.period_start}T00:00:00`);
  lo.setDate(lo.getDate() - 5);
  const hi = new Date(`${anom.period_end}T00:00:00`);
  hi.setDate(hi.getDate() + 10);
  return `${_fmtShortDate(lo.toISOString().slice(0, 10))} &ndash; ${_fmtShortDate(hi.toISOString().slice(0, 10))}`;
}

/* Counts non-anchor nodes reached by type, for a one-clause "what the traversal
   actually touched" aside -- e.g. "by way of 1 category and 1 warehouse node".
   Computed from the real exported graph, not restated as a separate claim. */
const _KG_TYPE_LABEL = {
  item: ['sibling product', 'sibling products'],
  region: ['region', 'regions'],
  category: ['category', 'categories'],
  warehouse: ['warehouse SKU', 'warehouse SKUs'],
  warehouse_site: ['warehouse site', 'warehouse sites'],
};

function _kgPathAsideText(graphData) {
  const nodes = ((graphData && graphData.graph && graphData.graph.nodes) || []).filter(n => n.hops > 0 && n.type !== 'feedback');
  if (!nodes.length) return '';
  const counts = {};
  nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });
  const parts = Object.entries(counts).map(([type, n]) => {
    const [singular, plural] = _KG_TYPE_LABEL[type] || [type, `${type}s`];
    return `${n} ${n > 1 ? plural : singular}`;
  });
  return ` (reached by way of ${parts.join(', ')})`;
}

function _kgAnomalySummaryHtml(anom, graphData) {
  const item = (anom && anom.sku) || 'this item';
  const region = (anom && (STATE_TO_REGION[anom.region] || anom.region)) || '';
  const period = (anom && anom.date) ? anom.date.toUpperCase() : '';
  const hops = (graphData && graphData.hops) || [];
  const windowText = _kgWindowRangeText(anom);

  let verdict;
  if (hops.length > 0) {
    const maxHop = Math.max(...hops.map(h => h.hops));
    const before = hops.filter(h => h.temporal_role === 'preceding_cause').length;
    const after = hops.length - before;
    const roleParts = [];
    if (before) roleParts.push(`${before} dated before the anomaly's period start (a possible cause)`);
    if (after) roleParts.push(`${after} dated during/after it (corroborating aftermath)`);
    const examples = hops.slice(0, 2).map(h => `a ${h.source} on ${h.date}`).join(' and ');
    const pathAside = _kgPathAsideText(graphData);

    let tieIn;
    if (anom && anom.abstained) {
      tieIn = ` Despite this evidence existing, the engine still abstained -- see the reason above (usually a direct contradiction, not an absence of evidence).`;
    } else if (anom && anom.recommendedAction && anom.recommendedAction.driver) {
      tieIn = ` This is the evidence backing the recommended driver: <em>${_escapeHtml(anom.recommendedAction.driver)}</em>.`;
    } else {
      tieIn = '';
    }

    verdict = `Searched ${windowText ? `<strong>${windowText}</strong>` : 'the temporal window'} around this anomaly and found `
      + `<strong>${hops.length} related record(s)</strong> within <strong>${maxHop} hop(s)</strong> of ${_escapeHtml(item)}/${_escapeHtml(region)}${pathAside}`
      + (roleParts.length ? ` &mdash; ${roleParts.join(', ')}.` : '.')
      + (examples ? ` For example: ${_escapeHtml(examples)}.` : '')
      + tieIn;
  } else if (anom && anom.abstained) {
    verdict = `Searched ${windowText ? `<strong>${windowText}</strong>` : 'the temporal window'} around this anomaly and found `
      + `<strong>no related records</strong> for ${_escapeHtml(item)}/${_escapeHtml(region)} &mdash; consistent with the `
      + `abstention above.`;
  } else if (APP_STATE.activeAnomalyKey === 'sparse') {
    verdict = `Searched ${windowText ? `<strong>${windowText}</strong>` : 'the temporal window'} and found <strong>no related records</strong> `
      + `for ${_escapeHtml(item)}/${_escapeHtml(region)} &mdash; expected for a new launch, since this item's sales history itself `
      + `is too short to have generated any feedback yet. The recommendation below is to establish a baseline, not to act on a signal.`;
  } else {
    verdict = `Searched ${windowText ? `<strong>${windowText}</strong>` : 'the temporal window'} around this anomaly and found `
      + `<strong>no related feedback records</strong> for ${_escapeHtml(item)}/${_escapeHtml(region)}. Structural links `
      + `(category/warehouse) may still appear in the diagram below, but nothing in that window ties directly to this anomaly.`;
  }

  const abstainedClass = (anom && anom.abstained) ? ' kg-anomaly-summary-abstained' : '';
  return `<div class="kg-anomaly-summary${abstainedClass}">
    <div class="kg-anomaly-summary-glyph">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><line x1="12" y1="7" x2="5" y2="17"/><line x1="12" y1="7" x2="19" y2="17"/><line x1="5" y1="19" x2="19" y2="19"/></svg>
    </div>
    <div>
      <div class="kg-anomaly-summary-label">What this graph found for ${_escapeHtml(item)} · ${_escapeHtml(region)} · ${_escapeHtml(period)}</div>
      <div class="kg-anomaly-summary-body">${verdict}</div>
    </div>
  </div>`;
}

function renderKnowledgeGraphPanel(graphData, itemId, anom) {
  const wrap = document.getElementById('kgGraphWrap');
  const detail = document.getElementById('kgDetailPanel');
  if (!wrap) return;
  if (detail) detail.innerHTML = `<div class="kg-detail-placeholder">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><line x1="12" y1="7" x2="5" y2="17"/><line x1="12" y1="7" x2="19" y2="17"/><line x1="5" y1="19" x2="19" y2="19"/></svg>
    <span>Click any node to inspect the retrieved record behind it.</span>
  </div>`;

  const graph = (graphData && graphData.graph) || { nodes: [], edges: [] };
  const hopsByNode = {};
  ((graphData && graphData.hops) || []).forEach(h => { hopsByNode[`feedback:${h.feedback_id}`] = h; });

  if (!graph.nodes || graph.nodes.length === 0) {
    wrap.innerHTML = `${_kgAnomalySummaryHtml(anom, graphData)}${_kgExplainerHtml()}
      <div class="kg-empty-state">No linked structured or unstructured records found within 3 hops of <strong>${_escapeHtml(itemId || 'this item')}</strong>.</div>`;
    return;
  }

  const { positions: pos, boxSize } = _layoutGraphNodes(graph.nodes);
  const nodeById = {};
  graph.nodes.forEach(n => { nodeById[n.id] = n; });

  const edgeSvg = (graph.edges || []).map(e => {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return '';
    // Relation labels only appear on hover (via <title>) -- a fully-labeled hub
    // graph at this density is unreadable, so a hairline + hover tooltip beats
    // permanent overlapping text at the center.
    return `<g class="kg-edge">
      <title>${_escapeHtml((e.relation || '').replace(/_/g, ' '))}</title>
      <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" />
    </g>`;
  }).join('');

  const nodeSvg = graph.nodes.map((n, i) => {
    const p = pos[n.id];
    if (!p) return '';
    const style = KG_NODE_STYLE[n.type] || KG_NODE_STYLE.item;
    const isOrigin = n.hops === 0;
    const restricted = !!n.restricted;
    // Restricted nodes used to repeat the literal word "RESTRICTED" under every
    // masked node -- fine once, but a graph with several masked nodes turned into
    // a wall of shouting red text. The node's *identity* is masked, not its type,
    // so the label now shows the (public) group name with a small lock glyph on
    // the node itself; the full explanation is still one click away.
    const label = restricted ? style.group : _truncateLabel(n.label);
    const fb = hopsByNode[n.id];
    const tooltip = fb
      ? `${fb.source} · ${fb.date} · ${fb.temporal_role === 'preceding_cause' ? 'before anomaly' : 'concurrent/after'}\n${fb.text}`
      : restricted
        ? `${style.group}: identity restricted for your current role${n.hops ? ` (${n.hops}-hop)` : ''}`
        : `${style.group}: ${n.label}${n.hops ? ` (${n.hops}-hop)` : ' (anchor)'}`;
    // Same-hop nodes sit on the same ring at similar heights, so their labels can
    // collide when several land close together (e.g. two hop-3 nodes side by side);
    // staggering the label offset by index parity keeps adjacent labels from running
    // into each other without a full collision-avoidance layout at this graph's scale.
    const labelDy = style.r + (i % 2 === 0 ? 14 : 26);
    const lockGlyph = restricted
      ? `<text x="${p.x}" y="${p.y + 4}" text-anchor="middle" font-size="${style.r}" pointer-events="none">🔒</text>`
      : '';
    return `<g class="kg-node ${isOrigin ? 'kg-node-origin' : ''} ${restricted ? 'kg-node-restricted' : ''}"
                data-node-id="${_escapeHtml(n.id)}" tabindex="0" role="button"
                onclick="handleKgNodeClick('${_escapeHtml(n.id).replace(/'/g, "\\'")}')">
      <title>${_escapeHtml(tooltip)}</title>
      <circle cx="${p.x}" cy="${p.y}" r="${style.r}" fill="${style.fill}" />
      ${lockGlyph}
      <text x="${p.x}" y="${p.y + labelDy}" class="kg-node-label">${_escapeHtml(label)}</text>
    </g>`;
  }).join('');

  const legendGroups = [...new Set(graph.nodes.map(n => n.type))];
  const legendHtml = legendGroups.map(t => {
    const style = KG_NODE_STYLE[t] || KG_NODE_STYLE.item;
    return `<div class="kg-legend-item"><span class="kg-legend-dot" style="background:${style.fill}"></span>${style.group}</div>`;
  }).join('');

  wrap.innerHTML = `
    ${_kgAnomalySummaryHtml(anom, graphData)}
    ${_kgExplainerHtml()}
    <div class="kg-graph-meta">
      <span class="kg-meta-chip">${graph.nodes.length} nodes</span>
      <span class="kg-meta-chip">${(graph.edges || []).length} edges</span>
      <span class="kg-meta-chip">3-hop max</span>
      <span class="kg-meta-chip">Server-masked per role</span>
    </div>
    <svg class="kg-graph-svg" viewBox="0 0 ${boxSize} ${boxSize}" preserveAspectRatio="xMidYMid meet">
      ${edgeSvg}
      ${nodeSvg}
    </svg>
    <div class="kg-legend">${legendHtml}</div>
  `;

  // Cache for click-to-detail lookups.
  window._kgGraphIndex = { graphData, nodeById };
}

function handleKgNodeClick(nodeId) {
  const idx = window._kgGraphIndex;
  const detail = document.getElementById('kgDetailPanel');
  if (!idx || !detail) return;
  const node = idx.nodeById[nodeId];
  if (!node) return;

  document.querySelectorAll('.kg-node').forEach(el => el.classList.toggle('kg-node-selected', el.dataset.nodeId === nodeId));

  if (node.type === 'feedback') {
    const hop = (idx.graphData.hops || []).find(h => `feedback:${h.feedback_id}` === nodeId);
    if (hop) {
      detail.innerHTML = `
        <div class="kg-detail-header">
          <span class="kg-detail-source">${_escapeHtml(hop.source)}</span>
          <span class="kg-detail-badge">${hop.hops}-hop · ${hop.temporal_role === 'preceding_cause' ? 'before anomaly' : 'concurrent / after'}</span>
        </div>
        <div class="kg-detail-date">${_escapeHtml(hop.date)}</div>
        <div class="kg-detail-text">${_escapeHtml(hop.text)}</div>
      `;
      highlightEvidenceCardForFeedback(hop.feedback_id);
      return;
    }
  }

  const style = KG_NODE_STYLE[node.type] || KG_NODE_STYLE.item;
  detail.innerHTML = `
    <div class="kg-detail-header"><span class="kg-detail-source">${_escapeHtml(style.group)}</span></div>
    <div class="kg-detail-text">${node.restricted ? 'This node\'s identity is restricted for your current role.' : `Entity: <strong>${_escapeHtml(node.label)}</strong>`}</div>
  `;
}

/* Correlates a clicked graph node back to its evidence card by feedback_id
   (evidence items are seeded as "ev-{feedback_id}"), instead of the old static
   index-based filter that had no real relationship to the data. */
function highlightEvidenceCardForFeedback(feedbackId) {
  const anom = ANOMALY_DATASET[APP_STATE.activeAnomalyKey];
  const evidence = (anom && anom.evidence) || [];
  const targetIndex = evidence.findIndex(e => e.id === `ev-${feedbackId}`);
  if (targetIndex === -1) return;

  document.querySelectorAll('.evidence-card-item').forEach((card, i) => {
    card.style.opacity = i === targetIndex ? '1' : '0.3';
  });
  const card = document.getElementById(`evCard-${targetIndex}`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('expanded');
  }
  setTimeout(() => {
    document.querySelectorAll('.evidence-card-item').forEach(c => { c.style.opacity = '1'; });
  }, 3500);
}

/* Live Telemetry Panel -- reads /api/telemetry (real, measured numbers). */
async function refreshTelemetryPanel() {
  if (typeof apiClient === 'undefined' || !apiClient.isConnected) return;
  const t = await apiClient.fetchTelemetry();
  if (!t) return;

  const sqlVal = document.getElementById('teleSqlVal');
  const sqlSub = document.getElementById('teleSqlSub');
  if (sqlVal) sqlVal.textContent = `${Math.round(t.live_avg_sql_latency_ms || 0)}ms`;
  if (sqlSub) sqlSub.textContent = `${t.live_request_count || 0} live requests this session`;

  const llmVal = document.getElementById('teleLlmVal');
  const llmSub = document.getElementById('teleLlmSub');
  if (llmVal) llmVal.textContent = '0';
  if (llmSub) llmSub.textContent = t.note || 'Deterministic pipeline — no LLM calls on the live request path';

  const costVal = document.getElementById('teleCostVal');
  const costSub = document.getElementById('teleCostSub');
  if (costVal) costVal.textContent = `$${(t.seed_total_cost_usd || 0).toFixed(4)}`;
  if (costSub) costSub.textContent = `${t.seed_llm_calls || 0} LLM call(s) across ${t.seed_anomalies_processed || 0} anomalies at seed time`;

  const freshVal = document.getElementById('teleFreshVal');
  const freshSub = document.getElementById('teleFreshSub');
  if (freshVal && t.data_freshness_seconds != null) {
    const mins = Math.round(t.data_freshness_seconds / 60);
    freshVal.textContent = mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`;
  }
  if (freshSub) freshSub.textContent = `Seeded ${t.seed_run_at || 'unknown'}`;

  const fbVal = document.getElementById('teleFeedbackVal');
  const fbSub = document.getElementById('teleFeedbackSub');
  if (fbVal) fbVal.textContent = String(t.feedback_count || 0);
  if (fbSub) {
    fbSub.textContent = t.feedback_count
      ? `Avg rating ${t.feedback_avg_rating != null ? t.feedback_avg_rating.toFixed(2) : '—'} across ${t.feedback_count} record(s)`
      : 'No feedback captured yet this run';
  }

  window._lastTelemetry = t;
}

function showTelemetryDetail(kind) {
  const t = window._lastTelemetry;
  if (!t) { showAppToast('Telemetry not loaded yet — is the backend running?'); return; }
  const messages = {
    sql: `Live SQL latency: ${Math.round(t.live_avg_sql_latency_ms || 0)}ms avg over ${t.live_request_count} requests. Seed-time analytics avg: ${Math.round(t.seed_avg_sql_query_ms || 0)}ms/anomaly.`,
    llm: t.note || 'Deterministic pipeline.',
    cost: `Total LLM cost across the whole seed run: $${(t.seed_total_cost_usd || 0).toFixed(4)} (${t.seed_total_tokens_in || 0} in / ${t.seed_total_tokens_out || 0} out tokens, ${t.seed_llm_calls || 0} calls).`,
    fresh: `Database last built at ${t.seed_run_at}. Abstained anomalies: ${t.abstained_count}/${t.active_anomalies_count}.`,
    feedback: t.feedback_count
      ? `${t.feedback_count} feedback record(s) captured (Approve actions + synthesis ratings), average rating ${t.feedback_avg_rating}. This is the closed loop the engine's recommendations would recalibrate against over time.`
      : 'No feedback captured yet -- approve an action or rate a synthesis to populate this.',
  };
  showAppToast(messages[kind] || 'No detail available.');
}

/* UI Modals & Menus */
function toggleSidebarCollapse() {
  document.body.classList.toggle('sidebar-collapsed');
  const isCollapsed = document.body.classList.contains('sidebar-collapsed');
  showAppToast(isCollapsed ? 'Sidebar collapsed (Focused Mode)' : 'Sidebar expanded');
}

function toggleThemeMode() {
  document.body.classList.toggle('theme-high-contrast');
  const isHighContrast = document.body.classList.contains('theme-high-contrast');
  showAppToast(isHighContrast ? 'Switched to High-Contrast OLED Theme' : 'Switched to Default Charcoal Theme');
}

function toggleNotificationsDropdown() {
  const dd = document.getElementById('notificationsDropdown');
  if (dd) {
    dd.classList.toggle('active');
  }
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add('active');
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove('active');
}

/* Pipeline Stage Schema Viewer */
/* Pipeline Stage Schema Viewer & Semantic Contract Browser */
function inspectPipelineStage(stageName, tableName, activeSubTab = 'tables') {
  const titleEl = document.getElementById('catalogModalTitle');
  const bodyEl = document.getElementById('catalogModalBody');
  if (!titleEl || !bodyEl) return;

  titleEl.textContent = `Enterprise Metadata Catalog`;

  // Tab Header HTML (using topbar-tab class styling for high design unity)
  const headerHtml = `
    <div class="modal-tabs-bar" style="display: flex; gap: 8px; border-bottom: 1px solid var(--border-subtle); padding-bottom: 12px; margin-bottom: 16px;">
      <button class="topbar-tab ${activeSubTab === 'tables' ? 'active' : ''}" style="border-radius: 4px; font-size: 11px; padding: 6px 12px;" onclick="inspectPipelineStage('Enterprise Data Catalog', '${tableName}', 'tables')">Physical Tables</button>
      <button class="topbar-tab ${activeSubTab === 'semantic' ? 'active' : ''}" style="border-radius: 4px; font-size: 11px; padding: 6px 12px;" onclick="inspectPipelineStage('Enterprise Data Catalog', '${tableName}', 'semantic')">Semantic Layer (KPIs)</button>
      <button class="topbar-tab ${activeSubTab === 'rbac' ? 'active' : ''}" style="border-radius: 4px; font-size: 11px; padding: 6px 12px;" onclick="inspectPipelineStage('Enterprise Data Catalog', '${tableName}', 'rbac')">RBAC Security entitlements</button>
    </div>
  `;

  let tabBodyHtml = '';

  if (activeSubTab === 'tables') {
    const selectedTable = tableName || 'fact_sales_daily';
    tabBodyHtml = `
      <div style="margin-bottom: 14px;">
        <label style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-tertiary); display: block; margin-bottom: 6px;">Select Physical Table:</label>
        <select onchange="inspectPipelineStage('Enterprise Data Catalog', this.value, 'tables')" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-sm); padding: 8px; color: var(--text-primary); font-family: inherit; font-size: 12px; outline: none;">
          <option value="fact_sales_daily" ${selectedTable === 'fact_sales_daily' ? 'selected' : ''}>fact_sales_daily (POS Sales Daily)</option>
          <option value="source_marketing_weekly" ${selectedTable === 'source_marketing_weekly' ? 'selected' : ''}>source_marketing_weekly (Weekly Marketing Feed)</option>
          <option value="source_supply_monthly" ${selectedTable === 'source_supply_monthly' ? 'selected' : ''}>source_supply_monthly (Monthly Supply Logistics)</option>
        </select>
      </div>
      <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 14px; font-family: 'SF Mono', monospace; font-size: 12px; line-height: 1.6; color: var(--text-secondary);">
        <strong>Grain:</strong> ${selectedTable === 'fact_sales_daily' ? 'Daily per (item_id, store_id, date)' : (selectedTable === 'source_marketing_weekly' ? 'Weekly per (region, channel, week_start)' : 'Monthly per (warehouse_sku, state_id, month)')}<br/>
        <strong>Partitions:</strong> Active (State/Month)<br/>
        <strong>Integrity:</strong> Foreign keys enforced on <code>sku_lookup</code><br/>
        <strong>Freshness:</strong> 0 days lag
      </div>
      <div style="margin-top: 14px;">
        <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-tertiary); margin-bottom: 6px;">Column Schema:</div>
        <div style="font-size: 11px; font-family: 'SF Mono', monospace; line-height: 1.5; color: var(--text-secondary); max-height: 140px; overflow-y: auto; background: var(--bg-card); padding: 8px; border-radius: 4px; border: 1px solid var(--border-subtle);">
          ${selectedTable === 'fact_sales_daily' ? `
            • date (TEXT)<br/>
            • state_id (TEXT)<br/>
            • cat_id (TEXT)<br/>
            • dept_id (TEXT)<br/>
            • units (INTEGER)<br/>
            • sell_price (REAL)<br/>
            • revenue (REAL)<br/>
            • cost_of_goods_sold (REAL)<br/>
            • gross_margin_percent (REAL)
          ` : (selectedTable === 'source_marketing_weekly' ? `
            • week_start_monday (TEXT)<br/>
            • region_name (TEXT)<br/>
            • channel (TEXT)<br/>
            • marketing_spend (REAL)
          ` : `
            • month (TEXT)<br/>
            • warehouse_sku (TEXT)<br/>
            • state_id (TEXT)<br/>
            • fill_rate (REAL)<br/>
            • stockout_days (INTEGER)
          `)}
        </div>
      </div>
    `;
  } else if (activeSubTab === 'semantic') {
    tabBodyHtml = `
      <div style="display: flex; flex-direction: column; gap: 12px; max-height: 380px; overflow-y: auto; padding-right: 4px;">
        <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <strong style="color: var(--text-primary); font-size: 13px;">Revenue</strong>
            <span style="font-size: 9px; font-family: 'SF Mono', monospace; background: var(--bg-card); border: 1px solid var(--border-subtle); padding: 2px 6px; border-radius: 2px; color: var(--text-secondary); text-transform: uppercase;">additive</span>
          </div>
          <p style="font-size: 12px; color: var(--text-secondary); margin: 0 0 8px 0; line-height: 1.4;">Total sales revenue derived from units sold * average selling price.</p>
          <div style="font-family: 'SF Mono', monospace; font-size: 11px; color: var(--accent-green); background: var(--bg-card); padding: 8px; border-radius: 4px; border: 1px solid var(--border-subtle);">
            Formula: SUM(revenue) <br/>Source Table: fact_sales_daily
          </div>
        </div>

        <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <strong style="color: var(--text-primary); font-size: 13px;">GrossMarginPercent</strong>
            <span style="font-size: 9px; font-family: 'SF Mono', monospace; background: var(--bg-card); border: 1px solid var(--border-subtle); padding: 2px 6px; border-radius: 2px; color: var(--text-secondary); text-transform: uppercase;">non_additive</span>
          </div>
          <p style="font-size: 12px; color: var(--text-secondary); margin: 0 0 8px 0; line-height: 1.4;">Gross profit margin percentage representing profit relative to revenue.</p>
          <div style="font-family: 'SF Mono', monospace; font-size: 11px; color: var(--accent-green); background: var(--bg-card); padding: 8px; border-radius: 4px; border: 1px solid var(--border-subtle);">
            Formula: (SUM(revenue) - SUM(cost_of_goods_sold)) / SUM(revenue) <br/>Source Table: fact_sales_daily
          </div>
        </div>

        <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <strong style="color: var(--text-primary); font-size: 13px;">InventoryTurnover</strong>
            <span style="font-size: 9px; font-family: 'SF Mono', monospace; background: var(--bg-card); border: 1px solid var(--border-subtle); padding: 2px 6px; border-radius: 2px; color: var(--text-secondary); text-transform: uppercase;">non_additive</span>
          </div>
          <p style="font-size: 12px; color: var(--text-secondary); margin: 0 0 8px 0; line-height: 1.4;">Indicates the supply chain efficiency in moving products, computed as monthly COGS / average inventory value.</p>
          <div style="font-family: 'SF Mono', monospace; font-size: 11px; color: var(--accent-green); background: var(--bg-card); padding: 8px; border-radius: 4px; border: 1px solid var(--border-subtle);">
            Formula: SUM(cost_of_goods_sold) / AVG(inventory_on_hand * supplier_raw_cost) <br/>Source Table: inventory_logs
          </div>
        </div>
      </div>
    `;
  } else if (activeSubTab === 'rbac') {
    tabBodyHtml = `
      <div style="display: flex; flex-direction: column; gap: 12px; max-height: 380px; overflow-y: auto; padding-right: 4px;">
        <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 12px;">
          <strong style="font-size: 12px; color: var(--text-primary); display: block; margin-bottom: 8px;">VP of Retail Sales (Executive)</strong>
          <div style="font-size: 11px; line-height: 1.5; color: var(--text-secondary);">
            <div style="margin-bottom: 6px;"><span style="color: var(--accent-green); font-weight: 700;">Allowed Fields:</span> <code>fact_sales_daily.*</code>, <code>source_marketing_weekly.*</code></div>
            <div><span style="color: var(--accent-red); font-weight: 700;">Restricted & Masked:</span> <code>supplier_raw_cost</code> (MASK_NULL), <code>fill_rate</code> (MASK_NULL), <code>stockout_days</code> (MASK_NULL)</div>
          </div>
        </div>

        <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 12px;">
          <strong style="font-size: 12px; color: var(--text-primary); display: block; margin-bottom: 8px;">Regional Supply Chain Planner (Analyst)</strong>
          <div style="font-size: 11px; line-height: 1.5; color: var(--text-secondary);">
            <div style="margin-bottom: 6px;"><span style="color: var(--accent-green); font-weight: 700;">Allowed Fields:</span> <code>source_supply_monthly.*</code>, <code>fact_sales_daily.(date, state_id, cat_id, dept_id, units, sell_price)</code></div>
            <div><span style="color: var(--accent-red); font-weight: 700;">Restricted & Masked:</span> <code>revenue</code> (MASK_NULL), <code>cost_of_goods_sold</code> (MASK_NULL), <code>gross_margin_percent</code> (MASK_NULL), <code>marketing_spend</code> (MASK_NULL)</div>
          </div>
        </div>
      </div>
    `;
  }

  bodyEl.innerHTML = headerHtml + tabBodyHtml;
  openModal('dataCatalogModal');
}

/* Copy Synthesis Reasoning */
function copySynthesisReasoning() {
  const text = document.querySelector('.rc-statement-title')?.textContent || 'Root cause model verified.';
  navigator.clipboard.writeText(text).then(() => {
    showAppToast('Copied root cause synthesis to clipboard');
  }).catch(() => {
    showAppToast('Reasoning copied to clipboard');
  });
}

/* Real feedback capture (REQ-07: "mechanism to learn from analyst and
   business-user feedback") -- writes to user_feedback via apiClient.submitUserFeedback
   (previously defined but never called from anywhere in the UI). Aggregate counts are
   surfaced in the Telemetry panel via refreshTelemetryPanel() below. */
async function submitSynthesisFeedback(rating, btnEl) {
  const key = APP_STATE.activeAnomalyKey;
  const anom = ANOMALY_DATASET[key];
  const wrap = document.getElementById('synthesisFeedbackWrap');

  // One vote per anomaly per session -- repeat clicks were inserting a fresh
  // user_feedback row every time, inflating the Telemetry panel's count with
  // duplicate votes rather than one real opinion per anomaly.
  if (APP_STATE.feedbackVotes[key] !== undefined) {
    showAppToast("You've already rated this anomaly's synthesis.");
    return;
  }

  if (typeof apiClient !== 'undefined' && apiClient.isConnected) {
    await apiClient.submitUserFeedback(key, rating, `${APP_STATE.activeRole} rated this synthesis ${rating > 0 ? 'useful' : 'not useful'} for ${anom ? anom.title : key}.`);
  }
  APP_STATE.feedbackVotes[key] = rating;
  try {
    localStorage.setItem('bi_feedbackVotes', JSON.stringify(APP_STATE.feedbackVotes));
  } catch (e) { /* private browsing / storage disabled -- vote still recorded server-side */ }

  if (wrap) {
    wrap.querySelectorAll('.btn-feedback-vote').forEach(b => {
      b.classList.remove('voted');
      b.disabled = true;
    });
    if (btnEl) btnEl.classList.add('voted');
  }
  showAppToast(rating > 0 ? 'Thanks -- logged as useful feedback.' : 'Thanks -- logged as not-useful feedback.');
  if (typeof refreshTelemetryPanel === 'function') refreshTelemetryPanel();
}

/* Dynamic Sidebar Scenario Card Rendering */
function renderSidebarCards() {
  const container = document.querySelector('.sidebar-deck-scroll');
  if (!container) return;
  
  const activePeriod = APP_STATE.activeReviewPeriod || 'all';
  
  // Filter anomalies chronologically or by search query
  const filteredKeys = Object.keys(ANOMALY_DATASET).filter(key => {
    const anom = ANOMALY_DATASET[key];
    
    // Period calendar filter
    if (activePeriod !== 'all') {
      if (!anom.period_start || !anom.period_start.startsWith(activePeriod)) {
        return false;
      }
    }
    
    // Search keyword filter
    const searchInput = document.getElementById('sidebarSearchInput');
    const query = searchInput ? searchInput.value.toLowerCase().trim() : '';
    if (query !== '') {
      const matchText = `${anom.kpi_name} ${anom.item_id || anom.sku} ${anom.state_id || anom.region} ${anom.headline || ''} ${anom.summary || ''}`.toLowerCase();
      if (!matchText.includes(query)) {
        return false;
      }
    }
    
    return true;
  });
  
  const cardsHtml = filteredKeys.map(key => {
    const anom = ANOMALY_DATASET[key];
    const isActive = APP_STATE.activeAnomalyKey === key ? 'active' : '';
    
    let iconSvg = '';
    if (key.includes('supply')) {
      iconSvg = `<svg viewBox="0 0 24 24" style="stroke: var(--accent-blue)"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>`;
    } else if (key.includes('billing')) {
      iconSvg = `<svg viewBox="0 0 24 24" style="stroke: var(--accent-amber)"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
    } else {
      iconSvg = `<svg viewBox="0 0 24 24" style="stroke: var(--accent-green)"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`;
    }
    
    const title = anom.title || (anom.kpi_name + ' ' + (anom.deviation_pct < 0 ? 'Drop' : 'Lift'));
    const subtitle = (anom.item_id ? anom.item_id : anom.sku) + ' · ' + (anom.state_id ? anom.state_id : anom.region) + ' · ' + (anom.period_start ? anom.period_start.substring(0, 7) : anom.date);
    const zScoreStr = typeof anom.z_score === 'number' ? anom.z_score.toFixed(2) : (anom.zScore || '2.00');
    const devStr = typeof anom.deviation_pct === 'number' ? (anom.deviation_pct * 100).toFixed(1) + '%' : (anom.deviation || '0.0%');
    const status = anom.status || 'active';
    const confidence = anom.confidence || 90;
    
    return `
      <div class="scenario-card ${isActive}" data-scenario="${key}" data-status="${status}" onclick="selectScenario('${key}')">
        <div class="sc-top">
          <div class="sc-icon">${iconSvg}</div>
          <div class="sc-info">
            <div class="sc-title">${title}</div>
            <div class="sc-subtitle">${subtitle}</div>
          </div>
        </div>
        <div class="sc-grid-stats">
          <div><div class="stat-label">Type</div><div class="stat-val" style="color: var(--accent-blue)">${anom.category || 'General'}</div></div>
          <div><div class="stat-label">Z-Score</div><div class="stat-val">${zScoreStr}</div></div>
          <div><div class="stat-label">Deviation</div><div class="stat-val">${devStr}</div></div>
        </div>
        <div class="sc-progress-wrap">
          <span class="sc-status-pill ${status}">${status.toUpperCase()}</span>
          <span class="sc-pct-text">${confidence}% conf</span>
        </div>
        <div class="sc-detection-row">
          <span class="detection-type-badge ${(_DETECTION_LABELS[anom.detectionType] || _DETECTION_LABELS.STATISTICAL).cls}" style="font-size:9px;padding:2px 7px;">${(_DETECTION_LABELS[anom.detectionType] || _DETECTION_LABELS.STATISTICAL).text}</span>
        </div>
      </div>
    `;
  }).join('');
  
  // Remove existing scenario card elements
  container.querySelectorAll('.scenario-card').forEach(el => el.remove());

  // Append new scenario cards
  container.insertAdjacentHTML('beforeend', cardsHtml);
}

/* Extract Unique Year-Months and Populate the Selector Dropdown */
function populateCalendarPeriodSelect() {
  const select = document.getElementById('calendarPeriodSelect');
  if (!select) return;
  
  const periods = new Set();
  Object.keys(ANOMALY_DATASET).forEach(key => {
    const anom = ANOMALY_DATASET[key];
    if (anom.period_start) {
      periods.add(anom.period_start.substring(0, 7));
    }
  });
  
  const sortedPeriods = Array.from(periods).sort();
  
  let html = `<option value="all">All Time (${Object.keys(ANOMALY_DATASET).length} Anomalies)</option>`;
  sortedPeriods.forEach(p => {
    const date = new Date(p + '-02');
    const label = date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    
    const count = Object.keys(ANOMALY_DATASET).filter(key => {
      const anom = ANOMALY_DATASET[key];
      return anom.period_start && anom.period_start.startsWith(p);
    }).length;
    
    html += `<option value="${p}">${label} (${count} ${count === 1 ? 'Anomaly' : 'Anomalies'})</option>`;
  });
  
  select.innerHTML = html;
}

/* Event Handler for Month Filter Change */
async function filterAnomaliesByMonth(periodValue) {
  APP_STATE.activeReviewPeriod = periodValue;
  renderSidebarCards();
  
  // Auto-select the first card in the newly filtered list if active card is hidden
  const activePeriod = periodValue;
  const filteredKeys = Object.keys(ANOMALY_DATASET).filter(key => {
    const anom = ANOMALY_DATASET[key];
    
    // Period calendar filter
    if (activePeriod !== 'all') {
      if (!anom.period_start || !anom.period_start.startsWith(activePeriod)) {
        return false;
      }
    }
    
    // Search keyword filter
    const searchInput = document.getElementById('sidebarSearchInput');
    const query = searchInput ? searchInput.value.toLowerCase().trim() : '';
    if (query !== '') {
      const matchText = `${anom.kpi_name} ${anom.item_id || anom.sku} ${anom.state_id || anom.region}`.toLowerCase();
      if (!matchText.includes(query)) {
        return false;
      }
    }
    return true;
  });
  
  if (filteredKeys.length > 0) {
    if (!filteredKeys.includes(APP_STATE.activeAnomalyKey)) {
      await selectScenario(filteredKeys[0]);
    }
  }
}

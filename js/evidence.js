/* ==========================================================================
   EVIDENCE & KNOWLEDGE GRAPH JS MODULE
   ========================================================================== */

/* Relevance framing shown to the user -- the underlying cosine-similarity score
   (evidence_reconciler.py) still drives sort order and the tier-color coding,
   it's just never surfaced as a raw number. A tier + one plain-language reason
   is the actual answer to "why is this here," which a bare "0.47" is not. */
const _EV_RELEVANCE = {
  high: { badge: 'Strong Match', reason: 'Directly matches this anomaly\'s signature -- a primary explanation.' },
  medium: { badge: 'Possible Match', reason: 'Plausibly related -- a secondary signal worth a look.' },
  low: { badge: 'Background Only', reason: 'Context that happened to occur this period, but doesn\'t explain the anomaly.' },
};

function renderEvidenceTimeline(anomalyKey = 'supply') {
  const anom = ANOMALY_DATASET[anomalyKey] || ANOMALY_DATASET.supply;
  const container = document.getElementById('evidenceTimelineDeck');
  if (!container) return;

  container.innerHTML = anom.evidence.map((ev, index) => {
    const rel = _EV_RELEVANCE[ev.similarityTier] || _EV_RELEVANCE.low;
    return `
    <div class="evidence-card-item scroll-reveal" id="evCard-${index}" onclick="toggleEvidenceAccordion(${index})">
      <div class="ev-node-bullet"></div>
      <div class="ev-card-surface">
        <div class="ev-card-header">
          <span class="ev-source-tag">${ev.type}</span>
          <span class="ev-similarity-badge ${ev.similarityTier}">${rel.badge}</span>
        </div>
        <div class="ev-item-title">${ev.title}</div>
        <div class="ev-item-preview">${ev.preview}</div>
        <div class="ev-relevance-reason">${rel.reason}</div>
        <div class="ev-full-transcript-panel" id="evTranscript-${index}">
          <div class="ev-meta-table">
            <div class="ev-meta-col">
              <div class="evm-label">Timestamp</div>
              <div class="evm-val">${ev.date}</div>
            </div>
            <div class="ev-meta-col">
              <div class="evm-label">Source Classification</div>
              <div class="evm-val">${ev.type}</div>
            </div>
          </div>
          <div class="ev-full-text-box">
            ${ev.fullText}
          </div>
        </div>
      </div>
    </div>
  `;
  }).join('');

  // Re-renders happen only in direct response to a user click (selecting a
  // scenario) -- the deck is already on-screen when this runs, so reveal
  // immediately rather than waiting on an IntersectionObserver. The observer
  // exists to progressively reveal content on the *initial* scroll-driven
  // page load; re-observing a freshly injected node here was unreliable in
  // practice and left every re-rendered card stuck at opacity:0.
  container.querySelectorAll('.scroll-reveal').forEach(el => el.classList.add('revealed'));
}

/* Detection: STATISTICAL / EVIDENCE-DRIVEN / HYBRID / SPARSE HISTORY badge.
   Backed by anomalies.detection_type (src/analytics/evidence_signal.py classification) --
   not a UI label chosen independently of the backend's actual discovery signal. */
const _DETECTION_LABELS = {
  STATISTICAL: { text: 'Detection: STATISTICAL', cls: 'statistical' },
  EVIDENCE_DRIVEN: { text: 'Detection: EVIDENCE-DRIVEN', cls: 'evidence-driven' },
  HYBRID: { text: 'Detection: HYBRID', cls: 'hybrid' },
  SPARSE_HISTORY: { text: 'Detection: SPARSE HISTORY', cls: 'sparse' },
};

function _buildDetectionBullets(anom) {
  const detectionType = anom.detectionType || 'STATISTICAL';
  if (detectionType === 'STATISTICAL' || detectionType === 'SPARSE_HISTORY') {
    return ['No corroborating unstructured evidence found.'];
  }

  const records = (anom.evidence || []).filter(ev => String(ev.source || '').startsWith('unstructured_feedback'));
  const bullets = [];
  if (records.length > 0) {
    bullets.push(`${records.length} supporting record${records.length === 1 ? '' : 's'} from unstructured feedback`);
    const topRecord = records[0];
    if (topRecord && topRecord.title) bullets.push(topRecord.title);
  }
  if (anom.evidenceClassification === 'strong' && anom.date) {
    const monthLabel = new Date(anom.date + '-02').toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    bullets.push(`Evidence temporally aligned with ${monthLabel}`);
  }
  if (typeof anom.evidenceScore === 'number') {
    bullets.push(`Evidence score: ${anom.evidenceScore.toFixed(2)} (${anom.evidenceClassification || 'n/a'})`);
  }
  return bullets.length ? bullets : ['No corroborating unstructured evidence found.'];
}

function renderDetectionPanel(anomalyKey) {
  const anom = ANOMALY_DATASET[anomalyKey];
  const badgeEl = document.getElementById('detectionTypeBadge');
  const bulletsEl = document.getElementById('detectionEvidenceBullets');
  if (!anom || !badgeEl || !bulletsEl) return;

  const meta = _DETECTION_LABELS[anom.detectionType] || _DETECTION_LABELS.STATISTICAL;
  badgeEl.textContent = meta.text;
  badgeEl.className = `detection-type-badge ${meta.cls}`;
  bulletsEl.innerHTML = _buildDetectionBullets(anom).map(b => `<li>${b}</li>`).join('');
}

function toggleEvidenceAccordion(index) {
  const item = document.getElementById(`evCard-${index}`);
  if (!item) return;

  const isExpanded = item.classList.contains('expanded');
  // Close any open item
  document.querySelectorAll('.evidence-card-item').forEach(el => el.classList.remove('expanded'));

  if (!isExpanded) {
    item.classList.add('expanded');
  }
}


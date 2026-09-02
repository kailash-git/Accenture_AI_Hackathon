/* ==========================================================================
   LIVE ANOMALY STREAM
   --------------------------------------------------------------------------
   Replaces the one-shot anomaly-list load with a ticker: poll
   /api/anomalies/stream a few anomalies at a time and prepend each to the
   left deck, newest on top, like a stock feed. Every item returned by that
   endpoint has already been through the SAME server-side pipeline as every
   other anomaly route -- _row_to_anomaly_dict -> _apply_persona ->
   _apply_entitlements -- so role masking is identical here; the client only
   renders what it is handed. Falls back to the static ANOMALY_DATASET when
   the backend is unreachable (handled in js/app.js).
   ========================================================================== */

const STREAM_STATE = {
  after: 0,
  total: null,
  done: false,
  running: false,
  timer: null,
  firstArrived: false,
  batchSize: 3,
  liveMs: 2000,   // cadence while anomalies are still arriving
  idleMs: 30000,  // heartbeat once the backlog is drained
};

function _streamDeck() {
  return document.querySelector('.sidebar-deck-scroll');
}

function _streamRole() {
  return (typeof APP_STATE !== 'undefined' && APP_STATE.activeRole) || 'vp_sales';
}

/* "LIVE 12/57" while streaming, "57 loaded" once drained. */
function _streamRenderLiveLabel() {
  const el = document.querySelector('.deck-section-header .deck-section-title');
  if (!el) return;
  const deck = _streamDeck();
  const n = deck ? deck.querySelectorAll('.scenario-card').length : 0;
  const total = STREAM_STATE.total;
  if (STREAM_STATE.done) {
    el.innerHTML = `Anomaly Scenarios <span class="deck-live-badge is-done">${n} loaded</span>`;
  } else {
    const of = total ? `${n}/${total}` : `${n}`;
    el.innerHTML = `Anomaly Scenarios <span class="deck-live-badge"><span class="deck-live-dot"></span>LIVE ${of}</span>`;
  }
}

/* Build one card and slide it in at the top of the deck (just under the
   header / review-period row). Replaces the card in place if the key is
   already on screen (e.g. after a role switch re-stream). */
function _streamFilterActive() {
  const period = (typeof APP_STATE !== 'undefined' && APP_STATE.activeReviewPeriod) || 'all';
  const search = document.getElementById('sidebarSearchInput');
  return period !== 'all' || (search && search.value.trim() !== '');
}

function _streamPrependCard(key, anom) {
  const deck = _streamDeck();
  if (!deck || typeof scenarioCardHtml !== 'function') return;

  // While the user has a search/period filter applied, defer to the filtered
  // one-shot renderer so arrivals don't bypass the active filter.
  if (_streamFilterActive() && typeof renderSidebarCards === 'function') {
    renderSidebarCards();
    return;
  }

  const tmp = document.createElement('div');
  tmp.innerHTML = scenarioCardHtml(key, anom).trim();
  const card = tmp.firstElementChild;
  if (!card) return;

  const sel = (window.CSS && CSS.escape) ? CSS.escape(key) : key;
  const prev = deck.querySelector(`.scenario-card[data-scenario="${sel}"]`);
  if (prev) {
    prev.replaceWith(card);
    return;
  }

  card.classList.add('sc-arriving');
  const anchor = deck.querySelector('.calendar-filter-container')
    || deck.querySelector('.deck-section-header');
  if (anchor && anchor.parentElement === deck) {
    anchor.insertAdjacentElement('afterend', card);
  } else {
    deck.insertBefore(card, deck.firstChild);
  }
  // next frame: drop the offset so the CSS transition plays
  requestAnimationFrame(() => requestAnimationFrame(() => {
    card.classList.remove('sc-arriving');
    card.classList.add('sc-fresh');
    setTimeout(() => card.classList.remove('sc-fresh'), 2400);
  }));
}

function _streamIngest(raw) {
  if (!raw || !raw.id || typeof apiClient === 'undefined') return;
  const key = raw.scenario_key
    || `gen-${raw.item_id}-${(raw.period_start || '').substring(0, 7)}-${raw.state_id}`;
  const normalized = apiClient.normalizeAnomalyForUI(raw, ANOMALY_DATASET[key]);
  normalized.title = friendlyScenarioTitle(raw.scenario_key, raw.kpi_name, raw.direction);
  normalized.badgeText = `${(raw.status || 'active').replace(/^./, c => c.toUpperCase())} · `
    + `${Math.round(raw.confidence)}% Conf${raw.abstained ? ' (Abstained)' : ''}`;
  normalized._fromStream = true;
  ANOMALY_DATASET[key] = normalized;
  _streamPrependCard(key, normalized);

  if (!STREAM_STATE.firstArrived) {
    STREAM_STATE.firstArrived = true;
    if (typeof selectScenario === 'function') selectScenario(key);
  }
}

async function _streamTick() {
  if (!STREAM_STATE.running) return;

  let payload = null;
  try {
    const url = `${API_CONFIG.baseUrl}/api/anomalies/stream`
      + `?after=${STREAM_STATE.after}&limit=${STREAM_STATE.batchSize}`;
    const res = await fetch(url, { headers: { 'X-User-Role': _streamRole() } });
    payload = await res.json();
  } catch (err) {
    console.warn('anomaly stream poll failed:', err);
    STREAM_STATE.timer = setTimeout(_streamTick, STREAM_STATE.idleMs);
    return;
  }

  const items = (payload && Array.isArray(payload.items)) ? payload.items : [];
  if (payload && typeof payload.total === 'number') STREAM_STATE.total = payload.total;

  items.forEach(_streamIngest);

  if (items.length) {
    if (typeof populateCalendarPeriodSelect === 'function') populateCalendarPeriodSelect();
  }

  if (payload && typeof payload.nextAfter === 'number') STREAM_STATE.after = payload.nextAfter;
  STREAM_STATE.done = Boolean(payload && payload.done);
  _streamRenderLiveLabel();

  STREAM_STATE.timer = setTimeout(
    _streamTick,
    STREAM_STATE.done ? STREAM_STATE.idleMs : STREAM_STATE.liveMs
  );
}

/* Kick off (or resume) the ticker. Clears any static placeholder cards so
   the deck fills purely from the stream. */
function startAnomalyStream() {
  if (STREAM_STATE.running) return;
  STREAM_STATE.running = true;

  const deck = _streamDeck();
  if (deck) deck.querySelectorAll('.scenario-card').forEach(el => el.remove());

  _streamRenderLiveLabel();
  _streamTick();
}

/* Role switch: re-stream from the top under the new role's masking. Keeps
   ANOMALY_DATASET entries so re-streamed rows overwrite them with correctly
   masked data; the deck DOM is rebuilt by startAnomalyStream(). */
function resetAnomalyStream() {
  if (STREAM_STATE.timer) { clearTimeout(STREAM_STATE.timer); STREAM_STATE.timer = null; }
  STREAM_STATE.after = 0;
  STREAM_STATE.total = null;
  STREAM_STATE.done = false;
  // keep the current selection if there is one -- don't let the first
  // re-streamed card hijack the view mid-read
  STREAM_STATE.firstArrived = Boolean(
    typeof APP_STATE !== 'undefined' && APP_STATE.activeAnomalyKey
  );
  STREAM_STATE.running = false;
  startAnomalyStream();
}

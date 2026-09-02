/* ==========================================================================
   CONVERSATIONAL ASSISTANT (opt-in, grounded Q&A over the active anomaly)
   --------------------------------------------------------------------------
   The dashboard's KPI narratives are generated offline with zero live LLM
   calls. This panel is a SEPARATE, opt-in "ask about this movement" feature.
   It POSTs to /api/chat, which grounds every answer in the SAME role-masked
   evidence the rest of the API enforces (see api_server.py build_chat_response)
   and instructs the model to abstain when the evidence doesn't support an
   answer. Model replies are rendered as text only (textContent) -- never as
   HTML.
   ========================================================================== */

const CHAT_STATE = { open: false, sending: false, lastMessage: '' };

function setChatPanelOpen(open) {
  CHAT_STATE.open = open;
  const panel = document.getElementById('chatPanel');
  const launcher = document.getElementById('chatLauncher');
  if (panel) panel.classList.toggle('is-open', open);
  if (launcher) {
    launcher.setAttribute('aria-expanded', String(open));
    launcher.classList.toggle('is-hidden', open);  // hide the pill while the sidebar is open
  }
  if (open) {
    const input = document.getElementById('chatInput');
    if (input) setTimeout(() => input.focus(), 50);
  }
}

function toggleChatPanel() {
  setChatPanelOpen(!CHAT_STATE.open);
}

function closeChatPanel() {
  setChatPanelOpen(false);
}

function _chatScrollToBottom() {
  const log = document.getElementById('chatLog');
  if (log) log.scrollTop = log.scrollHeight;
}

/* Appends a message row. `sender` is 'user' or 'assistant'. Returns
   { row, bubble } so a pending placeholder can be updated in place. */
function _appendChatMessage(sender, text, opts) {
  opts = opts || {};
  const log = document.getElementById('chatLog');
  if (!log) return null;

  const row = document.createElement('div');
  row.className = 'chat-msg chat-msg-' + sender + (opts.notice ? ' chat-msg-notice' : '');

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  bubble.textContent = text;
  row.appendChild(bubble);

  if (opts.meta) {
    const meta = document.createElement('div');
    meta.className = 'chat-msg-meta';
    meta.textContent = opts.meta;
    row.appendChild(meta);
  }

  log.appendChild(row);
  _chatScrollToBottom();
  return { row: row, bubble: bubble };
}

function _chatActiveRole() {
  return (typeof APP_STATE !== 'undefined' && APP_STATE.activeRole) || 'vp_sales';
}

/* Renders the "which movement did you mean?" choices as buttons. Each click
   re-asks the SAME question, pinned to one anomaly (focus:true). */
function _appendChatCandidates(candidates) {
  const log = document.getElementById('chatLog');
  if (!log || !Array.isArray(candidates) || !candidates.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'chat-candidates';
  candidates.forEach((c) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chat-candidate-btn';
    btn.textContent = c.label || c.key;
    btn.addEventListener('click', () => {
      wrap.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      sendChatMessage({ text: CHAT_STATE.lastMessage, focusKey: c.key });
    });
    wrap.appendChild(btn);
  });
  log.appendChild(wrap);
  _chatScrollToBottom();
}

/* Small footnote: what was deterministic vs. what the model did. */
function _appendChatProcessing(row, processing) {
  if (!row || !processing) return;
  const det = (processing.deterministic || []).length;
  const usedLlm = (processing.llm || []).some((s) => !/not called/i.test(s));
  const el = document.createElement('div');
  el.className = 'chat-processing';
  el.textContent = usedLlm
    ? det + ' deterministic step' + (det === 1 ? '' : 's') + ' (parse · SQL select · role masking · PVM) → model worded the answer only'
    : det + ' deterministic step' + (det === 1 ? '' : 's') + ' · no model call this turn';
  row.appendChild(el);
}

async function sendChatMessage(opts) {
  opts = opts || {};
  const input = document.getElementById('chatInput');
  const sendBtn = document.getElementById('chatSendBtn');
  if (!input || CHAT_STATE.sending) return;

  const isFollowUp = typeof opts.text === 'string';
  const message = (isFollowUp ? opts.text : input.value).trim();
  if (!message) return;

  if (!isFollowUp) {
    input.value = '';
    _appendChatMessage('user', message);
  }
  CHAT_STATE.lastMessage = message;

  if (typeof API_CONFIG === 'undefined' || !API_CONFIG.isBackendConnected) {
    _appendChatMessage(
      'assistant',
      'The assistant needs the live backend. Start api_server.py, wait for "Live DB Synced", then ask again.',
      { notice: true }
    );
    return;
  }

  CHAT_STATE.sending = true;
  if (sendBtn) sendBtn.disabled = true;
  const pending = _appendChatMessage('assistant', 'Thinking…', { notice: true });

  try {
    const res = await fetch(API_CONFIG.baseUrl + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-User-Role': _chatActiveRole() },
      body: JSON.stringify({
        message: message,
        role: _chatActiveRole(),
        anomaly_key: opts.focusKey
          || (typeof APP_STATE !== 'undefined' ? APP_STATE.activeAnomalyKey : null),
        focus: Boolean(opts.focusKey)
      }),
      signal: AbortSignal.timeout(25000)
    });

    const data = await res.json();
    const reply = data.reply || 'No response.';

    const metaLines = [];
    if (data.needs_clarification) {
      // handled below by rendering choice buttons
    } else if (data.grounded && data.anomaly) {
      let line = 'Grounded in: ' + data.anomaly;
      if (data.telemetry) {
        const toks = (data.telemetry.tokens_in || 0) + (data.telemetry.tokens_out || 0);
        line += ' · ' + toks + ' tok · ' + data.telemetry.latency_ms + ' ms';
      }
      metaLines.push(line);
      if (data.abstained) metaLines.push('Engine abstained on this movement — answer reflects low confidence');
    } else if (data.error) {
      metaLines.push('Not grounded — evidence or assistant unavailable');
    }
    if (data.resolution) metaLines.unshift(data.resolution);

    if (pending) {
      const notice = !data.grounded;
      pending.row.className = 'chat-msg chat-msg-assistant' + (notice ? ' chat-msg-notice' : '');
      pending.bubble.textContent = reply;
      metaLines.forEach((text) => {
        const m = document.createElement('div');
        m.className = 'chat-msg-meta';
        m.textContent = text;
        pending.row.appendChild(m);
      });
      _appendChatProcessing(pending.row, data.processing);
    }
    if (data.needs_clarification) _appendChatCandidates(data.candidates);
    _chatScrollToBottom();
  } catch (err) {
    if (pending) {
      pending.row.className = 'chat-msg chat-msg-assistant chat-msg-notice';
      pending.bubble.textContent = (err && err.name === 'TimeoutError')
        ? 'The assistant took too long to respond. Please try again.'
        : 'Could not reach the assistant. Check that api_server.py is running.';
    }
  } finally {
    CHAT_STATE.sending = false;
    if (sendBtn) sendBtn.disabled = false;
    if (input) input.focus();
  }
}

function _handleChatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  } else if (e.key === 'Escape') {
    closeChatPanel();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const launcher = document.getElementById('chatLauncher');
  const closeBtn = document.getElementById('chatCloseBtn');
  const sendBtn = document.getElementById('chatSendBtn');
  const input = document.getElementById('chatInput');

  if (launcher) launcher.addEventListener('click', toggleChatPanel);
  if (closeBtn) closeBtn.addEventListener('click', closeChatPanel);
  if (sendBtn) sendBtn.addEventListener('click', () => sendChatMessage());
  if (input) input.addEventListener('keydown', _handleChatKeydown);
});

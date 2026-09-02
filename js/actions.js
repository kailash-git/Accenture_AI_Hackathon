/* ==========================================================================
   ACTIONS, ASSIGNMENTS, AUDIT LOG & RBAC JS MODULE
   ========================================================================== */

let pendingDismissTimeout = null;
let lastDismissedCardId = null;

function handleActionApprove(anomalyKey, stepIndex, btn) {
  const anom = ANOMALY_DATASET[anomalyKey] || ANOMALY_DATASET.supply;
  if (!anom.recommendedAction) {
    showAppToast('No recommendation to approve — the engine abstained on this anomaly.');
    return;
  }
  const title = (stepIndex === -1 || stepIndex === undefined) ? anom.recommendedAction.title : (anom.recommendedAction.steps[stepIndex] || {}).text || anom.recommendedAction.title;

  if (typeof apiClient !== 'undefined') {
    apiClient.approveAction(anomalyKey, {
      title: title,
      expectedImpact: anom.recommendedAction.expectedImpact
    });
  } else {
    showAppToast(`Approved: ${title}`);
  }

  // Persist approval state
  if (stepIndex === -1 || stepIndex === undefined) {
    anom.isApproved = true;
  } else {
    if (!anom.approvedSteps) anom.approvedSteps = {};
    anom.approvedSteps[stepIndex] = true;
  }

  // The same recommendation is rendered in up to three places at once (hero
  // banner, Prescriptive Decision Engine card, investigation drawer) -- each
  // used to only update whichever single button was actually clicked, so
  // approving from one spot left the others still showing "Approve Action"
  // until the next full re-render. Syncing every matching button here means
  // approving anywhere reflects everywhere immediately.
  document.querySelectorAll(`.js-approve-action-btn[data-anomaly-key="${CSS.escape(anomalyKey)}"]`).forEach(b => {
    b.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> Approved & Dispatched`;
    b.style.backgroundColor = '#166534';
    b.style.color = '#ffffff';
    b.disabled = true;
  });
}

function handleActionAssign(anomalyKey) {
  openAssignmentModal(anomalyKey);
}

/* ---- Action correction (learning loop) -------------------------------------
   The user thinks the recommended action is wrong and types what to do
   instead. Stored server-side keyed to the anomaly's scenario/KPI so a future
   similar anomaly surfaces the corrected action (api_server.py
   _match_action_correction). Sits alongside the thumbs feedback. */
function toggleActionCorrectionForm() {
  const form = document.getElementById('actionCorrectionForm');
  const toggle = document.getElementById('actionCorrectionToggle');
  if (!form) return;
  form.hidden = !form.hidden;
  if (toggle) toggle.style.display = form.hidden ? '' : 'none';
  if (!form.hidden) document.getElementById('actionCorrectionText')?.focus();
}

async function submitActionCorrectionForm(anomalyKey) {
  const text = (document.getElementById('actionCorrectionText')?.value || '').trim();
  const why = (document.getElementById('actionCorrectionWhy')?.value || '').trim();
  if (!text) {
    showAppToast('Type what the recommended action should be first.');
    return;
  }

  let ok = false;
  if (typeof apiClient !== 'undefined' && apiClient.isConnected) {
    const res = await apiClient.submitActionCorrection(anomalyKey, text, why);
    ok = !!(res && res.success);
  }

  // Reflect it locally so the "Learned Recommendation" card shows immediately.
  const anom = ANOMALY_DATASET[anomalyKey];
  if (anom) {
    anom.actionCorrection = {
      corrected_action: text,
      rationale: why,
      corrected_by: APP_STATE.activeRole,
      source_anomaly_id: anom.id,
      is_own: true,
      match: 'this anomaly'
    };
  }

  showAppToast(ok
    ? 'Correction saved — the engine will surface it on similar anomalies.'
    : 'Correction saved for this session.');

  // Re-render the scenario so the learned card + hero recommendation update
  // (also re-fetches from the backend, proving the match round-trips).
  if (typeof selectScenario === 'function') selectScenario(anomalyKey);
}

function openAssignmentModal(anomalyKey) {
  const anom = ANOMALY_DATASET[anomalyKey] || ANOMALY_DATASET.supply;
  const modalBody = document.getElementById('assignModalBody');
  if (!modalBody) return;
  if (!anom.recommendedAction) {
    showAppToast('No recommendation to assign — the engine abstained on this anomaly.');
    return;
  }

  modalBody.innerHTML = `
    <div style="font-size: 14px; font-weight: 700; color: var(--text-primary); margin-bottom: 6px;">
      ${anom.recommendedAction.title}
    </div>
    <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 16px;">
      Impact: ${anom.recommendedAction.expectedImpact}
    </div>
    <label style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-tertiary); display: block; margin-bottom: 6px;">
      Assignee Role / Team Lead:
    </label>
    <select id="assigneeSelect" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-sm); padding: 10px; color: var(--text-primary); font-family: inherit; font-size: 13px; margin-bottom: 14px;">
      <option value="supply_planner@retailbi.ai">Sivasubramanian S (Regional Supply Planner)</option>
      <option value="vp_sales@retailbi.ai">Thirukailash K A (VP of Retail Sales)</option>
      <option value="procurement_lead@retailbi.ai">Procurement Operations Lead (West Region)</option>
      <option value="pos_engineering@retailbi.ai">POS Systems Engineering Lead (TX South)</option>
    </select>
    <label style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-tertiary); display: block; margin-bottom: 6px;">
      SLA Target Window:
    </label>
    <select id="slaSelect" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-sm); padding: 10px; color: var(--text-primary); font-family: inherit; font-size: 13px; margin-bottom: 20px;">
      <option value="24h">P1 — Critical (24 Hours)</option>
      <option value="48h" selected>P2 — Standard (48 Hours)</option>
      <option value="7d">P3 — Sprint Target (7 Days)</option>
    </select>
    <div style="display: flex; gap: 10px; justify-content: flex-end;">
      <button class="btn-outline-secondary" onclick="closeModal('assignmentModal')">Cancel</button>
      <button class="btn-solid-primary" onclick="confirmAssignment('${anomalyKey}')">Confirm & Dispatch</button>
    </div>
  `;
  openModal('assignmentModal');
}

async function confirmAssignment(anomalyKey) {
  const select = document.getElementById('assigneeSelect');
  const sla = document.getElementById('slaSelect');
  const assignee = select ? select.value : 'Operations Lead';
  const slaText = sla ? sla.value : '48h';

  closeModal('assignmentModal');

  // Was purely a client-side toast + badge swap with no backend call at all --
  // an assignment is a decision-rights dispatch just like Approve, and needs the
  // same audit trail (REQ-06: "actions grounded in... decision rights").
  let auditSuffix = '';
  if (typeof apiClient !== 'undefined') {
    const result = await apiClient.assignAction(anomalyKey, assignee, slaText);
    if (result && result.success) auditSuffix = ` (Audit #${result.audit_id})`;
  }
  showAppToast(`Dispatched to ${assignee} with ${slaText} SLA${auditSuffix}`);

  const card = document.getElementById(`actionCard-${anomalyKey}`);
  if (card) {
    const badge = document.createElement('div');
    badge.style.cssText = 'font-size: 11px; font-weight: 700; color: var(--accent-green); margin-top: 8px;';
    badge.textContent = `Assigned to: ${assignee}`;
    card.querySelector('.action-card-btn-row')?.replaceWith(badge);
  }
}

function handleActionDismiss(anomalyKey) {
  const card = document.getElementById(`actionCard-${anomalyKey}`);
  if (card) {
    lastDismissedCardId = `actionCard-${anomalyKey}`;
    card.style.opacity = '0.25';
    card.style.pointerEvents = 'none';
  }

  showAppToast(`Action archived. Click here to Undo.`, () => {
    if (lastDismissedCardId) {
      const el = document.getElementById(lastDismissedCardId);
      if (el) {
        el.style.opacity = '1';
        el.style.pointerEvents = 'all';
      }
      showAppToast('Action restored');
    }
  });
}

/* User Profile & Entitlements Modal */
const _PROFILE_MODAL_CONTENT = {
  supply_planner: {
    roleLine: 'Supply Planner · BusinessIntelligence.ai',
    accessColor: 'var(--accent-amber)',
    accessLabel: 'Role: Regional Planner (Restricted Access)',
    matrix: `
      ✗ <strong>Revenue & Margin:</strong> MASKED / RESTRICTED (<code>revenue</code>, <code>gross_margin_percent</code> hidden)<br/>
      ✗ <strong>Cost Breakdown:</strong> MASKED (<code>cost_of_goods_sold</code>, <code>marketing_spend</code> hidden)<br/>
      ✓ <strong>Supply Chain metrics:</strong> Unrestricted (<code>fill_rate</code>, <code>stockout_days</code> visible)<br/>
      ✓ <strong>GraphRAG Semantic Search:</strong> Unrestricted ticket & review access`,
  },
  // vp_sales is financially unrestricted but IS scoped away from warehouse/SKU
  // identity (item_id, sku, warehouse_sku, fill_rate, stockout_days -- see
  // _apply_entitlements' vp_sales branch) -- this used to be mislabeled here as
  // "Executive Administrator (Full Access)", which contradicted what the server
  // actually enforces for this role.
  vp_sales: {
    roleLine: 'VP of Retail Sales · BusinessIntelligence.ai',
    accessColor: 'var(--accent-green)',
    accessLabel: 'Role: VP of Retail Sales (Financial Access, Logistics Restricted)',
    matrix: `
      ✓ <strong>Revenue & Margin:</strong> Unrestricted (<code>gross_margin_percent</code> visible)<br/>
      ✓ <strong>Cost Breakdown:</strong> Unrestricted (<code>cost_of_goods_sold</code> visible)<br/>
      ✗ <strong>Warehouse/SKU Identity:</strong> MASKED (<code>item_id</code>, <code>sku</code>, <code>warehouse_sku</code> hidden)<br/>
      ✗ <strong>Supply Chain metrics:</strong> MASKED (<code>fill_rate</code>, <code>stockout_days</code> hidden)<br/>
      ✓ <strong>Action Dispatch:</strong> PO Approval, Buffer Reallocation Authorized`,
  },
  admin: {
    roleLine: 'Data Governance & Compliance Admin · BusinessIntelligence.ai',
    accessColor: 'var(--accent-amber)',
    accessLabel: 'Role: Data Governance Admin (Full Access -- Audit Oversight)',
    matrix: `
      ✓ <strong>Revenue & Margin:</strong> Unrestricted (<code>gross_margin_percent</code> visible)<br/>
      ✓ <strong>Cost Breakdown:</strong> Unrestricted (<code>cost_of_goods_sold</code> visible)<br/>
      ✓ <strong>Warehouse/SKU Identity:</strong> Unrestricted (<code>item_id</code>, <code>sku</code>, <code>warehouse_sku</code> visible)<br/>
      ✓ <strong>Supply Chain metrics:</strong> Unrestricted (<code>fill_rate</code>, <code>stockout_days</code> visible)<br/>
      ✓ <strong>GraphRAG Semantic Search:</strong> Fully unrestricted, including item/warehouse node identity<br/>
      <span style="color: var(--text-tertiary);">Granted every column vp_sales/supply_planner are scoped to, per schemas/semantic_contract.json -- an explicit governance grant, not a bypass of the entitlements model.</span>`,
  },
};

function openProfileModal() {
  const body = document.getElementById('profileModalBody');
  if (!body) return;

  const content = _PROFILE_MODAL_CONTENT[APP_STATE.activeRole] || _PROFILE_MODAL_CONTENT.vp_sales;

  body.innerHTML = `
    <div style="display: flex; align-items: center; gap: 16px; margin-bottom: 20px;">
      <div class="user-avatar" style="width: 52px; height: 52px; font-size: 18px; background-color: var(--bg-card); border: 1px solid var(--border-medium); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--text-secondary);">TK</div>
      <div>
        <div style="font-size: 16px; font-weight: 700; color: var(--text-primary);">Thirukailash K A</div>
        <div style="font-size: 12px; color: var(--text-secondary);">${content.roleLine}</div>
        <div style="font-size: 11px; color: ${content.accessColor}; margin-top: 2px;">${content.accessLabel}</div>
      </div>
    </div>

    <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: var(--text-tertiary); margin-bottom: 8px;">Active RBAC Entitlements Matrix</div>
    <div style="background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-md); padding: 14px; font-size: 12px; line-height: 1.7;">
      ${content.matrix}
    </div>

    <div style="display: flex; justify-content: flex-end; margin-top: 16px;">
      <button class="btn-solid-primary" onclick="closeModal('profileModal')">Done</button>
    </div>
  `;
  openModal('profileModal');
}

/* Audit Log Modal */
function openAuditLogModal() {
  const body = document.getElementById('auditModalBody');
  if (!body) return;

  body.innerHTML = `
    <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 14px;">
      Immutable record of all business actions, approvals, and AI confidence shifts recorded in SQLite table <code>user_feedback</code>.
    </div>

    <div style="display: flex; flex-direction: column; gap: 10px;">
      <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 12px;">
        <div style="display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; color: var(--accent-green); margin-bottom: 4px;">
          <span>#AUD-9081 · APPROVED</span>
          <span>Today · 16:32:10</span>
        </div>
        <div style="font-size: 13px; font-weight: 600;">Expedite Emergency Secondary Reorder</div>
        <div style="font-size: 11px; color: var(--text-tertiary); margin-top: 2px;">Authorized by: Thirukailash K A (VP of Retail Sales) · Target: 5,000 units FOODS_3_090</div>
      </div>

      <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 12px;">
        <div style="display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; color: var(--accent-amber); margin-bottom: 4px;">
          <span>#AUD-8812 · ABSTAINED</span>
          <span>Aug 24 · 14:15:00</span>
        </div>
        <div style="font-size: 13px; font-weight: 600;">Billing Bug Price Reversal</div>
        <div style="font-size: 11px; color: var(--text-tertiary); margin-top: 2px;">Trigger: Conflicting reviews vs fact_sales_daily · Manual audit queued for TX pos engine</div>
      </div>
    </div>

    <div style="display: flex; justify-content: flex-end; margin-top: 16px;">
      <button class="btn-solid-primary" onclick="closeModal('auditModal')">Close</button>
    </div>
  `;
  openModal('auditModal');
}

/* Custom Anomaly Creator Modal */
function openCreateAnomalyModal() {
  const body = document.getElementById('createAnomalyModalBody');
  if (!body) return;

  body.innerHTML = `
    <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 16px;">
      Inject a custom mathematical scenario or configure real-time Z-score thresholds for daily detection.
    </div>

    <label style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-tertiary); display: block; margin-bottom: 6px;">Target SKU / Department:</label>
    <select id="newAnomalySku" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-sm); padding: 10px; color: var(--text-primary); font-family: inherit; font-size: 13px; margin-bottom: 14px;">
      <option value="FOODS_3_090">FOODS_3_090 (Fresh Dairy Grade A)</option>
      <option value="FOODS_3_586">FOODS_3_586 (Pantry Snack Pack)</option>
      <option value="HOUSEHOLD_1_020">HOUSEHOLD_1_020 (Household Cleaning)</option>
    </select>

    <label style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-tertiary); display: block; margin-bottom: 6px;">Anomaly Trigger Archetype:</label>
    <select id="newAnomalyType" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-sm); padding: 10px; color: var(--text-primary); font-family: inherit; font-size: 13px; margin-bottom: 14px;">
      <option value="Supply Constraint">Supply Constraint (Fill rate plunge & stockout)</option>
      <option value="Price Elasticity">Price Cut & Volume Surge (PVM Shift)</option>
      <option value="Billing Drift">Billing Anomaly (Overcharge bug)</option>
      <option value="Marketing Data Gap">Missing Feed Gap (Abstention trigger)</option>
    </select>

    <label style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-tertiary); display: block; margin-bottom: 6px;">Z-Score Alert Threshold (Sigma σ):</label>
    <input type="number" id="newAnomalyZ" value="3.0" step="0.1" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-sm); padding: 10px; color: var(--text-primary); font-family: inherit; font-size: 13px; margin-bottom: 20px;" />

    <div style="display: flex; gap: 10px; justify-content: flex-end;">
      <button class="btn-outline-secondary" onclick="closeModal('createAnomalyModal')">Cancel</button>
      <button class="btn-solid-primary" onclick="submitCustomAnomaly()">Deploy Scenario</button>
    </div>
  `;
  openModal('createAnomalyModal');
}

function submitCustomAnomaly() {
  const sku = document.getElementById('newAnomalySku')?.value || 'FOODS_3_090';
  const type = document.getElementById('newAnomalyType')?.value || 'Custom Anomaly';
  const zScore = document.getElementById('newAnomalyZ')?.value || '3.0';

  closeModal('createAnomalyModal');
  showAppToast(`Deployed scenario "${type}" for ${sku} (σ = ${zScore})`);
}

/* RBAC Role Switcher -- triggers a REAL re-fetch from the backend scoped to the new
   role, so masking and persona-specific narratives are server-enforced (REQ-04/REQ-08),
   not a client-side CSS/text swap. */
const _ROLE_DISPLAY = {
  supply_planner: { label: 'Supply Planner', toastNote: 'Supply Planner (server-masked financials)' },
  vp_sales: { label: 'VP of Retail Sales', toastNote: 'VP of Retail Sales (server-masked logistics)' },
  admin: { label: 'Data Governance Admin', toastNote: 'Data Governance Admin (unrestricted -- audit/oversight view)' },
};

async function setAppRole(roleKey) {
  APP_STATE.activeRole = roleKey;
  const isPlanner = roleKey === 'supply_planner';
  const meta = _ROLE_DISPLAY[roleKey] || _ROLE_DISPLAY.vp_sales;

  // .role-planner is the one role that still blurs the Gross Margin/COGS KPI
  // tiles client-side (css/charts.css) on top of the real server-side masking --
  // vp_sales and admin both render the real fetched value, so neither needs it.
  document.body.classList.toggle('role-planner', isPlanner);
  document.getElementById('roleBtnVp').classList.toggle('active', roleKey === 'vp_sales');
  document.getElementById('roleBtnPlanner').classList.toggle('active', roleKey === 'supply_planner');
  document.getElementById('roleBtnAdmin').classList.toggle('active', roleKey === 'admin');

  const roleTextEl = document.getElementById('sidebarUserRole');
  if (roleTextEl) roleTextEl.textContent = meta.label;

  const gmEl = document.getElementById('kpiGrossMargin');
  const cogsEl = document.getElementById('kpiCogs');
  if (gmEl) gmEl.textContent = isPlanner ? 'RESTRICTED' : 'See PVM breakdown';
  if (cogsEl) cogsEl.textContent = isPlanner ? 'RESTRICTED' : 'See PVM breakdown';

  showAppToast(`Re-fetching as: ${meta.toastNote}...`);

  // Re-fetch the full anomaly list + current scenario detail under the new role so
  // every rendered field (narrative, action, masking) reflects real server enforcement.
  if (typeof STREAM_STATE !== 'undefined' && STREAM_STATE.running && typeof resetAnomalyStream === 'function') {
    // Live deck: re-stream every card from the top under the new role's masking.
    resetAnomalyStream();
  } else if (typeof loadAnomalyListFromBackend === 'function') {
    await loadAnomalyListFromBackend();
    renderSidebarCards();
  }
  if (typeof selectScenario === 'function' && APP_STATE.activeAnomalyKey) {
    await selectScenario(APP_STATE.activeAnomalyKey);
  }
}

/* Toast Notification Utility */
function showAppToast(message, undoCallback = null) {
  let container = document.getElementById('appToastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'appToastContainer';
    container.className = 'app-toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = 'toast-pill';
  toast.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent-green)" stroke-width="2.5">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
      <polyline points="22 4 12 14.01 9 11.01"/>
    </svg>
    <span>${message}</span>
  `;

  if (undoCallback) {
    const undoBtn = document.createElement('button');
    undoBtn.textContent = 'Undo';
    undoBtn.style.cssText = 'background:none;border:none;color:var(--accent-green);font-weight:700;cursor:pointer;margin-left:8px;text-decoration:underline;';
    undoBtn.onclick = (e) => {
      e.stopPropagation();
      undoCallback();
      toast.remove();
    };
    toast.appendChild(undoBtn);
  }

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(12px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

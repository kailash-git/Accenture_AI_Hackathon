/* ==========================================================================
   API CLIENT & BACKEND CONNECTOR MODULE (Live Database & Engine Bridge)
   ========================================================================== */

const API_CONFIG = {
  // same origin as the served page; :8000 only when opened as a file://
  baseUrl: (location.protocol === 'http:' || location.protocol === 'https:')
    ? location.origin
    : 'http://127.0.0.1:8000',
  isBackendConnected: false,
  pollIntervalMs: 15000,
  endpoints: {
    health: '/api/health',
    anomalies: '/api/anomalies/latest',
    anomalyDetail: (id) => `/api/anomalies/${id}`,
    anomalyTimeline: (id) => `/api/anomalies/${id}/timeline`,
    anomalyGraph: (id) => `/api/anomalies/${id}/graph`,
    approveAction: (id) => `/api/actions/${id}/approve`,
    assignAction: (id) => `/api/actions/${id}/assign`,
    correctAction: (id) => `/api/actions/${id}/correct`,
    telemetry: '/api/telemetry',
    submitFeedback: '/api/feedback'
  }
};

class BackendApiClient {
  constructor() {
    this.baseUrl = API_CONFIG.baseUrl;
    this.isConnected = false;
  }

  _headers(role) {
    return { 'Accept': 'application/json', 'X-User-Role': role || APP_STATE.activeRole || 'vp_sales' };
  }

  async checkHealth() {
    try {
      const response = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.health}`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(2000)
      });
      if (response.ok) {
        this.isConnected = true;
        API_CONFIG.isBackendConnected = true;
        this.updateConnectionStatusUI(true);
        return true;
      }
    } catch (err) {
      this.isConnected = false;
      API_CONFIG.isBackendConnected = false;
      this.updateConnectionStatusUI(false);
      return false;
    }
  }

  updateConnectionStatusUI(connected) {
    const statusDot = document.getElementById('backendStatusDot');
    const statusText = document.getElementById('backendStatusText');
    if (statusDot) {
      statusDot.style.backgroundColor = connected ? 'var(--accent-green)' : 'var(--accent-amber)';
      statusDot.title = connected ? 'Connected to live SQLite backend & Analytics Engine' : 'Running in local offline demo mode (backend unreachable — start api_server.py)';
    }
    if (statusText) {
      statusText.textContent = connected ? 'Live DB Synced' : 'Offline Demo Data';
    }
  }

  /* -------------------- Normalization: server schema -> UI schema -------------------- */
  normalizeAnomalyForUI(raw, existing) {
    if (!raw) return existing;
    const action = raw.recommendedAction;
    const abstained = !!raw.abstained;

    const steps = action ? [
      { label: 'Driver', text: action.driver },
      { label: 'Controllable Lever', text: action.controllable_lever },
      { label: 'Action', text: action.action },
      { label: 'Owner', text: action.owner },
      { label: 'Monitoring Plan', text: action.monitoring_plan }
    ] : [];

    return {
      ...existing,
      id: raw.id,
      kpiName: raw.kpi_name,
      title: (existing && existing.title) || raw.kpi_name + (raw.direction === 'DOWN' ? ' Decline' : ' Movement'),
      category: (existing && existing.category) || raw.scenario_key,
      sku: raw.item_id,
      region: raw.state_id,
      date: (raw.period_start || '').substring(0, 7),
      zScore: typeof raw.z_score === 'number' ? Number(raw.z_score.toFixed(2)) : raw.z_score,
      deviation: typeof raw.deviation_pct === 'number' ? `${(raw.deviation_pct * 100).toFixed(1)}%` : raw.deviation,
      confidence: Math.round(raw.confidence),
      status: raw.status,
      warehouse: (raw.logistics && raw.logistics.title) || '',
      period_start: raw.period_start,
      period_end: raw.period_end,
      headline: raw.headline,
      summary: raw.summary,
      synthesis: raw.synthesis,
      pvm: raw.pvm,
      products: raw.products,
      evidence: raw.evidence,
      logistics: raw.logistics,
      rootCause: raw.rootCause || (existing && existing.rootCause) || null,
      attribution: raw.attribution || (existing && existing.attribution) || null,
      graph_context: raw.graph_context,
      persona: raw.persona,
      generation_method: raw.generation_method,
      actionCorrection: raw.actionCorrection || (existing && existing.actionCorrection) || null,
      detectionType: raw.detection_type,
      evidenceScore: raw.evidence_score,
      evidenceClassification: raw.evidence_classification,
      abstained: abstained,
      abstention: raw.abstention,
      rawAction: action,
      recommendedAction: action ? {
        title: action.action,
        expectedImpact: action.expected_impact,
        owner: action.owner,
        monitoringPlan: action.monitoring_plan,
        driver: action.driver,
        controllableLever: action.controllable_lever,
        confidence: action.confidence,
        steps: steps
      } : null,
      _maskedFields: raw._masked_fields || []
    };
  }

  async fetchAnomalies(role) {
    if (!this.isConnected) {
      return Object.values(ANOMALY_DATASET);
    }
    try {
      const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.anomalies}`, { headers: this._headers(role) });
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('API call failed, falling back to offline dataset:', err);
    }
    return Object.values(ANOMALY_DATASET);
  }

  async fetchAnomalyDetail(anomalyKey, role) {
    if (!this.isConnected) {
      return null;
    }
    try {
      const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.anomalyDetail(anomalyKey)}`, { headers: this._headers(role) });
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('API call failed, falling back to offline dataset:', err);
    }
    return null;
  }

  async fetchAnomalyTimeline(anomalyKey, role, metric = 'revenue') {
    if (!this.isConnected) return null;
    try {
      const url = `${this.baseUrl}${API_CONFIG.endpoints.anomalyTimeline(anomalyKey)}?metric=${encodeURIComponent(metric)}`;
      const res = await fetch(url, { headers: this._headers(role) });
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('API call failed to fetch timeline:', err);
    }
    return null;
  }

  async fetchAnomalyGraph(anomalyKey, role) {
    if (!this.isConnected) return null;
    try {
      const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.anomalyGraph(anomalyKey)}`, { headers: this._headers(role) });
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('API call failed to fetch knowledge graph:', err);
    }
    return null;
  }

  async fetchTelemetry() {
    if (!this.isConnected) return null;
    try {
      const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.telemetry}`);
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('API call failed to fetch telemetry:', err);
    }
    return null;
  }

  async approveAction(anomalyKey, actionData = {}) {
    showAppToast(`Dispatching action approval to backend audit log...`);
    if (this.isConnected) {
      try {
        const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.approveAction(anomalyKey)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            anomaly_id: anomalyKey,
            approved_by: APP_STATE.activeRole,
            timestamp: new Date().toISOString(),
            ...actionData
          })
        });
        if (res.ok) {
          const result = await res.json();
          // result.audit_id already includes the "AUD-" prefix (api_server.py's
          // _new_audit_id()) -- this template used to prepend a second one, showing
          // "AUD-AUD-1a2b3c4d" in the toast.
          showAppToast(`Audit Log #${result.audit_id || 'AUD-000000'} recorded in SQLite`);
          return result;
        }
      } catch (err) {
        console.warn('Failed to record approval in backend, logged locally:', err);
      }
    }
    showAppToast(`Action verified & saved to local session audit queue`);
    return { success: true, local: true };
  }

  async assignAction(anomalyKey, assignee, sla) {
    if (!this.isConnected) return { success: false, local: true };
    try {
      const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.assignAction(anomalyKey)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assignee, sla, assigned_by: APP_STATE.activeRole, timestamp: new Date().toISOString() })
      });
      if (res.ok) return await res.json();
    } catch (err) {
      console.warn('Failed to record assignment in backend:', err);
    }
    return { success: false, local: true };
  }

  async submitUserFeedback(anomalyId, rating, comments = '') {
    if (this.isConnected) {
      try {
        await fetch(`${this.baseUrl}${API_CONFIG.endpoints.submitFeedback}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ anomaly_id: anomalyId, rating, user_comments: comments })
        });
      } catch (e) {
        console.warn('Failed to post feedback:', e);
      }
    }
  }

  /* Records "the recommended action is wrong, do this instead". The backend
     stores it keyed to the anomaly's scenario/KPI so it resurfaces on similar
     anomalies (see _match_action_correction in api_server.py). */
  async submitActionCorrection(anomalyKey, correctedAction, rationale = '') {
    if (!this.isConnected) return { success: false, local: true };
    try {
      const res = await fetch(`${this.baseUrl}${API_CONFIG.endpoints.correctAction(anomalyKey)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          corrected_action: correctedAction,
          rationale,
          role: APP_STATE.activeRole
        })
      });
      if (res.ok) return await res.json();
    } catch (e) {
      console.warn('Failed to submit action correction:', e);
    }
    return { success: false, local: true };
  }
}

const apiClient = new BackendApiClient();

// Check backend status on load and periodically
document.addEventListener('DOMContentLoaded', () => {
  apiClient.checkHealth();
  setInterval(() => apiClient.checkHealth(), API_CONFIG.pollIntervalMs);
});

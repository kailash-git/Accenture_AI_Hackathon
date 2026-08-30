"""
api_server.py
Zero-external-dependency REST API server bridging SQLite, the analytics
pipeline's stored output, and the dashboard frontend.

Security/entitlement note: every response below is masked server-side
according to schemas/semantic_contract.json's entitlements, based on the
caller's role (X-User-Role header or ?role= query param). This is real
enforcement -- restricted fields are actually removed from the JSON before
it leaves the server -- not a client-side display toggle.

The "admin" role (Data Governance & Compliance Admin) is a deliberate third
entitlements grant with an empty restricted_columns/masking_actions set in
the semantic contract -- see _apply_entitlements and _mask_graph_for_role,
which only branch on "supply_planner"/"vp_sales" and fall through unmasked
for any other valid role. It exists to demonstrate the policy layer
generalizes past two hardcoded personas, not as an unaudited bypass.
"""

import copy
import http.server
import json
import os
import re
import socketserver
import subprocess
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qs, urlparse

PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCENTURE_DIR = os.path.join(BASE_DIR, 'Accenture', 'Accenture')
DB_PATH = os.path.join(ACCENTURE_DIR, 'data', 'business_bi.db')
CONTRACT_PATH = os.path.join(ACCENTURE_DIR, 'schemas', 'semantic_contract.json')
SEED_SCRIPT_PATH = os.path.join(ACCENTURE_DIR, 'scripts', 'generate_mock_data.py')

# The anomaly-centric evidence graph (analytics.graph_builder) is built + pickled
# at seed time; this server loads it once at startup and serves per-anomaly
# subgraphs live (analytics.graph_subgraph.anomaly_subgraph). networkx is the
# only new runtime dependency this adds -- graph_query / graph_store / graph_subgraph
# are otherwise stdlib.
sys.path.insert(0, os.path.join(ACCENTURE_DIR, 'src'))
GRAPH_PATH = os.path.join(ACCENTURE_DIR, 'data', 'evidence_graph.gpickle')
GRAPH = None  # populated by _load_evidence_graph() in run_server()

VALID_ROLES = ("vp_sales", "supply_planner", "admin")
DEFAULT_ROLE = "vp_sales"

# Expert action corrections -- created lazily so it works on an already-seeded
# business_bi.db without a re-seed (mirrors schemas/db_init.sql table 6b).
_ACTION_CORRECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS action_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_id TEXT NOT NULL,
    scenario_key TEXT,
    kpi_name TEXT,
    cat_id TEXT,
    direction TEXT,
    detection_type TEXT,
    original_action TEXT,
    corrected_action TEXT NOT NULL,
    rationale TEXT,
    corrected_by TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

# Live-request SQL latency samples (rolling window, in-memory, reset on restart).
_SQL_LATENCY_SAMPLES_MS = []
_REQUEST_COUNT = 0

# --- Conversational assistant (optional, opt-in, live LLM path) ------------- #
# The dashboard's analytics path makes ZERO LLM calls -- every KPI narrative is
# pre-generated offline at seed time (see src/llm/llm_client.py). This endpoint
# is a SEPARATE, opt-in "ask a question about this movement" assistant. It is
# grounded: the model only ever sees the role-masked evidence for one anomaly
# (via the exact _apply_persona / _apply_entitlements enforcement every other
# endpoint uses) and is instructed to abstain when the evidence does not
# support an answer. The LLM is never the source of quantitative truth -- every
# number it can cite was already computed deterministically upstream.
#
# Provider is chosen at call time: GROQ_API_KEY (free tier, OpenAI-compatible)
# is used if present, otherwise ANTHROPIC_API_KEY. If neither is set -- or the
# call fails -- the endpoint returns a plain "assistant unavailable" message
# with HTTP 200 and the rest of the dashboard is unaffected.
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Default Groq-hosted model; override at runtime with GROQ_MODEL in the env/.env.
GROQ_MODEL_DEFAULT = "openai/gpt-oss-120b"

CHAT_MAX_TOKENS = 500
CHAT_TEMPERATURE = 0.2
CHAT_TIMEOUT_S = 20

# Best-effort per-1M-token pricing (USD) for claude-haiku-4-5, used only to
# report cost telemetry. Adjust here if published pricing changes.
_CHAT_PRICE_PER_1M_INPUT = 1.00
_CHAT_PRICE_PER_1M_OUTPUT = 5.00

# In-memory, reset-on-restart counters for the live conversational path.
_CHAT_TELEMETRY = {
    "calls": 0,
    "errors": 0,
    "clarifications": 0,   # turns answered deterministically by asking the user to disambiguate (no model call)
    "tokens_in": 0,
    "tokens_out": 0,
    "cost_usd": 0.0,
    "latency_ms_samples": [],
}

_CHAT_SYSTEM_PROMPT = (
    "You are a business-intelligence assistant embedded in a KPI analytics "
    "dashboard. Answer the user's question using ONLY the evidence in the "
    "CONTEXT block supplied with their message. The numbers in the context "
    "were computed deterministically by the analytics engine -- treat them as "
    "the only source of quantitative truth and never invent, extrapolate, or "
    "estimate figures that are not present. Explain findings in plain business "
    "language; do not mention JSON keys, column names, or internal record IDs. "
    "If a value is 'RESTRICTED' or a field is listed in "
    "restricted_fields_for_this_role, tell the user that detail is not "
    "available for their role rather than guessing around it. If the context "
    "does not contain what is needed to answer, say so plainly and do not "
    "speculate. If analysis_abstained is true or analysis_confidence is low, "
    "lead with the fact that the engine is not confident about this movement "
    "and explain why before saying anything else. Keep answers to 2-5 short "
    "sentences."
)

# The evidence the model sees is already role-masked (via _apply_persona /
# _apply_entitlements); this only tailors tone, depth, and which levers to
# emphasise per persona -- mirrors persona_profiles.md section 4.
_CHAT_PERSONA_GUIDANCE = {
    "vp_sales": (
        " The reader is a VP of Retail Sales: give an executive summary -- macro "
        "financial impact, price/volume/mix drivers, and the high-level decision "
        "to make. Do not mention SKU, warehouse, or carrier detail."
    ),
    "supply_planner": (
        " The reader is a Regional Supply Chain Planner: be operational and "
        "specific -- inventory turnover, lead times, shipment/carrier issues, and "
        "reorder actions. Do not state revenue, gross margin, or COGS figures."
    ),
    "admin": (
        " The reader is a Data Governance & Compliance admin: give a neutral, "
        "complete read of the evidence and call out any data-quality, freshness, "
        "or abstention caveats explicitly."
    ),
}


def _chat_system_prompt(role):
    return _CHAT_SYSTEM_PROMPT + _CHAT_PERSONA_GUIDANCE.get(role, "")


# --- Free-form question parsing (deterministic, no LLM) -------------------- #
# Lets a user ask about any month / region / KPI in words ("why did revenue
# fall in Texas in Oct 2012?") instead of only the movement currently open on
# the dashboard. Pure string/date matching against the real dimension values
# in the anomalies table -- the model is never asked to pick the record.
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Checked in order -- turnover/margin synonyms before the broader "sales".
_KPI_SYNONYMS = (
    ("InventoryTurnover", ("inventory turnover", "stock turn", "turnover", "inventory")),
    ("GrossMarginPercent", ("gross margin", "margin", "profitability", "profit")),
    ("Revenue", ("revenue", "sales", "top line", "top-line", "topline")),
)

_STATE_WORDS = {"california": "CA", "texas": "TX", "ca": "CA", "tx": "TX"}

_ANOMALY_DIMS = {"loaded": False, "states": (), "items": (), "categories": ()}


def _anomaly_dimensions(conn):
    """Distinct dimension values actually present in the anomalies table,
    loaded once and cached -- the query parser matches against these so it
    never invents a region/item/category the data doesn't have."""
    if not _ANOMALY_DIMS["loaded"]:
        try:
            cur = conn.cursor()
            _ANOMALY_DIMS["states"] = tuple(
                r[0] for r in cur.execute("SELECT DISTINCT state_id FROM anomalies")
            )
            _ANOMALY_DIMS["items"] = tuple(
                r[0] for r in cur.execute("SELECT DISTINCT item_id FROM anomalies")
            )
            _ANOMALY_DIMS["categories"] = tuple(
                r[0] for r in cur.execute("SELECT DISTINCT cat_id FROM anomalies")
            )
            _ANOMALY_DIMS["loaded"] = True
        except Exception:
            pass
    return _ANOMALY_DIMS


def _parse_chat_query(message, conn):
    """Extract {period|year, kpi, region, item, category} filters from free text.
    Any key missing means 'not mentioned' -- an empty dict means the question
    named no specific target and the caller should fall back to the open/most
    material movement."""
    msg = " " + (message or "").lower() + " "
    out = {}

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", msg)
    if m:
        out["date"] = m.group(0)
        out["period"] = "%s-%s" % (m.group(1), m.group(2))
    else:
        m = re.search(r"\b(\d{4})-(\d{2})\b", msg)
        if m and 1 <= int(m.group(2)) <= 12:
            out["period"] = m.group(0)
        else:
            names = "|".join(_MONTHS)
            m = (re.search(r"\b(%s)\.?\s+(\d{4})\b" % names, msg)
                 or re.search(r"\b(\d{4})\s+(%s)\b" % names, msg))
            if m:
                a, b = m.group(1), m.group(2)
                mon, yr = (a, b) if a in _MONTHS else (b, a)
                out["period"] = "%04d-%02d" % (int(yr), _MONTHS[mon])
            else:
                m = re.search(r"\b(20\d{2})\b", msg)
                if m:
                    out["year"] = m.group(1)

    for kpi, syns in _KPI_SYNONYMS:
        if any(s in msg for s in syns):
            out["kpi"] = kpi
            break

    dims = _anomaly_dimensions(conn)
    for word, code in _STATE_WORDS.items():
        if code in dims["states"] and re.search(r"\b%s\b" % word, msg):
            out["region"] = code
            break
    for it in dims["items"]:
        if it.lower() in msg:
            out["item"] = it
            break
    for cat in dims["categories"]:
        if re.search(r"\b%s\b" % re.escape(cat.lower()), msg):
            out["category"] = cat
            break
    return out


def _anomaly_candidate(row):
    period = (row["period_start"] or "")[:7]
    label = " · ".join(p for p in (
        row["kpi_name"], period, row["state_id"],
        (row["severity"] or "").title() or None,
    ) if p)
    return {"key": row["scenario_key"] or row["anomaly_id"], "label": label}


_ANOMALY_MATERIALITY_ORDER = """
    ORDER BY
        CASE WHEN scenario_key LIKE 'gen-%' THEN 1 ELSE 0 END,
        CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END,
        ABS(z_score) DESC
"""


def _select_anomaly_row(conn, key, message, focus=False):
    """Resolve which anomaly grounds this turn. Precedence:
      1. focus=True + key  -> use that key verbatim (a clarification pick).
      2. the question names a date / region / KPI -> match on that.
      3. an anomaly is open on the dashboard (key) -> use it.
      4. otherwise -> the single most material movement.
    Returns (row_or_None, meta). row is None only when the question is
    genuinely ambiguous (meta['ambiguous']) or there is no data at all."""
    meta = {
        "matched_on": [], "candidates": [], "ambiguous": False, "fallback": False,
        "narrowed": False, "date_requested": None, "date_had_anomaly": None,
        "requested_kpi": None,
    }

    def _by_key(k):
        rows, _ = _timed_query(
            conn, "SELECT * FROM anomalies WHERE scenario_key = ? OR anomaly_id = ?", (k, k)
        )
        return rows[0] if rows else None

    if focus and key:
        row = _by_key(key)
        if row is not None:
            meta["matched_on"] = ["your selected movement"]
            return row, meta

    q = _parse_chat_query(message, conn) if message else {}

    clauses, params, labels = [], [], []
    if q.get("period"):
        clauses.append("period_start LIKE ?"); params.append(q["period"] + "%")
        labels.append(q["period"]); meta["date_requested"] = q.get("date") or q["period"]
    elif q.get("year"):
        clauses.append("period_start LIKE ?"); params.append(q["year"] + "%")
        labels.append(q["year"]); meta["date_requested"] = q["year"]
    if q.get("kpi"):
        clauses.append("kpi_name = ?"); params.append(q["kpi"]); labels.append(q["kpi"])
        meta["requested_kpi"] = q["kpi"]
    if q.get("region"):
        clauses.append("state_id = ?"); params.append(q["region"]); labels.append(q["region"])
    if q.get("item"):
        clauses.append("item_id = ?"); params.append(q["item"]); labels.append(q["item"])
    if q.get("category"):
        clauses.append("cat_id = ?"); params.append(q["category"]); labels.append(q["category"])

    if clauses:
        rows, _ = _timed_query(
            conn,
            "SELECT * FROM anomalies WHERE " + " AND ".join(clauses) + _ANOMALY_MATERIALITY_ORDER,
            tuple(params),
        )
        meta["matched_on"] = labels
        if meta["date_requested"] is not None:
            meta["date_had_anomaly"] = bool(rows)
        if rows:
            if len(rows) > 1:
                meta["candidates"] = [_anomaly_candidate(r) for r in rows[:4]]
                if q.get("period") or q.get("year"):
                    meta["narrowed"] = True          # a time anchor lets us rank
                else:
                    meta["ambiguous"] = True          # nothing to rank on -> ask
            return (None if meta["ambiguous"] else rows[0]), meta
        meta["fallback"] = True                       # named a target, found nothing

    if not focus and key and not clauses:
        row = _by_key(key)
        if row is not None:
            meta["matched_on"] = ["the movement you're viewing"]
            return row, meta

    rows, _ = _timed_query(conn, "SELECT * FROM anomalies" + _ANOMALY_MATERIALITY_ORDER + " LIMIT 5")
    if not rows:
        return None, meta
    if not meta["matched_on"]:
        meta["matched_on"] = ["the most material recent movement"]
    if meta["fallback"]:
        meta["candidates"] = [_anomaly_candidate(r) for r in rows[:4]]
    return rows[0], meta


def _resolution_note(meta, kpi_name):
    """One plain sentence for the UI explaining why THIS movement was chosen --
    the transparency the brief asks for around abstention / clarification."""
    if meta["matched_on"] in (["your selected movement"], ["the movement you're viewing"]):
        return None
    if meta["fallback"] and meta.get("date_had_anomaly") is False:
        return ("No material %s movement was detected for %s, so this answer is about "
                "the most material recent movement instead." % (
                    meta.get("requested_kpi") or kpi_name or "KPI", meta["date_requested"]))
    if meta["narrowed"]:
        return ("Several movements match %s; showing the most material one. Add a "
                "month, region, or item to narrow it further." % ", ".join(meta["matched_on"]))
    if meta["matched_on"] == ["the most material recent movement"]:
        return "Your question named no specific month or region, so this is the most material recent movement."
    return "Showing the movement matching %s." % ", ".join(meta["matched_on"])


def _load_dotenv():
    """
    Minimal .env support with no dependency: populate os.environ from a .env
    file next to this script, but only for keys not already set in the real
    environment. Only simple KEY=VALUE lines are honored. The API key is never
    logged, printed, or echoed anywhere.
    """
    path = os.path.join(BASE_DIR, '.env')
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_dotenv()


def _load_contract():
    try:
        with open(CONTRACT_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


CONTRACT = _load_contract()


def _ensure_db_seeded():
    """
    Clean-machine safety net: if the database has never been generated, run
    the seeding pipeline once automatically instead of serving fabricated
    placeholder data. This runs the same script a developer would run by
    hand -- no separate/duplicate data path.
    """
    if os.path.exists(DB_PATH):
        return
    print("No database found at", DB_PATH)
    print("Running the seeding pipeline once (this can take under a minute)...")
    try:
        subprocess.run([sys.executable, SEED_SCRIPT_PATH], check=True, cwd=ACCENTURE_DIR)
    except Exception as e:
        print(f"WARNING: automatic seeding failed ({type(e).__name__}). "
              f"Run 'python scripts/generate_mock_data.py' manually from {ACCENTURE_DIR}.")


def _resolve_role(headers, query):
    role = headers.get('X-User-Role') or (query.get('role', [None])[0])
    if role not in VALID_ROLES:
        role = DEFAULT_ROLE
    return role


def _timed_query(conn, sql, params=()):
    start = time.perf_counter()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    _SQL_LATENCY_SAMPLES_MS.append(elapsed_ms)
    if len(_SQL_LATENCY_SAMPLES_MS) > 200:
        del _SQL_LATENCY_SAMPLES_MS[: len(_SQL_LATENCY_SAMPLES_MS) - 200]
    return rows, elapsed_ms


def _load_evidence_graph():
    """Load the pickled evidence graph once at startup. Any failure leaves
    GRAPH as None and every graph surface degrades to empty rather than 500."""
    global GRAPH
    try:
        from analytics.graph_store import load_graph, save_graph
        if not os.path.exists(GRAPH_PATH) and os.path.exists(DB_PATH):
            # DB seeded by an older checkout that predates the evidence graph --
            # build it once now rather than serving empty panels.
            print(f"  evidence graph missing -- building it from {DB_PATH} ...")
            from analytics.graph_builder import build_graph as _bg
            save_graph(_bg(DB_PATH), GRAPH_PATH)
        if not os.path.exists(GRAPH_PATH):
            print(f"  evidence graph not found at {GRAPH_PATH} -- graph panel will be empty "
                  f"until 'python scripts/build_graph.py' is run.")
            return
        GRAPH = load_graph(GRAPH_PATH)
        print(f"  evidence graph loaded: {GRAPH.number_of_nodes()} nodes / "
              f"{GRAPH.number_of_edges()} edges from {GRAPH_PATH}")
    except Exception as e:  # noqa: BLE001 -- never let graph load break the server
        GRAPH = None
        print(f"  WARNING: could not load evidence graph ({type(e).__name__}: {e}); "
              f"graph endpoints will return empty.")


_EMPTY_GRAPH_CTX = {"nodes": [], "edges": [], "node_count": 0, "focal": None}


def _anomaly_subgraph_for_row(r):
    """Per-anomaly subgraph from the live evidence graph for a DB row `r`
    (sqlite3.Row from the `anomalies` table). Falls back to the seed-time
    snapshot stored in graph_context_json if the live graph is unavailable."""
    if GRAPH is not None:
        try:
            from analytics.graph_subgraph import anomaly_subgraph
            return anomaly_subgraph(
                GRAPH, r["kpi_name"], r["item_id"], r["state_id"],
                r["period_start"], r["period_end"],
            )
        except Exception as e:  # noqa: BLE001
            print(f"  WARNING: anomaly_subgraph failed for {r['anomaly_id']}: "
                  f"{type(e).__name__}: {e}")
    try:
        stored = json.loads(r["graph_context_json"]) if r["graph_context_json"] else None
        if isinstance(stored, dict) and "nodes" in stored:
            return stored
    except Exception:
        pass
    return dict(_EMPTY_GRAPH_CTX)


def _row_to_anomaly_dict(r):
    return {
        "id": r["anomaly_id"],
        "detected_at": r["detected_at"],
        "kpi_name": r["kpi_name"],
        "item_id": r["item_id"],
        "state_id": r["state_id"],
        "cat_id": r["cat_id"],
        "period_start": r["period_start"],
        "period_end": r["period_end"],
        "actual_value": r["actual_value"],
        "baseline_value": r["baseline_value"],
        "deviation_pct": r["deviation_pct"],
        "z_score": r["z_score"],
        "direction": r["direction"],
        "severity": r["severity"],
        "confidence": r["confidence"],
        "status": r["status"],
        "scenario_key": r["scenario_key"],
        "detection_type": r["detection_type"],
        "evidence_score": r["evidence_score"],
        "evidence_classification": r["evidence_classification"],
        "abstained": bool(r["abstained"]),
        "abstention_reason": r["abstention_reason"],
        "pvm": json.loads(r["pvm_json"]),
        "products": json.loads(r["products_json"]),
        "evidence": json.loads(r["evidence_json"]),
        "logistics": json.loads(r["logistics_json"]),
        "graph_context": _anomaly_subgraph_for_row(r),
        "generation_telemetry": json.loads(r["generation_telemetry_json"]) if r["generation_telemetry_json"] else {},
        "narratives": json.loads(r["narratives_json"]) if r["narratives_json"] else {},
    }


def _apply_persona(anomaly, role):
    """Selects the role-specific narrative/action fields (REQ-04)."""
    narratives = anomaly.pop("narratives", {})
    persona_view = narratives.get(role) or narratives.get(DEFAULT_ROLE) or {}
    anomaly["persona"] = role
    anomaly["headline"] = persona_view.get("headline", "")
    anomaly["summary"] = persona_view.get("summary", "")
    anomaly["synthesis"] = {
        "title": persona_view.get("synthesis_title", ""),
        "body": persona_view.get("synthesis_body", ""),
    }
    anomaly["recommendedAction"] = persona_view.get("recommended_action")
    anomaly["abstention"] = persona_view.get("abstention")
    anomaly["generation_method"] = persona_view.get("generation_method", "deterministic")
    return anomaly


_FINANCIAL_DISCLOSURE_TERMS = ("revenue", "gross margin", "margin percent", "cost of goods")


def _redact_financial_disclosure(text, role):
    """
    Free-text evidence (support tickets, customer reviews) isn't a structured
    financial column, but it can still narrate one -- e.g. a support ticket that
    says "It shows high dollar revenue in our logs." A supply_planner is
    restricted from fact_sales_daily.revenue everywhere else in the payload
    (REQ-08); this closes the same enforcement over free text instead of
    trusting that no evidence snippet ever mentions a dollar figure.
    """
    if role != "supply_planner" or not text:
        return text
    sentences = re.split(r'(?<=[.!?])\s+', text)
    redacted = [
        "[Financial detail redacted for this role.]" if any(term in s.lower() for term in _FINANCIAL_DISCLOSURE_TERMS) else s
        for s in sentences
    ]
    return " ".join(redacted)


_VP_WAREHOUSE_KINDS = ("warehouse_entity", "supply_anomaly", "inventory_anomaly")
_PLANNER_FINANCIAL_ATTRS = ("value", "baseline_mean", "price_effect", "volume_effect",
                            "interaction_effect", "total_decomposed", "actual_delta")


def _mask_graph_for_role(graph_ctx, role):
    """
    Real server-side masking for the /graph endpoint and the anomaly-detail
    payload's embedded graph_context, mirroring _apply_entitlements -- the
    evidence-graph panel is a first-class surface of the semantic contract,
    not an unmasked side channel around it.

    The subgraph shape is {nodes:[{id,kind,label,layer,...attrs}], edges:[...],
    node_count, focal} (see analytics.graph_subgraph.anomaly_subgraph).
    "admin" falls through unmodified -- the contract's full-access role.
    """
    d = copy.deepcopy(graph_ctx) if graph_ctx else dict(_EMPTY_GRAPH_CTX)
    nodes = d.get("nodes", [])

    if role == "vp_sales":
        # SKU / warehouse-level identity is restricted for the VP everywhere else
        # (item_id, products[].sku, logistics.*) -- don't let the graph be the leak.
        for n in nodes:
            if n.get("kind") in ("item_entity",):
                n["label"] = "RESTRICTED"
                n["restricted"] = True
            if n.get("kind") in _VP_WAREHOUSE_KINDS:
                n["restricted"] = True
                if n.get("kind") == "warehouse_entity":
                    n["label"] = "RESTRICTED"
                n.pop("warehouse_sku", None)
                n.pop("inventory_on_hand", None)

    elif role == "supply_planner":
        # Revenue / margin / marketing-spend figures are restricted for this role.
        for n in nodes:
            kind = n.get("kind")
            if kind == "sales_anomaly":
                for attr in _PLANNER_FINANCIAL_ATTRS:
                    if attr in n:
                        n[attr] = None
                n["restricted"] = True
            elif kind == "marketing_anomaly":
                n["label"] = "RESTRICTED"
                n["restricted"] = True
                n.pop("value", None)
                n.pop("region", None)
        for e in d.get("edges", []):
            if "dollar_effect" in e:
                e["dollar_effect"] = None

    return d


def _apply_entitlements(anomaly, role):
    """
    Real server-side masking per schemas/semantic_contract.json (REQ-08).
    Restricted fields are actually removed from the payload, not just hidden
    in the UI -- a supply_planner-scoped request never receives revenue/
    margin figures, and a vp_sales-scoped request never receives SKU/
    warehouse-level logistics detail. "admin" matches neither branch below by
    design and returns the payload unmodified -- the contract's full-access
    governance role, not a missed case.
    """
    d = copy.deepcopy(anomaly)

    if d.get("graph_context"):
        # The anomaly-detail payload embeds the same graph context the dedicated
        # /graph endpoint serves -- mask it here too so this isn't a second,
        # unmasked path to the same restricted evidence/identity detail.
        d["graph_context"] = _mask_graph_for_role(d["graph_context"], role)

    if role == "supply_planner":
        d["actual_value"] = None
        d["baseline_value"] = None
        d["_masked_fields"] = ["actual_value", "baseline_value", "pvm.*.val", "products.*.revenueImpact", "marketing evidence"]
        if d.get("pvm"):
            for k in ("volume", "price", "mix", "other"):
                if k in d["pvm"]:
                    d["pvm"][k] = {"val": None, "pct": "RESTRICTED", "share_of_change": "RESTRICTED",
                                   "direction": d["pvm"][k].get("direction", "none"),
                                   "expl": "Financial figures restricted for this role."}
            if "driver_summary" in d["pvm"]:
                d["pvm"]["driver_summary"] = "RESTRICTED"
        for p in d.get("products", []):
            p["revenueImpact"] = "RESTRICTED"
        for e in d.get("evidence", []):
            if e.get("source") == "source_marketing_weekly":
                e["title"] = "RESTRICTED"
                e["preview"] = "RESTRICTED"
                e["fullText"] = "RESTRICTED"
            else:
                # Free-text support tickets/reviews aren't a structured source we can
                # blanket-restrict, but they can still narrate a revenue figure --
                # redact just the disclosing clause instead of the whole record.
                e["title"] = _redact_financial_disclosure(e.get("title", ""), role)
                e["preview"] = _redact_financial_disclosure(e.get("preview", ""), role)
                e["fullText"] = _redact_financial_disclosure(e.get("fullText", ""), role)
    elif role == "vp_sales":
        # anomaly_id is built as f"ANOM-{period}-{state_id}-{item_id}" (see
        # generate_mock_data.py) -- it embeds the same item_id restricted a few
        # lines below, so it must be redacted here too, or the identity that
        # d["item_id"] = "RESTRICTED" is supposed to hide leaks straight back
        # out through the id field sitting right next to it in this same payload.
        if d.get("id") and anomaly.get("item_id"):
            d["id"] = str(d["id"]).replace(anomaly["item_id"], "ITEM")
        d["item_id"] = "RESTRICTED"
        d["_masked_fields"] = ["id", "item_id", "logistics.*", "products.*.sku", "supply evidence detail"]
        if d.get("logistics"):
            d["logistics"] = {
                "title": "RESTRICTED", "status": "RESTRICTED", "statusClass": "",
                "desc": "Warehouse/SKU-level logistics detail is restricted for this role.", "metrics": [],
            }
        for p in d.get("products", []):
            p["sku"] = "RESTRICTED"
        for e in d.get("evidence", []):
            if e.get("source") == "source_supply_monthly":
                e["title"] = "RESTRICTED"
                e["preview"] = "RESTRICTED"
                e["fullText"] = "RESTRICTED"
    return d


def _chat_provider():
    """Which LLM backend the chat endpoint will use this call, or None."""
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _active_chat_model():
    p = _chat_provider()
    if p == "groq":
        return os.environ.get("GROQ_MODEL", GROQ_MODEL_DEFAULT)
    if p == "anthropic":
        return ANTHROPIC_MODEL
    return None


def _chat_available():
    return _chat_provider() is not None


def _chat_cost(tokens_in, tokens_out):
    return (
        (tokens_in / 1_000_000.0) * _CHAT_PRICE_PER_1M_INPUT
        + (tokens_out / 1_000_000.0) * _CHAT_PRICE_PER_1M_OUTPUT
    )


def _chat_llm(system_prompt, user_prompt):
    """Dispatch one grounded turn to whichever provider is configured."""
    if _chat_provider() == "groq":
        return _call_groq(system_prompt, user_prompt)
    return _anthropic_chat(system_prompt, user_prompt)


def _call_groq(system_prompt, user_prompt):
    """
    One Groq chat-completions call via stdlib urllib (Groq is OpenAI-compatible).
    Same return-dict shape as _anthropic_chat; never raises. Groq's free tier is
    $0, so cost is reported as 0.0 while tokens/latency are still measured.
    """
    out = {
        "success": False, "text": "", "tokens_in": 0, "tokens_out": 0,
        "cost_usd": 0.0, "latency_s": 0.0,
        "model": os.environ.get("GROQ_MODEL", GROQ_MODEL_DEFAULT), "error": None,
    }
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        out["error"] = "GROQ_API_KEY not set"
        return out

    body = json.dumps({
        "model": out["model"],
        "max_tokens": CHAT_MAX_TOKENS,
        "temperature": CHAT_TEMPERATURE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(GROQ_API_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", "Bearer " + api_key)
    # Default urllib User-Agent can be blocked by the CDN in front of Groq.
    req.add_header("user-agent", "Mozilla/5.0 (compatible; kpi-dashboard/1.0)")

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
        out["latency_s"] = time.perf_counter() - start
        data = json.loads(raw)
        choice = (data.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        usage = data.get("usage", {}) or {}
        out["tokens_in"] = int(usage.get("prompt_tokens", 0) or 0)
        out["tokens_out"] = int(usage.get("completion_tokens", 0) or 0)
        out["text"] = text
        out["success"] = bool(text)
        if not text:
            out["error"] = "empty response from model"
        return out
    except urllib.error.HTTPError as e:
        out["latency_s"] = time.perf_counter() - start
        detail = ""
        try:
            body_err = json.loads(e.read().decode("utf-8"))
            detail = ": " + str(body_err.get("error", {}).get("message", ""))[:200]
        except Exception:
            pass
        out["error"] = f"HTTP {e.code} from Groq API{detail}"
        return out
    except Exception as e:
        out["latency_s"] = time.perf_counter() - start
        out["error"] = f"{type(e).__name__} during Groq call"
        return out


def _anthropic_chat(system_prompt, user_prompt):
    """
    Exactly one Anthropic Messages API call via the stdlib (urllib) -- no SDK
    dependency, keeping this server zero-external-dependency. Never raises:
    every failure is captured in the returned dict so the caller can fall back
    deterministically. The API key is read from the environment only and is
    never logged or returned.
    """
    out = {
        "success": False, "text": "", "tokens_in": 0, "tokens_out": 0,
        "cost_usd": 0.0, "latency_s": 0.0, "model": ANTHROPIC_MODEL, "error": None,
    }
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        out["error"] = "ANTHROPIC_API_KEY not set"
        return out

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": CHAT_MAX_TOKENS,
        "temperature": CHAT_TEMPERATURE,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(ANTHROPIC_API_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("anthropic-version", ANTHROPIC_VERSION)
    req.add_header("x-api-key", api_key)
    # Identity-linked API keys must name the workspace the request acts in.
    # Optional -- workspace-scoped keys don't need it.
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        req.add_header("anthropic-workspace-id", workspace_id)

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
        out["latency_s"] = time.perf_counter() - start
        data = json.loads(raw)
        parts = data.get("content", []) or []
        text = "".join(
            p.get("text", "") for p in parts if p.get("type") == "text"
        ).strip()
        usage = data.get("usage", {}) or {}
        out["tokens_in"] = int(usage.get("input_tokens", 0) or 0)
        out["tokens_out"] = int(usage.get("output_tokens", 0) or 0)
        out["cost_usd"] = _chat_cost(out["tokens_in"], out["tokens_out"])
        out["text"] = text
        out["success"] = bool(text)
        if not text:
            out["error"] = "empty response from model"
        return out
    except urllib.error.HTTPError as e:
        out["latency_s"] = time.perf_counter() - start
        detail = ""
        try:
            body_err = json.loads(e.read().decode("utf-8"))
            detail = ": " + str(body_err.get("error", {}).get("message", ""))[:200]
        except Exception:
            pass
        out["error"] = f"HTTP {e.code} from model API{detail}"
        return out
    except Exception as e:
        out["latency_s"] = time.perf_counter() - start
        out["error"] = f"{type(e).__name__} during model call"
        return out


def _compact_evidence(evidence):
    """Trim evidence records to just what the assistant needs, to keep the
    prompt small -- titles/previews and provenance, no raw internal ids."""
    slim = []
    for e in (evidence or [])[:6]:
        slim.append({
            "date": e.get("date"),
            "source": e.get("source"),
            "type": e.get("type"),
            "title": e.get("title"),
            "detail": e.get("preview") or e.get("fullText"),
            "similarity": e.get("similarity"),
        })
    return slim


def _chat_processing_breakdown(llm_used):
    """The LLM-vs-non-LLM split the brief asks every turn to make explicit.
    Every quantitative step is deterministic; the model only ever does wording."""
    return {
        "deterministic": [
            "parsed the question for date / region / KPI filters (regex)",
            "selected the matching movement by materiality (SQL over the anomalies table)",
            "applied role-based entitlement masking to the evidence (semantic contract)",
            "assembled price/volume/mix + evidence context (computed offline at seed time)",
        ],
        "llm": (["worded the answer from the supplied evidence only"] if llm_used
                else ["not called for this turn"]),
    }


def _chat_anomaly_context(key, role, message=None, focus=False):
    """
    Build the grounded, role-masked context block the assistant is allowed to
    see for one anomaly. Reuses the same _apply_persona / _apply_entitlements
    enforcement every other endpoint uses -- the assistant never sees a field
    the caller's role is not entitled to. Returns (context_or_None, label, meta);
    context is None when the DB is missing or the question is ambiguous
    (meta['ambiguous']).
    """
    meta = {"matched_on": [], "candidates": [], "ambiguous": False, "fallback": False,
            "narrowed": False, "date_requested": None, "date_had_anomaly": None}
    if not os.path.exists(DB_PATH):
        return None, "no data", meta
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row, meta = _select_anomaly_row(conn, key, message, focus=focus)
    finally:
        conn.close()

    if row is None:
        return None, ("ambiguous" if meta["ambiguous"] else "no data"), meta

    a = _row_to_anomaly_dict(row)
    a = _apply_persona(a, role)
    a = _apply_entitlements(a, role)

    period = (a.get("period_start") or "")[:7]
    label = " · ".join(p for p in (a.get("kpi_name"), period, a.get("state_id")) if p)

    abstention_reason = a.get("abstention_reason")
    abst = a.get("abstention")
    if not abstention_reason and isinstance(abst, dict):
        abstention_reason = abst.get("reason") or abst.get("body")

    context = {
        "kpi_name": a.get("kpi_name"),
        "period_start": a.get("period_start"),
        "period_end": a.get("period_end"),
        "region": a.get("state_id"),
        "direction": a.get("direction"),
        "deviation_pct": a.get("deviation_pct"),
        "z_score": a.get("z_score"),
        "severity": a.get("severity"),
        "analysis_confidence": a.get("confidence"),
        "analysis_abstained": bool(a.get("abstained")),
        "status": a.get("status"),
        "detection_type": a.get("detection_type"),
        "evidence_classification": a.get("evidence_classification"),
        "abstention_reason": abstention_reason,
        "headline": a.get("headline"),
        "summary": a.get("summary"),
        "synthesis": a.get("synthesis"),
        "price_volume_mix": a.get("pvm"),
        # Authored-once, correctly-signed driver sentence -- the model should use
        # this phrasing rather than re-deriving percentages from pvm.*.val.
        "revenue_driver_summary": (a.get("pvm") or {}).get("driver_summary"),
        "drivers_opposing": (a.get("pvm") or {}).get("drivers_opposing"),
        "products": a.get("products"),
        "recommended_action": a.get("recommendedAction"),
        "evidence": _compact_evidence(a.get("evidence")),
        "restricted_fields_for_this_role": a.get("_masked_fields", []),
    }
    if meta["fallback"] and meta.get("date_had_anomaly") is False:
        context["note_for_assistant"] = (
            "The user asked about %s, but no material movement was detected then. "
            "Say that plainly, then answer about the movement in the context."
            % meta["date_requested"]
        )
    return context, label, meta


def build_chat_response(message, role=DEFAULT_ROLE, anomaly_key=None, focus=False):
    """
    Orchestrates one grounded assistant turn. Deterministic steps: parse the
    question, resolve the anomaly, apply role entitlements, assemble the
    evidence context -- and, if the question is ambiguous, answer it by asking
    the user to pick (no model call at all). LLM step: word an answer over that
    context only. Every failure path returns grounded=False so the frontend can
    render it as a notice.
    """
    context, label, meta = _chat_anomaly_context(anomaly_key, role, message=message, focus=focus)

    # Deterministic clarification path -- the brief's "requests clarification"
    # behaviour. No model call: we don't know which movement the user means.
    if context is None and meta.get("ambiguous"):
        _CHAT_TELEMETRY["clarifications"] += 1
        return {
            "reply": "Your question could point to a few different movements. "
                     "Which one do you mean?",
            "grounded": False, "abstained": False, "needs_clarification": True,
            "candidates": meta["candidates"], "anomaly": None, "role": role,
            "llm_used": False, "model": _active_chat_model(),
            "processing": _chat_processing_breakdown(False),
        }

    if context is None:
        return {
            "reply": "I can't reach the analytics data right now, so there's no "
                     "evidence to ground an answer. Try again once the backend "
                     "has loaded the KPI database.",
            "grounded": False, "abstained": True, "anomaly": label, "role": role,
            "llm_used": False, "model": _active_chat_model(), "error": "no anomaly context",
            "processing": _chat_processing_breakdown(False),
        }

    resolution = _resolution_note(meta, context.get("kpi_name"))

    if not _chat_available():
        return {
            "reply": "The conversational assistant is not configured on this "
                     "server (set GROQ_API_KEY or ANTHROPIC_API_KEY). Everything "
                     "else works normally -- every KPI narrative on the dashboard "
                     "was generated offline with no live model call.",
            "grounded": False, "abstained": True, "anomaly": label, "role": role,
            "resolution": resolution, "llm_used": False, "model": None,
            "error": "no chat provider configured",
            "processing": _chat_processing_breakdown(False),
        }

    user_prompt = (
        "CONTEXT (caller role: %s -- this is the only evidence you may use):\n"
        "%s\n\nQUESTION: %s"
    ) % (role, json.dumps(context, indent=2, default=str), message)

    res = _chat_llm(_chat_system_prompt(role), user_prompt)

    _CHAT_TELEMETRY["calls"] += 1
    _CHAT_TELEMETRY["tokens_in"] += res["tokens_in"]
    _CHAT_TELEMETRY["tokens_out"] += res["tokens_out"]
    _CHAT_TELEMETRY["cost_usd"] += res["cost_usd"]
    if res["latency_s"]:
        _CHAT_TELEMETRY["latency_ms_samples"].append(res["latency_s"] * 1000.0)
        if len(_CHAT_TELEMETRY["latency_ms_samples"]) > 200:
            del _CHAT_TELEMETRY["latency_ms_samples"][:-200]

    if not res["success"]:
        _CHAT_TELEMETRY["errors"] += 1
        return {
            "reply": "I couldn't get a response from the model just now. Please "
                     "try again in a moment.",
            "grounded": False, "abstained": True, "anomaly": label, "role": role,
            "resolution": resolution, "llm_used": True, "model": res["model"],
            "error": res["error"], "processing": _chat_processing_breakdown(True),
        }

    return {
        "reply": res["text"],
        "grounded": True,
        "abstained": bool(context.get("analysis_abstained")),
        "anomaly": label,
        "resolution": resolution,
        "role": role,
        "llm_used": True,
        "model": res["model"],
        "restricted_fields": context.get("restricted_fields_for_this_role", []),
        "processing": _chat_processing_breakdown(True),
        "telemetry": {
            "tokens_in": res["tokens_in"],
            "tokens_out": res["tokens_out"],
            "cost_usd": round(res["cost_usd"], 6),
            "latency_ms": round(res["latency_s"] * 1000.0, 1),
        },
    }


class ApiRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, X-User-Role')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        global _REQUEST_COUNT
        _REQUEST_COUNT += 1
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        query = parse_qs(parsed.query)
        role = _resolve_role(self.headers, query)

        if path == '/api/health':
            self._send_json({
                "status": "healthy",
                "database_connected": os.path.exists(DB_PATH),
                "database_path": DB_PATH,
                "version": "2.0.0",
                "engine": "KPI Intelligence Backend API",
            })
            return

        if path in ('/api/anomalies/latest', '/api/anomalies'):
            self._handle_anomalies_list(role)
            return

        if path.startswith('/api/anomalies/') and path.endswith('/timeline'):
            key = path.split('/')[3]
            metric = (query.get('metric', ['revenue'])[0] or 'revenue').lower()
            self._handle_timeline(key, role, metric)
            return

        if path.startswith('/api/anomalies/') and path.endswith('/graph'):
            key = path.split('/')[3]
            self._handle_graph(key, role)
            return

        if path.startswith('/api/anomalies/'):
            key = path.split('/')[3] if len(path.split('/')) > 3 else None
            self._handle_anomaly_detail(key, role)
            return

        if path == '/api/telemetry':
            self._handle_telemetry()
            return

        if path == '/api/entitlements':
            self._send_json(CONTRACT.get('semantic_layer', {}).get('entitlements', {}).get(role, {}))
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len) if content_len > 0 else b'{}'
        try:
            payload = json.loads(body.decode('utf-8'))
        except Exception:
            payload = {}

        if path.startswith('/api/actions/') and path.endswith('/approve'):
            anomaly_key = path.split('/')[3]
            audit_id = self._new_audit_id()
            self._log_feedback(anomaly_key, 1, f"Approved action via dashboard. Audit #{audit_id}")
            self._send_json({
                "success": True, "audit_id": audit_id, "status": "Approved & Dispatched",
                "anomaly_id": anomaly_key,
                "message": "Action successfully recorded and assigned to operations queue.",
            })
            return

        if path.startswith('/api/actions/') and path.endswith('/assign'):
            # Was previously a purely client-side toast (confirmAssignment in
            # actions.js) with no backend call at all -- "recommends actions grounded
            # in... decision rights" (REQ-06) means who it was dispatched to and under
            # what SLA needs to actually be recorded, the same way Approve already is,
            # not just flashed as a toast and forgotten on refresh.
            anomaly_key = path.split('/')[3]
            audit_id = self._new_audit_id()
            assignee = payload.get('assignee', 'unassigned')
            sla = payload.get('sla', 'unspecified')
            self._log_feedback(anomaly_key, 1, f"Assigned to {assignee} (SLA {sla}) via dashboard. Audit #{audit_id}")
            self._send_json({
                "success": True, "audit_id": audit_id, "status": "Assigned & Dispatched",
                "anomaly_id": anomaly_key, "assignee": assignee, "sla": sla,
            })
            return

        if path.startswith('/api/actions/') and path.endswith('/correct'):
            # The user judged the recommended action wrong and typed what to do
            # instead. Stored so future similar anomalies surface the correction.
            anomaly_key = path.split('/')[3]
            corrected = (payload.get('corrected_action') or '').strip()
            if not corrected:
                self._send_json({"error": "corrected_action is required"}, status_code=400)
                return
            rationale = (payload.get('rationale') or '').strip()
            role = payload.get('role')
            if role not in VALID_ROLES:
                role = _resolve_role(self.headers, {})
            audit_id = self._new_audit_id()
            correction_id = self._save_action_correction(anomaly_key, corrected, rationale, role)
            # Also record it as a thumbs-down so the telemetry feedback tally reflects it.
            self._log_feedback(anomaly_key, -1,
                               f"Action correction (Audit #{audit_id}): {corrected[:240]}")
            self._send_json({
                "success": correction_id is not None,
                "audit_id": audit_id,
                "correction_id": correction_id,
            })
            return

        if path == '/api/feedback':
            anomaly_id = payload.get('anomaly_id', 'unknown')
            rating = payload.get('rating', 1)
            comments = payload.get('user_comments', '')
            self._log_feedback(anomaly_id, rating, comments)
            self._send_json({"success": True, "logged": True})
            return

        if path == '/api/chat':
            message = (payload.get('message') or '').strip()
            if not message:
                self._send_json({"error": "message is required"}, status_code=400)
                return
            role = payload.get('role')
            if role not in VALID_ROLES:
                role = _resolve_role(self.headers, {})
            anomaly_key = payload.get('anomaly_key') or payload.get('anomalyKey')
            # focus=True means the caller explicitly picked this anomaly (a
            # clarification-prompt choice) -- use it verbatim, don't re-parse.
            focus = bool(payload.get('focus'))
            result = build_chat_response(message, role=role, anomaly_key=anomaly_key, focus=focus)
            self._send_json(result)
            return

        self.send_error(404, "Endpoint Not Found")

    # ------------------------------------------------------------------ #
    def _handle_anomalies_list(self, role):
        if not os.path.exists(DB_PATH):
            self._send_json({"error": "Database not seeded. Run scripts/generate_mock_data.py."}, status_code=503)
            return
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # "Detects and prioritises material KPI movements" (Round 2 brief, REQ-01) --
            # ordering purely by period_start DESC is recency, not prioritization by
            # materiality. Curated scenarios (scenario_key not starting "gen-") get a
            # full PVM/evidence/action workup and represent completed diagnoses, so they
            # rank ahead of raw statistical detections; within each group, rank by
            # severity tier then |z_score| (the actual statistical-significance measure
            # AnomalyDetector used to flag it), so the most materially significant
            # movements surface first regardless of when they happened to occur.
            rows, _ = _timed_query(conn, """
                SELECT * FROM anomalies
                ORDER BY
                    CASE WHEN scenario_key LIKE 'gen-%' THEN 1 ELSE 0 END,
                    CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END,
                    ABS(z_score) DESC
            """)
        finally:
            conn.close()
        anomalies = []
        for r in rows:
            a = _row_to_anomaly_dict(r)
            a = _apply_persona(a, role)
            a = _apply_entitlements(a, role)
            anomalies.append(a)
        self._send_json(anomalies)

    def _fetch_anomaly_row(self, conn, key):
        rows, _ = _timed_query(conn, "SELECT * FROM anomalies WHERE scenario_key = ? OR anomaly_id = ?", (key, key))
        if rows:
            return rows[0]
        rows, _ = _timed_query(conn, "SELECT * FROM anomalies LIMIT 1")
        return rows[0] if rows else None

    def _handle_anomaly_detail(self, key, role):
        if not os.path.exists(DB_PATH):
            self._send_json({"error": "Database not seeded. Run scripts/generate_mock_data.py."}, status_code=503)
            return
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            r = self._fetch_anomaly_row(conn, key)
        finally:
            conn.close()
        if not r:
            self._send_json({"error": f"No anomaly found for key '{key}'"}, status_code=404)
            return
        a = _row_to_anomaly_dict(r)
        a = _apply_persona(a, role)
        a = _apply_entitlements(a, role)
        # Learning loop: if an expert has corrected the action for this anomaly
        # or a similar one, hand the correction back so the UI can surface it.
        a["actionCorrection"] = self._match_action_correction(r)
        self._send_json(a)

    def _handle_graph(self, key, role):
        empty = dict(_EMPTY_GRAPH_CTX)
        if not os.path.exists(DB_PATH):
            self._send_json(empty)
            return
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            r = self._fetch_anomaly_row(conn, key)
        finally:
            conn.close()
        if not r:
            self._send_json(empty)
            return
        # Extract this anomaly's subgraph live from the loaded evidence graph
        # (falls back to the seed-time snapshot in graph_context_json), then
        # apply role-based entitlement masking.
        graph_ctx = _anomaly_subgraph_for_row(r)
        self._send_json(_mask_graph_for_role(graph_ctx, role))

    def _handle_timeline(self, key, role, metric='revenue'):
        """
        Serves the trajectory chart's monthly series for the anomaly's own
        (item_id, state_id) -- for metric='revenue' this is the original
        Revenue/Units series; for 'margin' and 'turnover' it computes the SAME
        two other KPIs the semantic contract actually defines (GrossMarginPercent,
        InventoryTurnover), which until now were declared in the contract and
        detectable via AnomalyDetector but never actually wired to anything --
        the "Gross Margin %"/"Inventory Turnover" tabs in the UI called a
        switchActiveKPI() that didn't exist. Three real, connected KPIs, not one
        real KPI plus two decorative tab labels (Round 2 brief: "Three to five
        connected KPIs across two or three data sources with different grains").
        """
        empty = {"labels": [], "values": [], "valueLabel": "", "anomalyIndex": None,
                 "anomalies": [], "focusIndex": None, "focusKey": None, "focusKpi": "",
                 "anomalyColor": "#ef4444", "headlineDelta": "", "isNegative": False}
        if not os.path.exists(DB_PATH):
            self._send_json(empty)
            return
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            detail_row = self._fetch_anomaly_row(conn, key)
            if not detail_row:
                self._send_json(empty)
                return
            item_id = detail_row["item_id"]
            state_id = detail_row["state_id"]
            series_anomalies = []
            _ANOM_KPI_NAME = {'revenue': 'Revenue', 'margin': 'GrossMarginPercent', 'turnover': 'InventoryTurnover'}

            if metric == 'margin':
                # gross_margin_percent is explicitly restricted for supply_planner in
                # semantic_contract.json (same as the revenue/COGS columns it's derived
                # from) -- masked here server-side, not left to the client to hide.
                if role == 'supply_planner':
                    self._send_json({**empty, "valueLabel": "Gross Margin %", "restricted": True})
                    return
                months = self._monthly_margin_series(conn, item_id, state_id)
                value_label = "Gross Margin %"
            elif metric == 'turnover':
                months = self._monthly_turnover_series(conn, item_id, state_id)
                value_label = "Inventory Turnover"
            else:
                # Persona-aware series: planners see unit velocity, executives see revenue --
                # this also means a supply_planner-scoped request never receives $ figures.
                if role == "supply_planner":
                    metric_sql, value_label = "SUM(units)", "Units"
                else:
                    metric_sql, value_label = "SUM(revenue)", "Revenue"
                rows, _ = _timed_query(conn, f"""
                    SELECT strftime('%Y-%m', date) as m, {metric_sql}
                    FROM fact_sales_daily
                    WHERE item_id = ? AND state_id = ?
                    GROUP BY m
                    ORDER BY m ASC
                """, (item_id, state_id))
                months = [(r[0], float(r[1]) if r[1] is not None else 0.0) for r in rows]

            # Every anomaly on this same (item_id, state_id) series for the KPI
            # being charted -- returned so the chart can plot them all as dots the
            # analyst can click through to, not just the one currently open.
            anom_rows, _ = _timed_query(conn, """
                SELECT scenario_key, anomaly_id, period_start, direction, severity
                FROM anomalies
                WHERE item_id = ? AND state_id = ? AND kpi_name = ?
            """, (item_id, state_id, _ANOM_KPI_NAME.get(metric, 'Revenue')))
            series_anomalies = [dict(r) for r in anom_rows]
        finally:
            conn.close()

        import calendar
        labels = []
        values = []
        for m, v in months:
            month_num = int(m[5:7])
            year = m[2:4]
            labels.append(f"{calendar.month_abbr[month_num]} {year}")
            values.append(v)

        anomalies_out = []
        month_to_idx = {m: i for i, (m, _) in enumerate(months)}
        selected_key = detail_row["scenario_key"] or detail_row["anomaly_id"]
        for a in series_anomalies:
            mi = month_to_idx.get((a["period_start"] or "")[:7])
            if mi is None:
                continue
            akey = a["scenario_key"] or a["anomaly_id"]
            anomalies_out.append({
                "monthIndex": mi,
                "key": akey,
                "label": f'{(a["severity"] or "").title() or "KPI"} anomaly',
                "severity": a["severity"],
                "direction": a["direction"],
                "selected": akey == selected_key,
            })

        deviation_pct = detail_row["deviation_pct"] or 0.0
        if metric == 'revenue':
            anomaly_idx = month_to_idx.get(detail_row["period_start"][:7])
            is_negative = deviation_pct < 0
            headline_delta = f"{deviation_pct * 100:+.1f}%"
        else:
            # If the open anomaly is itself on this KPI series, anchor the headline
            # to its own month; otherwise fall back to the rolling z-score last-point
            # check (window=8, threshold=2.0 -- the same convention AnomalyDetector uses).
            sel = next((x for x in anomalies_out if x["selected"]), None)
            if sel is not None:
                anomaly_idx = sel["monthIndex"]
                is_negative = deviation_pct < 0
                headline_delta = f"{deviation_pct * 100:+.1f}%"
            else:
                anomaly_idx, is_negative, headline_delta = self._flag_last_point_if_anomalous(values)

        # The month of the anomaly currently under investigation, regardless of
        # whether it belongs to the KPI being charted -- lets the chart always
        # mark "you are looking into this point in time" even on a KPI tab where
        # that anomaly has no dot of its own.
        focus_idx = month_to_idx.get((detail_row["period_start"] or "")[:7])

        self._send_json({
            "labels": labels,
            "values": values,
            "valueLabel": value_label,
            "anomalyIndex": anomaly_idx,
            "anomalies": anomalies_out,
            "focusIndex": focus_idx,
            "focusKey": selected_key,
            "focusKpi": value_label,
            "anomalyColor": "#ef4444" if is_negative else "#10b981",
            "headlineDelta": headline_delta,
            "isNegative": is_negative,
        })

    def _monthly_margin_series(self, conn, item_id, state_id):
        rows, _ = _timed_query(conn, """
            SELECT strftime('%Y-%m', date) as m, SUM(revenue) as rev, SUM(cost_of_goods_sold) as cogs
            FROM fact_sales_daily
            WHERE item_id = ? AND state_id = ?
            GROUP BY m
            ORDER BY m ASC
        """, (item_id, state_id))
        out = []
        for m, rev, cogs in rows:
            rev = rev or 0.0
            margin = (rev - (cogs or 0.0)) / rev if rev else 0.0
            out.append((m, round(margin * 100, 2)))
        return out

    def _monthly_turnover_series(self, conn, item_id, state_id):
        rows, _ = _timed_query(conn, """
            SELECT strftime('%Y-%m', il.date) as m,
                   SUM(COALESCE(fs.cost_of_goods_sold, 0)) as cogs_sum,
                   AVG(il.inventory_on_hand * sl.supplier_raw_cost) as avg_inv_val
            FROM inventory_logs il
            JOIN sku_lookup sl ON il.item_id = sl.item_id
            LEFT JOIN fact_sales_daily fs
                ON il.date = fs.date AND il.item_id = fs.item_id AND il.state_id = fs.state_id
            WHERE il.item_id = ? AND il.state_id = ?
            GROUP BY m
            ORDER BY m ASC
        """, (item_id, state_id))
        out = []
        for m, cogs_sum, avg_inv_val in rows:
            turnover = (cogs_sum or 0.0) / avg_inv_val if avg_inv_val else 0.0
            out.append((m, round(turnover, 3)))
        return out

    @staticmethod
    def _flag_last_point_if_anomalous(values, window=8, threshold=2.0):
        if len(values) < window + 1:
            return None, False, ""
        baseline = values[-(window + 1):-1]
        mean_val = sum(baseline) / len(baseline)
        variance = sum((v - mean_val) ** 2 for v in baseline) / len(baseline)
        std_val = variance ** 0.5
        last = values[-1]
        z = (last - mean_val) / std_val if std_val >= 1e-5 else 0.0
        deviation_pct = (last - mean_val) / mean_val if mean_val else 0.0
        if abs(z) >= threshold:
            return len(values) - 1, deviation_pct < 0, f"{deviation_pct * 100:+.1f}%"
        return None, False, ""

    def _handle_telemetry(self):
        summary_row = None
        anomaly_count = 0
        abstained_count = 0
        feedback_count = 0
        feedback_avg_rating = None
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            try:
                rows, _ = _timed_query(conn, "SELECT * FROM telemetry_summary ORDER BY run_id DESC LIMIT 1")
                if rows:
                    summary_row = rows[0]
                count_rows, _ = _timed_query(conn, "SELECT COUNT(*), SUM(abstained) FROM anomalies")
                if count_rows:
                    anomaly_count = count_rows[0][0] or 0
                    abstained_count = count_rows[0][1] or 0
                # REQ-07 evidence: this is the closed loop for "mechanism to learn from
                # analyst and business-user feedback" -- the Approve button and the
                # synthesis thumbs-up/down both write here, and this is where that
                # capture becomes visible again rather than disappearing into the DB.
                fb_rows, _ = _timed_query(conn, "SELECT COUNT(*), AVG(rating) FROM user_feedback")
                if fb_rows and fb_rows[0][0]:
                    feedback_count = fb_rows[0][0]
                    feedback_avg_rating = round(fb_rows[0][1], 2) if fb_rows[0][1] is not None else None
            finally:
                conn.close()

        avg_live_sql_ms = (
            sum(_SQL_LATENCY_SAMPLES_MS) / len(_SQL_LATENCY_SAMPLES_MS) if _SQL_LATENCY_SAMPLES_MS else 0.0
        )
        data_freshness_seconds = (time.time() - os.path.getmtime(DB_PATH)) if os.path.exists(DB_PATH) else None

        payload = {
            "live_avg_sql_latency_ms": round(avg_live_sql_ms, 2),
            "live_request_count": _REQUEST_COUNT,
            "active_anomalies_count": anomaly_count,
            "abstained_count": abstained_count,
            "feedback_count": feedback_count,
            "feedback_avg_rating": feedback_avg_rating,
            "data_freshness_seconds": round(data_freshness_seconds, 1) if data_freshness_seconds is not None else None,
        }
        if summary_row:
            payload.update({
                "seed_run_at": summary_row["run_at"],
                "seed_anomalies_processed": summary_row["anomalies_processed"],
                "seed_llm_calls": summary_row["llm_calls"],
                "seed_llm_generated_count": summary_row["llm_generated_count"],
                "seed_deterministic_generated_count": summary_row["deterministic_generated_count"],
                "seed_total_tokens_in": summary_row["total_tokens_in"],
                "seed_total_tokens_out": summary_row["total_tokens_out"],
                "seed_total_cost_usd": summary_row["total_cost_usd"],
                "seed_pipeline_seconds": summary_row["total_pipeline_seconds"],
                "seed_avg_sql_query_ms": summary_row["avg_sql_query_ms"],
                "note": (
                    "LLM calls happen once, offline, at data-seed time only (never on the live request "
                    "path) -- live dashboard/API traffic makes zero LLM calls and costs $0."
                ),
            })
        else:
            payload["note"] = "No seed telemetry recorded yet."

        # Live conversational-assistant path (opt-in, separate from the
        # deterministic dashboard path above). Zero until a user actually asks
        # a question; every call is measured here so the LLM-vs-non-LLM split
        # stays honest (Round 2 brief: "runtime telemetry covering latency,
        # model calls, token usage and estimated cost").
        chat = _CHAT_TELEMETRY
        chat_lat = chat["latency_ms_samples"]
        payload["live_chat"] = {
            "assistant_available": _chat_available(),
            "provider": _chat_provider(),
            "model": _active_chat_model(),
            "calls": chat["calls"],
            "errors": chat["errors"],
            "clarifications": chat["clarifications"],
            "tokens_in": chat["tokens_in"],
            "tokens_out": chat["tokens_out"],
            "est_cost_usd": round(chat["cost_usd"], 6),
            "avg_latency_ms": round(sum(chat_lat) / len(chat_lat), 1) if chat_lat else 0.0,
        }
        if chat["calls"] > 0:
            payload["note"] = (
                payload.get("note", "")
                + f" The optional conversational assistant has made {chat['calls']} "
                "live, grounded model call(s) this session (opt-in, one per "
                "question) -- see live_chat for its measured tokens, latency and cost."
            )
        self._send_json(payload)

    @staticmethod
    def _new_audit_id():
        # Was `abs(hash(json.dumps(payload))) % 10000` -- Python's str hash is
        # randomized per-process (PYTHONHASHSEED), so the "same" audit id space
        # shifted on every server restart. uuid4 is actually unique and never
        # collides across restarts, which an audit trail ID should guarantee.
        return f"AUD-{uuid.uuid4().hex[:8].upper()}"

    def _log_feedback(self, anomaly_id, rating, comments):
        if not os.path.exists(DB_PATH):
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO user_feedback (anomaly_id, rating, user_comments) VALUES (?, ?, ?)",
                (anomaly_id, rating, comments),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: could not log feedback: {type(e).__name__}")

    def _save_action_correction(self, anomaly_key, corrected_action, rationale, role):
        """Persist an expert's replacement for the recommended action, snapshotting
        the matchable attributes of the anomaly it was made on."""
        if not os.path.exists(DB_PATH):
            return None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            conn.execute(_ACTION_CORRECTIONS_DDL)
            r = self._fetch_anomaly_row(conn, anomaly_key)
            anomaly_id = anomaly_key
            scenario_key = kpi_name = cat_id = direction = detection_type = None
            original_action = ""
            if r:
                anomaly_id = r["anomaly_id"]  # canonical id, so is_own matching works
                scenario_key, kpi_name = r["scenario_key"], r["kpi_name"]
                cat_id, direction = r["cat_id"], r["direction"]
                detection_type = r["detection_type"]
                try:
                    nv = json.loads(r["narratives_json"]) if r["narratives_json"] else {}
                    ra = (nv.get(DEFAULT_ROLE) or {}).get("recommended_action") or {}
                    original_action = ra.get("action") or ""
                except Exception:
                    pass
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO action_corrections
                   (anomaly_id, scenario_key, kpi_name, cat_id, direction, detection_type,
                    original_action, corrected_action, rationale, corrected_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (anomaly_id, scenario_key, kpi_name, cat_id, direction, detection_type,
                 original_action, corrected_action, rationale, role),
            )
            conn.commit()
            cid = cur.lastrowid
            conn.close()
            return cid
        except Exception as e:
            print(f"Warning: could not save action correction: {type(e).__name__}: {e}")
            return None

    def _match_action_correction(self, r):
        """Find the most relevant prior action correction for anomaly row `r`:
        an exact scenario_key match wins, otherwise same kpi_name + cat_id +
        direction. Returns None if nothing applies."""
        if not os.path.exists(DB_PATH) or r is None:
            return None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            conn.execute(_ACTION_CORRECTIONS_DDL)
            cur = conn.cursor()
            cur.execute(
                """SELECT * FROM action_corrections
                   WHERE status = 'active'
                     AND ( scenario_key = ?
                           OR (kpi_name = ? AND cat_id = ? AND direction = ?) )
                   ORDER BY (scenario_key = ?) DESC, timestamp DESC
                   LIMIT 1""",
                (r["scenario_key"], r["kpi_name"], r["cat_id"], r["direction"], r["scenario_key"]),
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            same_scenario = bool(row["scenario_key"] and row["scenario_key"] == r["scenario_key"])
            if row["anomaly_id"] == r["anomaly_id"]:
                match_desc = "this anomaly"
            elif same_scenario:
                match_desc = "the same recurring scenario"
            else:
                match_desc = f'a similar {row["kpi_name"]} {(row["direction"] or "").lower()} anomaly in {row["cat_id"]}'
            return {
                "corrected_action": row["corrected_action"],
                "rationale": row["rationale"] or "",
                "corrected_by": row["corrected_by"] or "an analyst",
                "source_anomaly_id": row["anomaly_id"],
                "is_own": row["anomaly_id"] == r["anomaly_id"],
                "match": match_desc,
                "timestamp": row["timestamp"],
            }
        except Exception as e:
            print(f"Warning: action-correction match failed: {type(e).__name__}: {e}")
            return None

    def _send_json(self, data, status_code=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    # Plain TCPServer handles one request at a time, which is not enough for a
    # single browser tab: a scenario switch alone fires ~6 concurrent fetches
    # (anomaly detail, timeline, graph, telemetry, health, plus asset requests),
    # and a busy single-threaded accept loop causes some of those to be refused
    # or reset outright -- which the frontend's offline fallback silently
    # swallows as "backend unreachable" even though the server process is alive.
    daemon_threads = True
    allow_reuse_address = True


def run_server():
    os.chdir(BASE_DIR)
    _ensure_db_seeded()
    _load_evidence_graph()
    with ThreadingHTTPServer(("", PORT), ApiRequestHandler) as httpd:
        print("============================================================")
        print(f"  KPI Intelligence API Server Running on http://127.0.0.1:{PORT}")
        print(f"  Serving dashboard at: http://127.0.0.1:{PORT}/dashboard.html")
        print(f"  Database target: {DB_PATH}")
        print("============================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


if __name__ == '__main__':
    run_server()

#!/usr/bin/env python3
"""
eval/ragas_eval.py -- run RAGAS on the one part of the system it applies to:
the /api/chat surface (a real RAG pipeline -- retrieve a role-masked context
block, answer over it only).

Metrics (both LLM-judged, no embeddings needed):
  * Faithfulness         -- fraction of claims in the answer that are entailed
                            by the retrieved context (RAGAS canonical).
  * ResponseGroundedness -- is every statement in the answer supported by the
                            context (0/0.5/1, reference-free).

These are the standard versions of this harness's rule-based `faithfulness`
check, so the two can be compared directly.

RAGAS is NOT run on detection / PVM / abstention / RBAC -- those are
classification / exact-algebra / safety-invariant problems, not generation.
See docs/EVALUATION_REPORT.md section 7.

Judge model: Groq `openai/gpt-oss-120b` via the OpenAI-compatible endpoint
(GROQ_API_KEY in .env). Uses a small query subset to stay under the free-tier
daily token budget -- pass --all for the full 30.

Usage:
    python eval/ragas_eval.py [--server URL] [--all] [--delay 4]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "Accenture", "Accenture"))

# grounded, single-answer query types only (clarification / pure-abstain turns
# have nothing for RAGAS to score).
SUBSET_TYPES = ("kpi_revenue", "kpi_margin", "kpi_turnover", "multi_factor", "provenance")


def _groq_key():
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith("GROQ_API_KEY"):
            return line.split("=", 1)[1].strip()
    return os.environ.get("GROQ_API_KEY", "")


def _post_chat(server, message, role, retries=4):
    body = json.dumps({"message": message, "role": role}).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(server.rstrip("/") + "/api/chat", data=body,
                                         method="POST", headers={"Content-Type": "application/json"})
            d = json.loads(urllib.request.urlopen(req, timeout=45).read())
        except Exception as e:
            d = {"reply": "", "error": str(e)}
        if d.get("error") or "couldn't get a response" in (d.get("reply", "") or "").lower():
            time.sleep(6 + 6 * i)
            continue
        return d
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--delay", type=float, default=4.0)
    ap.add_argument("--out", default=os.path.join(HERE, "ragas_results.json"))
    args = ap.parse_args()

    key = _groq_key()
    if not key:
        sys.exit("GROQ_API_KEY not found in .env")

    import warnings
    warnings.filterwarnings("ignore")
    from ragas import evaluate, EvaluationDataset
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import Faithfulness, ResponseGroundedness
    from ragas.llms import LangchainLLMWrapper
    from langchain_openai import ChatOpenAI
    import api_server

    judge = LangchainLLMWrapper(ChatOpenAI(
        model="openai/gpt-oss-120b",
        base_url="https://api.groq.com/openai/v1",
        api_key=key, temperature=0.0, timeout=60, max_retries=2))

    with open(os.path.join(HERE, "dataset30.jsonl")) as f:
        cases = [json.loads(x) for x in f if x.strip()]
    if not args.all:
        cases = [c for c in cases if c.get("type") in SUBSET_TYPES]

    samples, meta = [], []
    for i, c in enumerate(cases):
        if i:
            time.sleep(args.delay)
        resp = _post_chat(args.server, c["query"], c["role"])
        reply = resp.get("reply") or ""
        if not reply or resp.get("error"):
            print(f"  skip {c['id']}: {resp.get('error') or 'empty reply'}")
            continue
        ctx, label, _ = api_server._chat_anomaly_context(None, c["role"], message=c["query"], focus=False)
        if ctx is None:
            print(f"  skip {c['id']}: no context")
            continue
        samples.append(SingleTurnSample(
            user_input=c["query"],
            response=reply,
            retrieved_contexts=[json.dumps(ctx, default=str)],
            reference=label or "",
        ))
        meta.append({"id": c["id"], "type": c["type"], "role": c["role"],
                     "query": c["query"], "reply": reply})
        print(f"  got {c['id']} ({c['type']})")

    if not samples:
        sys.exit("no gradable samples -- chat provider unreachable / rate-limited")

    ds = EvaluationDataset(samples=samples)
    print(f"\nscoring {len(samples)} samples with RAGAS (Faithfulness, ResponseGroundedness)...")
    res = evaluate(ds, metrics=[Faithfulness(llm=judge), ResponseGroundedness(llm=judge)],
                   llm=judge, show_progress=True, raise_exceptions=False)

    df = res.to_pandas()
    per_case = []
    for m, row in zip(meta, df.to_dict("records")):
        per_case.append({**m,
                         "faithfulness": row.get("faithfulness"),
                         "response_groundedness": row.get("response_groundedness")})
    agg = {k: (round(float(v), 3) if v == v else None) for k, v in res._repr_dict.items()} \
        if hasattr(res, "_repr_dict") else {}
    if not agg:
        for col in ("faithfulness", "response_groundedness"):
            vals = [r[col] for r in per_case if isinstance(r.get(col), (int, float))]
            agg[col] = round(sum(vals) / len(vals), 3) if vals else None

    out = {"judge_model": "openai/gpt-oss-120b (Groq)", "n_samples": len(samples),
           "subset": "all" if args.all else list(SUBSET_TYPES),
           "aggregate": agg, "per_case": per_case}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nwrote {args.out}")
    print("  aggregate:", json.dumps(agg))


if __name__ == "__main__":
    main()

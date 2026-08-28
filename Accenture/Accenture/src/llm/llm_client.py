"""
src/llm/llm_client.py
Minimal, cost-controlled OpenAI wrapper.

Design constraints (deliberate, see docs/TECH_APPENDIX.md):
  - The LLM is NEVER the source of quantitative truth. It only polishes
    narrative language around numbers that were already computed
    deterministically by anomaly_detector / pvm_analyzer / evidence_reconciler.
  - Called at most ONCE per anomaly (both personas requested in a single
    prompt/response) during offline data seeding -- never on the live
    request path -- so judging-time/demo-time behavior has zero external
    dependency and zero added cost or latency.
  - If OPENAI_API_KEY is absent, or the call fails for any reason, this
    module returns success=False and the caller falls back to the fully
    deterministic template engine. The pipeline must work with zero LLM
    calls at all times.
  - The API key is read from the environment only. It is never logged,
    printed, returned, or written to any file.
"""

import json
import os
import time

MODEL = "gpt-4o-mini"

# Per-1M-token pricing for gpt-4o-mini (USD), used only to report real cost telemetry.
_PRICE_PER_1M_INPUT = 0.150
_PRICE_PER_1M_OUTPUT = 0.600


def is_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1_000_000.0) * _PRICE_PER_1M_INPUT + (tokens_out / 1_000_000.0) * _PRICE_PER_1M_OUTPUT


def generate_json(system_prompt: str, user_prompt: str, max_tokens: int = 900) -> dict:
    """
    Makes exactly one chat completion call and expects a JSON object back.
    Returns a dict with keys: success, content (parsed dict or None),
    tokens_in, tokens_out, cost_usd, latency_s, error (str or None).
    Never raises -- all failures are captured in the return value so the
    caller can deterministically fall back.
    """
    result = {
        "success": False,
        "content": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "latency_s": 0.0,
        "model": MODEL,
        "error": None,
    }

    if not is_available():
        result["error"] = "OPENAI_API_KEY not set"
        return result

    try:
        from openai import OpenAI
    except ImportError:
        result["error"] = "openai package not installed"
        return result

    start = time.perf_counter()
    try:
        client = OpenAI()  # reads key from env internally; never touched by our code
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        elapsed = time.perf_counter() - start

        raw_text = response.choices[0].message.content
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0

        parsed = json.loads(raw_text)

        result.update({
            "success": True,
            "content": parsed,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": _estimate_cost(tokens_in, tokens_out),
            "latency_s": elapsed,
        })
        return result
    except Exception as e:
        # Sanitized failure message only -- never echo request/response internals
        # that could carry auth headers or raw payload content.
        result["error"] = f"{type(e).__name__} during LLM call"
        result["latency_s"] = time.perf_counter() - start
        return result

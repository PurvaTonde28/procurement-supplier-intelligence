"""
LLM router: two-tier cache (exact hash -> semantic similarity) in front of
Groq (primary) with Gemini (fallback on error/rate-limit). Every call —
cache hit or real API call — logs to agent_runs with a real cost estimate.
"""
import os
import time
import uuid
import hashlib
from dotenv import load_dotenv
load_dotenv(override=True)

from groq import Groq
import google.generativeai as genai
from sqlalchemy import text as sql_text

from app.retrieval.embeddings import embed_text, to_pgvector_literal

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "models/gemini-flash-latest"

SEMANTIC_CACHE_THRESHOLD = 0.75     # calibrated empirically: true paraphrases ~0.79-0.80,
                                    # unrelated questions ~0.65-0.66 on gemini-embedding-001
                                    # for short factual queries. See README for methodology.

# Approximate published per-1M-token pricing, used to log a realistic cost
# estimate even though these specific calls run on free tiers.
COST_PER_1M_TOKENS = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "models/gemini-flash-latest": {"input": 0.075, "output": 0.30},
}


def _normalize(prompt: str) -> str:
    return " ".join(prompt.strip().lower().split())


def _query_hash(tenant_id: str, model: str, prompt: str) -> str:
    raw = f"{tenant_id}:{model}:{_normalize(prompt)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = COST_PER_1M_TOKENS.get(model)
    if not rates:
        return 0.0
    return round((input_tokens / 1_000_000) * rates["input"] + (output_tokens / 1_000_000) * rates["output"], 6)


def _log_run(conn, tenant_id, agent_name, run_id, model, input_tokens, output_tokens, latency_ms, status, cost, error_message=None):
    conn.execute(sql_text("""
        insert into agent_runs (tenant_id, agent_name, run_id, input_tokens, output_tokens,
                                 estimated_cost_usd, latency_ms, status, error_message)
        values (:t, :a, :r, :it, :ot, :cost, :lat, :s, :err)
    """), {"t": tenant_id, "a": f"{agent_name}:{model}", "r": run_id, "it": input_tokens,
            "ot": output_tokens, "cost": cost, "lat": latency_ms, "s": status, "err": error_message})


def _check_exact_cache(conn, tenant_id, model, prompt):
    qhash = _query_hash(tenant_id, model, prompt)
    row = conn.execute(sql_text("""
        select id, response_text from llm_cache where tenant_id = :t and query_hash = :h
    """), {"t": tenant_id, "h": qhash}).fetchone()
    if row:
        conn.execute(sql_text("update llm_cache set hit_count = hit_count + 1 where id = :id"), {"id": row.id})
        return row.response_text
    return None


def _check_semantic_cache(conn, tenant_id, prompt):
    embedding = embed_text(prompt, task_type="retrieval_query")
    emb_literal = to_pgvector_literal(embedding)
    row = conn.execute(sql_text("""
        select id, response_text, 1 - (query_embedding <=> CAST(:emb AS vector)) as similarity
        from llm_cache
        where tenant_id = :t and query_embedding is not null
        order by similarity desc
        limit 1
    """), {"emb": emb_literal, "t": tenant_id}).fetchone()

    if row and row.similarity >= SEMANTIC_CACHE_THRESHOLD:
        conn.execute(sql_text("update llm_cache set hit_count = hit_count + 1 where id = :id"), {"id": row.id})
        return row.response_text, row.similarity
    return None, (row.similarity if row else None)


def _store_cache(conn, tenant_id, model, prompt, response_text):
    qhash = _query_hash(tenant_id, model, prompt)
    embedding = embed_text(prompt, task_type="retrieval_document")
    emb_literal = to_pgvector_literal(embedding)
    conn.execute(sql_text("""
        insert into llm_cache (tenant_id, query_hash, query_embedding, response_text, model_used)
        values (:t, :h, CAST(:emb AS vector), :resp, :m)
        on conflict (tenant_id, query_hash) do update set response_text = excluded.response_text, hit_count = 0
    """), {"t": tenant_id, "h": qhash, "emb": emb_literal, "resp": response_text, "m": model})


def _call_groq(prompt: str):
    start = time.time()
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    latency_ms = int((time.time() - start) * 1000)
    text = response.choices[0].message.content
    return text, response.usage.prompt_tokens, response.usage.completion_tokens, latency_ms


def _call_gemini(prompt: str):
    start = time.time()
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(prompt)
    latency_ms = int((time.time() - start) * 1000)
    text = response.text
    usage = response.usage_metadata
    return text, usage.prompt_token_count, usage.candidates_token_count, latency_ms


def call_llm(conn, tenant_id: str, agent_name: str, prompt: str, use_cache: bool = True) -> dict:
    """Returns {"text": str, "source": "cache_exact"|"cache_semantic"|"groq"|"gemini", "cost_usd": float}"""

    if use_cache:
        cached = _check_exact_cache(conn, tenant_id, GROQ_MODEL, prompt)
        if cached is not None:
            return {"text": cached, "source": "cache_exact", "cost_usd": 0.0}

        cached, similarity = _check_semantic_cache(conn, tenant_id, prompt)
        if cached is not None:
            return {"text": cached, "source": "cache_semantic", "cost_usd": 0.0, "similarity": similarity}

    run_id = uuid.uuid4()

    try:
        text, in_tok, out_tok, latency = _call_groq(prompt)
        cost = _estimate_cost(GROQ_MODEL, in_tok, out_tok)
        _log_run(conn, tenant_id, agent_name, run_id, GROQ_MODEL, in_tok, out_tok, latency, "SUCCESS", cost)
        if use_cache:
            _store_cache(conn, tenant_id, GROQ_MODEL, prompt, text)
        return {"text": text, "source": "groq", "cost_usd": cost}

    except Exception as groq_error:
        _log_run(conn, tenant_id, agent_name, run_id, GROQ_MODEL, None, None, None,
                  "RATE_LIMITED" if "429" in str(groq_error) else "ERROR", 0.0, str(groq_error))

        try:
            run_id_fallback = uuid.uuid4()
            text, in_tok, out_tok, latency = _call_gemini(prompt)
            cost = _estimate_cost(GEMINI_MODEL, in_tok, out_tok)
            _log_run(conn, tenant_id, agent_name, run_id_fallback, GEMINI_MODEL, in_tok, out_tok, latency, "SUCCESS", cost)
            if use_cache:
                _store_cache(conn, tenant_id, GEMINI_MODEL, prompt, text)
            return {"text": text, "source": "gemini_fallback", "cost_usd": cost}

        except Exception as gemini_error:
            run_id_final = uuid.uuid4()
            _log_run(conn, tenant_id, agent_name, run_id_final, GEMINI_MODEL, None, None, None,
                      "ERROR", 0.0, str(gemini_error))
            raise RuntimeError(f"Both Groq and Gemini failed. Groq: {groq_error} | Gemini: {gemini_error}")
import os
import time
import uuid
from dotenv import load_dotenv
load_dotenv(override=True)

import google.generativeai as genai
from sqlalchemy import text as sql_text

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
EMBED_MODEL = "models/gemini-embedding-001"
EMBED_DIMENSIONS = 768


def embed_text(text: str, task_type: str = "retrieval_document") -> list[float]:
    result = genai.embed_content(
        model=EMBED_MODEL,
        content=text,
        task_type=task_type,
        output_dimensionality=EMBED_DIMENSIONS,
    )
    return result["embedding"]


def to_pgvector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


def log_embedding_run(conn, tenant_id, run_id, status, latency_ms, error_message=None):
    conn.execute(sql_text("""
        insert into agent_runs (tenant_id, agent_name, run_id, latency_ms, status, error_message, estimated_cost_usd)
        values (:t, 'embedder', :r, :lat, :s, :err, 0.0)
    """), {"t": tenant_id, "r": run_id, "lat": latency_ms, "s": status, "err": error_message})
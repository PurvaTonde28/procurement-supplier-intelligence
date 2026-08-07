"""Thin LLM rerank layer: takes the SQL hybrid-search top-K, returns a
validated, cited top-3 with a rationale for each. The SQL does retrieval;
the LLM does relevance judgment and citation framing."""
import os
import json
import time
import uuid
from groq import Groq
from pydantic import BaseModel, Field
from typing import List
from sqlalchemy import text as sql_text

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = "llama-3.3-70b-versatile"


class CitedResult(BaseModel):
    contract_number: str
    page_number: int
    relevant_excerpt: str = Field(description="Short quote or paraphrase, under 40 words")
    relevance_reason: str


class RerankOutput(BaseModel):
    results: List[CitedResult]


RERANK_PROMPT = """You are ranking contract clauses by relevance to a question.
Given the question and up to 8 candidate chunks (each with a contract number and page number),
select the TOP 3 most relevant, and for each explain briefly why it's relevant.

Return ONLY valid JSON matching this shape:
{{
  "results": [
    {{"contract_number": "string", "page_number": int, "relevant_excerpt": "string", "relevance_reason": "string"}}
  ]
}}

Question: {question}

Candidates:
{candidates}
"""


def rerank_top3(conn, tenant_id: str, question: str, candidates: list[dict]) -> RerankOutput:
    candidates_text = "\n\n".join(
        f"[Contract {c['contract_number']}, Page {c['page_number']}]: {c['chunk_text']}"
        for c in candidates
    )
    prompt = RERANK_PROMPT.format(question=question, candidates=candidates_text)
    run_id = uuid.uuid4()
    start = time.time()

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    latency_ms = int((time.time() - start) * 1000)
    parsed = json.loads(response.choices[0].message.content)
    validated = RerankOutput(**parsed)

    conn.execute(sql_text("""
        insert into agent_runs (tenant_id, agent_name, run_id, input_tokens, output_tokens, latency_ms, status, estimated_cost_usd)
        values (:t, 'reranker', :r, :it, :ot, :lat, 'SUCCESS', 0.0)
    """), {"t": tenant_id, "r": run_id, "it": response.usage.prompt_tokens,
           "ot": response.usage.completion_tokens, "lat": latency_ms})

    return validated
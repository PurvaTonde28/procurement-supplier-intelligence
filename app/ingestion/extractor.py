"""
Validated extraction pipeline: PDF text -> LLM -> Pydantic schema,
with retry-on-validation-failure and full run logging to agent_runs.
"""
import os
from dotenv import load_dotenv
load_dotenv(override=True)
import json
import time
import uuid
from groq import Groq
from pydantic import ValidationError
from sqlalchemy import text as sql_text

from app.models.schemas import ContractExtraction, InvoiceExtraction

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = "llama-3.3-70b-versatile"  # check console.groq.com/docs/models for current availability
MAX_RETRIES = 3

CONTRACT_PROMPT = """You are a precise data extraction system. Extract structured data from this contract text.
Return ONLY valid JSON matching this shape, no markdown, no explanation:
{{
  "contract_number": "string",
  "supplier_name": "string",
  "items": [{{"item_sku": "string", "item_description": "string or null", "agreed_unit_price": number}}],
  "payment_terms": "string or null"
}}

Contract text:
{content}"""

INVOICE_PROMPT = """You are a precise data extraction system. Extract structured data from this invoice text.
Return ONLY valid JSON matching this shape, no markdown, no explanation:
{{
  "invoice_number": "string",
  "supplier_name": "string",
  "item_sku": "string",
  "quantity_billed": integer,
  "invoice_unit_price": number,
  "total_amount": number,
  "invoice_date": "YYYY-MM-DD"
}}

Invoice text:
{content}"""


def _log_run(conn, tenant_id, agent_name, run_id, input_tokens, output_tokens, latency_ms, status, error_message=None):
    conn.execute(sql_text("""
        insert into agent_runs (tenant_id, agent_name, run_id, input_tokens, output_tokens,
                                 estimated_cost_usd, latency_ms, status, error_message)
        values (:t, :a, :r, :it, :ot, :cost, :lat, :s, :err)
    """), {
        "t": tenant_id, "a": agent_name, "r": run_id,
        "it": input_tokens, "ot": output_tokens,
        # Groq free-tier llama models: treat as $0 for now, real pricing wired in Phase 6 router
        "cost": 0.0,
        "lat": latency_ms, "s": status, "err": error_message
    })


def _call_llm(prompt: str) -> tuple[str, int, int, int]:
    start = time.time()
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    latency_ms = int((time.time() - start) * 1000)
    raw = response.choices[0].message.content
    usage = response.usage
    return raw, usage.prompt_tokens, usage.completion_tokens, latency_ms


def extract_with_retry(conn, tenant_id: str, content: str, schema_type: str):
    """schema_type: 'contract' or 'invoice'. Returns a validated Pydantic instance or raises."""
    assert schema_type in ("contract", "invoice")
    base_prompt = CONTRACT_PROMPT if schema_type == "contract" else INVOICE_PROMPT
    schema_cls = ContractExtraction if schema_type == "contract" else InvoiceExtraction
    run_id = uuid.uuid4()

    last_error = None
    prompt = base_prompt.format(content=content)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw, in_tok, out_tok, latency = _call_llm(prompt)
            parsed = json.loads(raw)
            parsed["tenant_id"] = tenant_id  # injected, never trusted from LLM output
            validated = schema_cls(**parsed)

            _log_run(conn, tenant_id, f"extractor:{schema_type}", run_id, in_tok, out_tok, latency, "SUCCESS")
            return validated

        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)
            _log_run(conn, tenant_id, f"extractor:{schema_type}", run_id,
                      in_tok if 'in_tok' in dir() else None,
                      out_tok if 'out_tok' in dir() else None,
                      latency if 'latency' in dir() else None,
                      "ERROR", error_message=f"attempt {attempt}: {last_error}")

            # Feed the validation error back so the retry is corrective, not blind
            prompt = base_prompt.format(content=content) + f"\n\nYour previous attempt failed validation with this error:\n{last_error}\nFix the JSON and try again."

        except Exception as e:
            last_error = str(e)
            _log_run(conn, tenant_id, f"extractor:{schema_type}", run_id, None, None, None,
                      "ERROR", error_message=f"attempt {attempt} (unexpected): {last_error}")

    raise ValueError(f"Extraction failed after {MAX_RETRIES} attempts. Last error: {last_error}")
"""Natural language -> SQL -> results, executed against a dedicated
read-only DB role (sql_agent_ro) with RLS still enforced via app.tenant_id.
Two independent safety layers: DB-level (role grants) and app-level
(guardrails.py) — neither trusts the other."""
import os
import json
import uuid
import time
from dotenv import load_dotenv
load_dotenv(override=True)

from sqlalchemy import create_engine, text as sql_text

from app.sql_agent.schema_context import SCHEMA_CONTEXT
from app.sql_agent.guardrails import validate_and_limit, SQLSafetyError
from app.llm.router import call_llm

_ro_engine = create_engine(os.getenv("SQL_AGENT_DATABASE_URL"))

SQL_GENERATION_PROMPT = """{schema}

Given the question below, write a single PostgreSQL SELECT query to answer it.
Return ONLY the raw SQL, no markdown, no explanation, no semicolon.

Question: {question}

SQL:"""


def generate_sql(conn, tenant_id: str, question: str) -> str:
    prompt = SQL_GENERATION_PROMPT.format(schema=SCHEMA_CONTEXT, question=question)
    result = call_llm(conn, tenant_id, "sql_agent:generate", prompt, use_cache=False)  # changed
    raw_sql = result["text"].strip()
    raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
    return raw_sql


def run_query(main_conn, tenant_id: str, question: str) -> dict:
    """main_conn is the app_user connection, used only for call_llm's
    logging/caching. The actual query runs on the separate read-only role."""
    run_id = uuid.uuid4()
    start = time.time()

    raw_sql = generate_sql(main_conn, tenant_id, question)

    try:
        safe_sql = validate_and_limit(raw_sql)
    except SQLSafetyError as e:
        main_conn.execute(sql_text("""
            insert into agent_runs (tenant_id, agent_name, run_id, status, error_message, estimated_cost_usd)
            values (:t, 'sql_agent:blocked', :r, 'ERROR', :err, 0.0)
        """), {"t": tenant_id, "r": run_id, "err": f"{e} | generated_sql={raw_sql}"})
        main_conn.commit()
        return {"question": question, "generated_sql": raw_sql, "error": str(e), "rows": []}

    try:
        with _ro_engine.connect() as ro_conn:
            ro_conn.execute(sql_text("select set_config('app.tenant_id', :t, false)"), {"t": tenant_id})
            result = ro_conn.execute(sql_text(safe_sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]

        latency_ms = int((time.time() - start) * 1000)
        main_conn.execute(sql_text("""
            insert into agent_runs (tenant_id, agent_name, run_id, status, latency_ms, estimated_cost_usd)
            values (:t, 'sql_agent:execute', :r, 'SUCCESS', :lat, 0.0)
        """), {"t": tenant_id, "r": run_id, "lat": latency_ms})
        main_conn.commit()

        return {"question": question, "generated_sql": safe_sql, "row_count": len(rows), "rows": rows[:20]}

    except Exception as e:
        main_conn.execute(sql_text("""
            insert into agent_runs (tenant_id, agent_name, run_id, status, error_message, estimated_cost_usd)
            values (:t, 'sql_agent:execute', :r, 'ERROR', :err, 0.0)
        """), {"t": tenant_id, "r": run_id, "err": f"{e} | sql={safe_sql}"})
        main_conn.commit()
        return {"question": question, "generated_sql": safe_sql, "error": str(e), "rows": []}
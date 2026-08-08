import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from app.llm.router import call_llm

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

    conn.execute(text("delete from llm_cache where tenant_id = :t"), {"t": tenant_id})
    conn.commit()

    print("=== Call 1: fresh prompt (expect source=groq) ===")
    print(call_llm(conn, str(tenant_id), "test_agent", "What is the capital of France?"))
    conn.commit()

    print("\n=== Call 2: paraphrase (expect source=cache_semantic now) ===")
    print(call_llm(conn, str(tenant_id), "test_agent", "Which city is the capital of France?"))
    conn.commit()

    print("\n=== Call 3: genuinely different (expect source=groq) ===")
    print(call_llm(conn, str(tenant_id), "test_agent", "What is the capital of Japan?"))
    conn.commit()
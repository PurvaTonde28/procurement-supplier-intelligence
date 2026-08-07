from dotenv import load_dotenv
load_dotenv(override=True)
import os
import glob
from sqlalchemy import create_engine, text
from app.ingestion.pdf_parser import parse_pdf_text
from app.ingestion.extractor import extract_with_retry


engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

    print("=== Contract Extraction ===")
    for path in glob.glob("data/synthetic/contracts/*.pdf"):
        content = parse_pdf_text(path)
        try:
            result = extract_with_retry(conn, str(tenant_id), content, "contract")
            conn.commit()
            print(f"✅ {path}\n   {result.model_dump()}\n")
        except ValueError as e:
            conn.commit()
            print(f"❌ {path}: {e}\n")

    print("=== Invoice Extraction ===")
    for path in glob.glob("data/synthetic/invoices/*.pdf"):
        content = parse_pdf_text(path)
        try:
            result = extract_with_retry(conn, str(tenant_id), content, "invoice")
            conn.commit()
            print(f"✅ {path}\n   {result.model_dump()}\n")
        except ValueError as e:
            conn.commit()
            print(f"❌ {path}: {e}\n")

    print("=== agent_runs log for this session ===")
    rows = conn.execute(text("""
        select agent_name, status, input_tokens, output_tokens, latency_ms
        from agent_runs where tenant_id = :t order by created_at
    """), {"t": tenant_id}).fetchall()
    for r in rows:
        print(r)
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from app.reconciliation.engine import run_reconciliation_for_tenant

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenants = conn.execute(text("select id, name from tenants")).fetchall()
    for t in tenants:
        flagged_count = run_reconciliation_for_tenant(conn, t.id)
        conn.commit()
        print(f"{t.name}: {flagged_count} invoices flagged")

print("\n✅ Reconciliation complete.")
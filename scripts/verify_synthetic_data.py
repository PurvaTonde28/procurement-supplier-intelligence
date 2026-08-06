import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenants = conn.execute(text("select id, name from tenants")).fetchall()
    for t in tenants:
        conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(t.id)})
        s = conn.execute(text("select count(*) from suppliers")).scalar()
        c = conn.execute(text("select count(*) from contracts")).scalar()
        ci = conn.execute(text("select count(*) from contract_items")).scalar()
        po = conn.execute(text("select count(*) from purchase_orders")).scalar()
        inv = conn.execute(text("select count(*) from invoices")).scalar()
        print(f"{t.name}: {s} suppliers, {c} contracts, {ci} contract_items, {po} POs, {inv} invoices")
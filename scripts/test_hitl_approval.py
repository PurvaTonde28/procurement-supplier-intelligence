import os
import uuid
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agents.graph import build_graph

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))


def run_scenario(conn, app, tenant_id, label, decision):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print(f"\n=== Scenario: {label} ===")
    result = app.invoke(
        {"messages": [HumanMessage(content="Draft an email to Apex Packaging about the price overcharge on extra large crates.")],
         "tenant_id": str(tenant_id)},
        config=config,
    )

    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"⏸️  Interrupted. Draft presented for review:\n{payload['draft']}\n")

        result = app.invoke(Command(resume=decision), config=config)
        print(f"Final: {result['messages'][-1].content}")
    else:
        print("⚠️  No interrupt fired — check routing/graph wiring.")


with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

    # Reset audit_log for this tenant so each run's output is self-contained
    # and not confused with leftover rows from prior test runs
    conn.execute(text("delete from audit_log where tenant_id = :t"), {"t": tenant_id})
    conn.commit()
    
    app = build_graph(conn)

    run_scenario(conn, app, tenant_id, "APPROVE as-is",
                 {"action": "approve", "actor": "priya@meridian.example"})

    run_scenario(conn, app, tenant_id, "APPROVE with edits",
                 {"action": "edit", "edited_text": "Dear Apex Packaging team,\n\nWe've identified a billing discrepancy on Extra Large Crates (PKG-BOX-XL) — invoiced at a higher rate than our contracted price. Please review and confirm correction within 5 business days.\n\nRegards,\nProcurement Team",
                  "actor": "priya@meridian.example"})

    run_scenario(conn, app, tenant_id, "REJECT",
                 {"action": "reject", "actor": "priya@meridian.example"})

    print("\n=== audit_log for this session ===")
    rows = conn.execute(text("""
        select entity_type, action, actor, content_hash, created_at
        from audit_log where tenant_id = :t order by created_at
    """), {"t": tenant_id}).fetchall()
    for r in rows:
        print(r)
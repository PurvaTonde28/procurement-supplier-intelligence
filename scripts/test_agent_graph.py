import os
import uuid
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

    app = build_graph(conn)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print("=== Turn 1: anomaly question ===")
    result = app.invoke(
        {"messages": [HumanMessage(content="What invoices are currently flagged for leakage?")],
         "tenant_id": str(tenant_id)},
        config=config,
    )
    print(result["messages"][-1].content)

    print("\n=== Turn 2: supplier intel question (same thread — tests memory) ===")
    result = app.invoke(
        {"messages": [HumanMessage(content="What's the contracted price for extra large shipping crates?")],
         "tenant_id": str(tenant_id)},
        config=config,
    )
    print(result["messages"][-1].content)

    print("\n=== Turn 3: negotiation drafting ===")
    result = app.invoke(
        {"messages": [HumanMessage(content="Draft an email to Apex Packaging about the price overcharge on extra large crates.")],
         "tenant_id": str(tenant_id)},
        config=config,
    )
    print(result["messages"][-1].content)
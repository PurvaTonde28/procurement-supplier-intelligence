import asyncio
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from app.mcp_server.server import list_tools, call_tool

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

async def main():
    with engine.connect() as conn:
        tenant_id = str(conn.execute(text("select id from tenants limit 1")).scalar())

    tools = await list_tools()
    print("=== Registered MCP tools ===")
    for t in tools:
        print(f"  {t.name}: {t.description}")

    print("\n=== Calling get_flagged_invoices ===")
    result = await call_tool("get_flagged_invoices", {"tenant_id": tenant_id, "limit": 3})
    print(result[0].text)

    print("\n=== Calling search_contract_clauses ===")
    result = await call_tool("search_contract_clauses", {"tenant_id": tenant_id, "question": "What is the price for extra large crates?"})
    print(result[0].text)

    print("\n=== Calling ask_procurement_data ===")
    result = await call_tool("ask_procurement_data", {"tenant_id": tenant_id, "question": "How many suppliers are there?"})
    print(result[0].text)

asyncio.run(main())
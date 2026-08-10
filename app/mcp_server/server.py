"""MCP server exposing core procurement-intelligence tools. Wraps existing
Phase 3/5/9 logic — no reimplementation. Run standalone via stdio for use
with Claude Desktop or any MCP-compatible host."""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv(override=True)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from sqlalchemy import create_engine, text as sql_text

from app.reconciliation.engine import run_reconciliation_for_tenant
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.rerank import rerank_top3
from app.sql_agent.query_agent import run_query

engine = create_engine(os.getenv("DATABASE_URL"))
server = Server("procurement-supplier-intelligence")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_flagged_invoices",
            description="Returns invoices currently flagged as LEAKAGE_DETECTED for a tenant.",
            inputSchema={
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
                "required": ["tenant_id"],
            },
        ),
        Tool(
            name="search_contract_clauses",
            description="Searches contract text for clauses relevant to a question, with citations.",
            inputSchema={
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}, "question": {"type": "string"}},
                "required": ["tenant_id", "question"],
            },
        ),
        Tool(
            name="run_reconciliation",
            description="Re-runs the deterministic reconciliation engine for a tenant.",
            inputSchema={
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}},
                "required": ["tenant_id"],
            },
        ),
        Tool(
            name="ask_procurement_data",
            description="Ask a natural-language question over the procurement database (read-only, guardrailed SQL agent).",
            inputSchema={
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}, "question": {"type": "string"}},
                "required": ["tenant_id", "question"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    with engine.connect() as conn:
        tenant_id = arguments["tenant_id"]
        conn.execute(sql_text("select set_config('app.tenant_id', :t, false)"), {"t": tenant_id})

        if name == "get_flagged_invoices":
            limit = arguments.get("limit", 10)
            rows = conn.execute(sql_text("""
                select i.invoice_number, s.name as supplier_name, rr.check_type, rr.severity
                from invoices i
                join reconciliation_results rr on rr.invoice_id = i.id
                join suppliers s on s.id = i.supplier_id
                where i.tenant_id = :t and i.reconciliation_status = 'LEAKAGE_DETECTED'
                limit :lim
            """), {"t": tenant_id, "lim": limit}).fetchall()
            text = "\n".join(f"{r.invoice_number} | {r.supplier_name} | {r.check_type} | {r.severity}" for r in rows) or "No flagged invoices."
            return [TextContent(type="text", text=text)]

        elif name == "search_contract_clauses":
            candidates = hybrid_search(conn, tenant_id, arguments["question"], top_k=8)
            if not candidates:
                return [TextContent(type="text", text="No contract clauses found.")]
            result = rerank_top3(conn, tenant_id, arguments["question"], candidates)
            conn.commit()
            text = "\n\n".join(f"[{r.contract_number}, Page {r.page_number}]: {r.relevant_excerpt}" for r in result.results)
            return [TextContent(type="text", text=text)]

        elif name == "run_reconciliation":
            flagged_count = run_reconciliation_for_tenant(conn, tenant_id)
            conn.commit()
            return [TextContent(type="text", text=f"Reconciliation complete. {flagged_count} invoices flagged.")]

        elif name == "ask_procurement_data":
            result = run_query(conn, tenant_id, arguments["question"])
            if "error" in result:
                return [TextContent(type="text", text=f"Blocked/failed: {result['error']}")]
            return [TextContent(type="text", text=f"SQL: {result['generated_sql']}\nRows: {result['rows']}")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
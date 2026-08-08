"""
Supervisor + 3 sub-agent LangGraph orchestration:
- Supplier Intelligence Agent (RAG + supplier lookups)
- Anomaly/Leakage Agent (reconciliation engine + flagged invoices)
- Negotiation Drafting Agent (email drafts, no send action here — HITL is Phase 8)
Postgres-backed checkpointing gives cross-turn memory per thread_id.
"""
import os
from functools import partial
from dotenv import load_dotenv
load_dotenv(override=True)

from psycopg_pool import ConnectionPool
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.agents import create_agent

from app.agents.state import AgentState
from app.agents.tools import make_tools, make_negotiation_tool
from app.llm.router import call_llm

# Module-level pool: created once, reused across every build_graph() call
# in this process. Avoids the from_conn_string generator-CM lifetime issue
# (that context manager's underlying connection could get closed out from
# under us once the enclosing function scope changed).
_pool = ConnectionPool(
    conninfo=os.getenv("DATABASE_URL"),
    max_size=10,
    kwargs={"autocommit": True, "prepare_threshold": 0},
)

SUPERVISOR_PROMPT = """You are a routing supervisor for a procurement intelligence system.
Given the conversation so far, decide which specialist should handle the LATEST user message.

Respond with EXACTLY ONE of these labels, nothing else:
- supplier_intel   (questions about suppliers, contracts, contract clauses, pricing terms)
- anomaly          (questions about flagged invoices, leakage, running reconciliation, anomalies)
- negotiation      (requests to draft an email, dispute, or outreach to a supplier)
- end              (the conversation is answered/complete, no further tool use needed)

Conversation:
{conversation}

Label:"""

VALID_LABELS = {"supplier_intel", "anomaly", "negotiation", "end"}


def _supervisor_node(state: AgentState, conn) -> dict:
    """Routes to a sub-agent based on a constrained-label LLM classification.
    Fails closed to 'end' on ANY error (malformed LLM output, API failure,
    missing state) rather than ever crashing the graph — a router should
    degrade gracefully, not take down the whole request."""
    label = "end"
    try:
        conversation = "\n".join(f"{m.type}: {m.content}" for m in state["messages"][-6:])
        prompt = SUPERVISOR_PROMPT.format(conversation=conversation)
        result = call_llm(conn, state["tenant_id"], "supervisor", prompt, use_cache=False)
        conn.commit()

        candidate = result["text"].strip().lower()
        if candidate in VALID_LABELS:
            label = candidate
        else:
            print(f"⚠️  Supervisor returned an unrecognized label '{candidate}', defaulting to 'end'")
    except Exception as e:
        print(f"⚠️  Supervisor routing failed, defaulting to 'end': {e}")

    return {"next_agent": label}


def _route(state: AgentState) -> str:
    """Defensive read: never assume next_agent was set, even though
    _supervisor_node always returns it — protects against any future node
    ordering/timing edge case in LangGraph's execution model."""
    return state.get("next_agent", "end")


def build_graph(conn):
    """conn is a live SQLAlchemy connection for tool/DB access (tenant-scoped
    business logic). The LangGraph checkpointer uses its OWN separate psycopg
    connection pool (_pool above), since checkpointing needs psycopg (v3),
    not SQLAlchemy's psycopg2 driver."""

    def supplier_intel_node(state: AgentState) -> dict:
        tools = make_tools(conn, state["tenant_id"])
        agent = create_agent(
            model="groq:llama-3.3-70b-versatile",
            tools=tools,
            system_prompt="You are a Supplier Intelligence agent. Use tools to answer questions "
                   "about suppliers, contracts, and pricing terms. Cite contract numbers "
                   "and page numbers when referencing clauses.",
        )
        result = agent.invoke({"messages": state["messages"]})
        return {"messages": result["messages"][-1:]}

    def anomaly_node(state: AgentState) -> dict:
        tools = make_tools(conn, state["tenant_id"])
        agent = create_agent(
            model="groq:llama-3.3-70b-versatile",
            tools=tools,
            system_prompt="You are an Anomaly/Leakage agent. Use tools to check flagged invoices "
                   "and run reconciliation. Always state check_type and severity when "
                   "reporting a flag. Never invent numbers — only report what the tools return.",
        )
        result = agent.invoke({"messages": state["messages"]})
        return {"messages": result["messages"][-1:]}

    def negotiation_node(state: AgentState) -> dict:
        tools = make_negotiation_tool(conn, state["tenant_id"])
        agent = create_agent(
            model="groq:llama-3.3-70b-versatile",
            tools=tools,
            system_prompt="You are a Negotiation Drafting agent. Draft supplier outreach emails "
                   "about billing issues. Always state clearly that this draft requires "
                   "human approval before sending — you cannot send anything yourself.",
        )
        result = agent.invoke({"messages": state["messages"]})
        return {"messages": result["messages"][-1:]}

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", partial(_supervisor_node, conn=conn))
    graph.add_node("supplier_intel", supplier_intel_node)
    graph.add_node("anomaly", anomaly_node)
    graph.add_node("negotiation", negotiation_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges("supervisor", _route, {
        "supplier_intel": "supplier_intel",
        "anomaly": "anomaly",
        "negotiation": "negotiation",
        "end": END,
    })
    graph.add_edge("supplier_intel", END)
    graph.add_edge("anomaly", END)
    graph.add_edge("negotiation", END)

    checkpointer = PostgresSaver(_pool)
    # Tables already created via scripts/setup_checkpoint_tables.py (admin role).
    # app_user has read/write grants on them, no CREATE privilege needed here.

    return graph.compile(checkpointer=checkpointer)
"""
Supervisor + 3 sub-agent LangGraph orchestration, with a human-in-the-loop
approval gate on negotiation drafts (Phase 8).

Phase 8 fix: all create_agent() calls now use app.agents.model.agent_model
(Groq -> Gemini fallback) instead of the raw "groq:model-name" shorthand,
which had no fallback and hard-crashed once Groq's daily token quota was
exhausted. The supervisor's fail-closed exception path now also logs to
agent_runs, so a total routing failure leaves a durable trace instead of
silently defaulting to "end" with no record.
"""
import os
import uuid
from functools import partial
from dotenv import load_dotenv
load_dotenv(override=True)

from psycopg_pool import ConnectionPool
from sqlalchemy import text as sql_text
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt
from langchain_core.messages import AIMessage
from langchain.agents import create_agent

from app.agents.state import AgentState
from app.agents.tools import make_tools, make_negotiation_tool
from app.agents.audit import log_decision
from app.agents.model import agent_model
from app.llm.router import call_llm

_pool = ConnectionPool(
    conninfo=os.getenv("DATABASE_URL"),
    max_size=10,
    kwargs={"autocommit": True, "prepare_threshold": 0},
)

SUPERVISOR_PROMPT = """You are a routing supervisor for a procurement intelligence system.
Given the conversation so far, decide which specialist should handle the LATEST user message.

Respond with EXACTLY ONE WORD from this list, with no punctuation, no bullet points, no explanation:
supplier_intel
anomaly
negotiation
end

- supplier_intel: questions about suppliers, contracts, contract clauses, pricing terms
- anomaly: questions about flagged invoices, leakage, running reconciliation, anomalies
- negotiation: requests to draft an email, dispute, or outreach to a supplier
- end: the conversation is answered/complete, no further tool use needed

Conversation:
{conversation}

Answer with exactly one word:"""

VALID_LABELS = {"supplier_intel", "anomaly", "negotiation", "end"}


def _supervisor_node(state: AgentState, conn) -> dict:
    """Routes to a sub-agent based on a constrained-label LLM classification.
    Fails closed to 'end' on ANY error (malformed LLM output, API failure,
    missing state) rather than ever crashing the graph — a router should
    degrade gracefully, not take down the whole request. On failure, also
    logs to agent_runs so the fail-closed path leaves a durable trace
    instead of silently vanishing."""
    label = "end"
    try:
        conversation = "\n".join(f"{m.type}: {m.content}" for m in state["messages"][-6:])
        prompt = SUPERVISOR_PROMPT.format(conversation=conversation)
        result = call_llm(conn, state["tenant_id"], "supervisor", prompt, use_cache=False)
        conn.commit()

        candidate = result["text"].strip().lower()
        candidate = candidate.lstrip("-*• \t")   # strip common bullet/markdown prefixes
        candidate = candidate.strip()

        if candidate in VALID_LABELS:
            label = candidate
        else:
            # last resort: check if any valid label appears as a substring
            # (covers cases like "Label: negotiation" or "The answer is negotiation.")
            matched = next((v for v in VALID_LABELS if v in candidate), None)
            if matched:
                label = matched
                print(f"⚠️  Supervisor label '{result['text'].strip()}' needed normalization -> '{matched}'")
            else:
                print(f"⚠️  Supervisor returned an unrecognized label '{candidate}', defaulting to 'end'")

    except Exception as e:
        print(f"⚠️  Supervisor routing failed, defaulting to 'end': {e}")
        try:
            conn.execute(sql_text("""
                insert into agent_runs (tenant_id, agent_name, status, error_message, estimated_cost_usd)
                values (:t, 'supervisor', 'ERROR', :err, 0.0)
            """), {"t": state["tenant_id"], "err": str(e)})
            conn.commit()
        except Exception:
            pass  # don't let audit logging itself crash the router

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
            model=agent_model,
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
            model=agent_model,
            tools=tools,
            system_prompt="You are an Anomaly/Leakage agent. Use tools to check flagged invoices "
                   "and run reconciliation. Always state check_type and severity when "
                   "reporting a flag. Never invent numbers — only report what the tools return.",
        )
        result = agent.invoke({"messages": state["messages"]})
        return {"messages": result["messages"][-1:]}


    def negotiation_node(state: AgentState) -> dict:
        """Drafts the email, but does NOT finalize it as a response message.
        Stores it as pending_draft and routes to human_approval. entity_id
        and the DRAFTED audit log entry are created HERE, not in
        human_approval_node, because human_approval_node re-runs from the
        top on every interrupt() resume — anything before interrupt() in
        that node would otherwise log DRAFTED once per resume, not once
        per draft. negotiation_node itself is never replayed."""
        tools = make_negotiation_tool(conn, state["tenant_id"])
        agent = create_agent(
            model=agent_model,
            tools=tools,
            system_prompt="You are a Negotiation Drafting agent. Draft supplier outreach emails "
                "about billing issues using the draft_negotiation_email tool. "
                "Output ONLY the drafted email text, nothing else.",
        )
        result = agent.invoke({"messages": state["messages"]})
        draft_text = result["messages"][-1].content

        supplier_name = "the supplier"
        for m in result["messages"]:
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    if tc.get("name") == "draft_negotiation_email":
                        supplier_name = tc.get("args", {}).get("supplier_name", supplier_name)

        entity_id = str(uuid.uuid4())
        log_decision(conn, state["tenant_id"], "negotiation_email", entity_id,
                    "DRAFTED", draft_text, None, actor="agent")
        conn.commit()

        return {
            "pending_draft": {
                "entity_type": "negotiation_email",
                "entity_id": entity_id,
                "supplier_name": supplier_name,
                "content": draft_text,
            }
        }
    
    def human_approval_node(state: AgentState) -> dict:
        """Pauses the graph via interrupt(). Everything in this function before interrupt() re-runs on every resume — so no side effects
        happen here before that point. entity_id and the DRAFTED log were already created once in negotiation_node."""
        draft = state["pending_draft"]
        entity_id = draft["entity_id"]

        decision = interrupt({
            "type": "approval_required",
            "entity_type": draft["entity_type"],
            "entity_id": entity_id,
            "supplier_name": draft["supplier_name"],
            "draft": draft["content"],
        })

        action = decision.get("action", "reject")
        actor = decision.get("actor", "unknown_reviewer")
        edited_text = decision.get("edited_text")

        if action == "approve":
            log_decision(conn, state["tenant_id"], draft["entity_type"], entity_id,
                        "APPROVED", draft["content"], None, actor=actor)
            final_text = draft["content"]
            response = f"✅ Approved by {actor}. Email ready for manual sending to {draft['supplier_name']}:\n\n{final_text}"
        elif action == "edit":
            log_decision(conn, state["tenant_id"], draft["entity_type"], entity_id,
                        "EDITED", draft["content"], edited_text, actor=actor)
            final_text = edited_text
            response = f"✅ Approved with edits by {actor}. Email ready for manual sending to {draft['supplier_name']}:\n\n{final_text}"
        else:
            log_decision(conn, state["tenant_id"], draft["entity_type"], entity_id,
                        "REJECTED", draft["content"], edited_text, actor=actor)
            response = f"❌ Rejected by {actor}. No email will be sent to {draft['supplier_name']}."

        conn.commit()
        return {"messages": [AIMessage(content=response)], "pending_draft": None}


    graph = StateGraph(AgentState)
    graph.add_node("supervisor", partial(_supervisor_node, conn=conn))
    graph.add_node("supplier_intel", supplier_intel_node)
    graph.add_node("anomaly", anomaly_node)
    graph.add_node("negotiation", negotiation_node)
    graph.add_node("human_approval", human_approval_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges("supervisor", _route, {
        "supplier_intel": "supplier_intel",
        "anomaly": "anomaly",
        "negotiation": "negotiation",
        "end": END,
    })
    graph.add_edge("supplier_intel", END)
    graph.add_edge("anomaly", END)
    graph.add_edge("negotiation", "human_approval")
    graph.add_edge("human_approval", END)

    checkpointer = PostgresSaver(_pool)
    # Tables already created via scripts/setup_checkpoint_tables.py (admin role).
    # app_user has read/write grants on them, no CREATE privilege needed here.

    return graph.compile(checkpointer=checkpointer)
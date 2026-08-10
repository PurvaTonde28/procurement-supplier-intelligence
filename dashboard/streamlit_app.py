"""
Phase 13: Streamlit dashboard tying together reconciliation, RAG citations,
negotiation drafting + HITL approval, SQL agent, and live telemetry.
"""
import os
import sys
import uuid

# Allow importing from app/ when Streamlit runs this file directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(override=True)

from app.reconciliation.engine import run_reconciliation_for_tenant
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.rerank import rerank_top3
from app.agents.tools import make_negotiation_tool
from app.agents.audit import log_decision
from app.sql_agent.query_agent import run_query
from app.eval.telemetry import telemetry_summary
from app.ingestion.pdf_parser import parse_pdf_text
from app.ingestion.extractor import extract_with_retry

st.set_page_config(page_title="Procurement Supplier Intelligence", layout="wide")

engine = create_engine(os.getenv("DATABASE_URL"))


@st.cache_resource
def get_engine():
    return engine


def set_tenant(conn, tenant_id):
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})


# --- Sidebar: tenant selector ---
with engine.connect() as conn:
    tenants = conn.execute(text("select id, name from tenants order by name")).fetchall()

tenant_options = {t.name: str(t.id) for t in tenants}
selected_tenant_name = st.sidebar.selectbox("Tenant", list(tenant_options.keys()))
tenant_id = tenant_options[selected_tenant_name]

st.sidebar.markdown("---")
st.sidebar.caption("Procurement Supplier Intelligence — multi-agent value-leakage engine")

st.title("📦 Procurement Supplier Intelligence")
st.caption(f"Active tenant: **{selected_tenant_name}**")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔍 Flagged Invoices", "📄 Contract Search", "✉️ Negotiation + Approval", "💬 Ask the Data", "📊 Telemetry"
])

# ============ TAB 1: Flagged Invoices + Reconciliation ============
with tab1:
    st.subheader("Reconciliation Results")

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔄 Re-run Reconciliation"):
            with engine.connect() as conn:
                set_tenant(conn, tenant_id)
                flagged_count = run_reconciliation_for_tenant(conn, tenant_id)
                conn.commit()
            st.success(f"Reconciliation complete. {flagged_count} invoices flagged.")

    with engine.connect() as conn:
        set_tenant(conn, tenant_id)
        rows = conn.execute(text("""
            select i.invoice_number, s.name as supplier_name, rr.check_type,
                   rr.expected_value, rr.actual_value, rr.variance_amount, rr.severity
            from invoices i
            join reconciliation_results rr on rr.invoice_id = i.id
            join suppliers s on s.id = i.supplier_id
            where i.tenant_id = :t
            order by rr.severity desc, rr.detected_at desc
        """), {"t": tenant_id}).fetchall()

    if rows:
        st.dataframe(
            [dict(r._mapping) for r in rows],
            use_container_width=True,
            column_config={"severity": st.column_config.TextColumn("Severity")},
        )
    else:
        st.info("No flagged invoices. Run reconciliation to check for anomalies.")

    st.markdown("---")
    st.subheader("📤 Upload New Invoice for Extraction")
    uploaded = st.file_uploader("Upload an invoice PDF", type=["pdf"])
    if uploaded and st.button("Extract & Reconcile"):
        temp_path = f"data/synthetic/invoices/_uploaded_{uploaded.name}"
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        with open(temp_path, "wb") as f:
            f.write(uploaded.getbuffer())

        with engine.connect() as conn:
            set_tenant(conn, tenant_id)
            content = parse_pdf_text(temp_path)
            with st.spinner("Extracting structured data..."):
                try:
                    extracted = extract_with_retry(conn, tenant_id, content, "invoice")
                    conn.commit()
                    st.success(f"Extracted: {extracted.model_dump()}")
                    st.caption("Note: extracted data is validated but not yet inserted into the invoices table in this demo flow — Phase 4's pipeline validates structure; wiring extraction directly into reconciliation would be a natural next step.")
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

# ============ TAB 2: Contract Search (Cited RAG) ============
with tab2:
    st.subheader("Ask a question about contract terms")
    question = st.text_input("Question", placeholder="What is the agreed price for extra large shipping crates?")
    if st.button("Search Contracts") and question:
        with engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with st.spinner("Searching + reranking..."):
                candidates = hybrid_search(conn, tenant_id, question, top_k=8)
                if not candidates:
                    st.warning("No contract clauses found.")
                else:
                    result = rerank_top3(conn, tenant_id, question, candidates)
                    conn.commit()
                    for r in result.results:
                        with st.container(border=True):
                            st.markdown(f"**{r.contract_number}, Page {r.page_number}**")
                            st.write(r.relevant_excerpt)
                            st.caption(f"Why relevant: {r.relevance_reason}")

# ============ TAB 3: Negotiation Drafting + HITL Approval ============
with tab3:
    st.subheader("Draft a negotiation email")

    if "draft_state" not in st.session_state:
        st.session_state.draft_state = None

    with engine.connect() as conn:
        set_tenant(conn, tenant_id)
        suppliers = conn.execute(text("select name from suppliers where tenant_id = :t order by name"), {"t": tenant_id}).fetchall()
    supplier_names = [s.name for s in suppliers]

    supplier_choice = st.selectbox("Supplier", supplier_names)
    issue = st.text_area("Issue summary", placeholder="Invoiced PKG-BOX-XL at INR 42.00, contracted price is INR 35.50")

    if st.button("Generate Draft") and issue:
        with engine.connect() as conn:
            set_tenant(conn, tenant_id)
            tools = make_negotiation_tool(conn, tenant_id)
            draft_tool = tools[0]
            with st.spinner("Drafting..."):
                draft_text = draft_tool.invoke({"supplier_name": supplier_choice, "issue_summary": issue})
            entity_id = str(uuid.uuid4())
            log_decision(conn, tenant_id, "negotiation_email", entity_id, "DRAFTED", draft_text, None, actor="agent")
            conn.commit()
            st.session_state.draft_state = {
                "entity_id": entity_id, "original": draft_text, "supplier": supplier_choice
            }

    if st.session_state.draft_state:
        ds = st.session_state.draft_state
        st.markdown("### Review Draft")
        edited_text = st.text_area("Draft (editable)", value=ds["original"], height=200, key="edit_area")

        col_a, col_b, col_c = st.columns(3)
        actor = st.text_input("Your name/email (approver identity)", value="reviewer@example.com")

        with col_a:
            if st.button("✅ Approve as-is"):
                with engine.connect() as conn:
                    set_tenant(conn, tenant_id)
                    log_decision(conn, tenant_id, "negotiation_email", ds["entity_id"], "APPROVED", ds["original"], None, actor=actor)
                    conn.commit()
                st.success("Approved. Ready for manual sending.")
                st.session_state.draft_state = None

        with col_b:
            if st.button("✏️ Approve with edits"):
                with engine.connect() as conn:
                    set_tenant(conn, tenant_id)
                    log_decision(conn, tenant_id, "negotiation_email", ds["entity_id"], "EDITED", ds["original"], edited_text, actor=actor)
                    conn.commit()
                st.success("Approved with edits. Ready for manual sending.")
                st.session_state.draft_state = None

        with col_c:
            if st.button("❌ Reject"):
                with engine.connect() as conn:
                    set_tenant(conn, tenant_id)
                    log_decision(conn, tenant_id, "negotiation_email", ds["entity_id"], "REJECTED", ds["original"], edited_text, actor=actor)
                    conn.commit()
                st.warning("Rejected. No email will be sent.")
                st.session_state.draft_state = None

    st.markdown("---")
    st.subheader("Audit Trail")
    with engine.connect() as conn:
        set_tenant(conn, tenant_id)
        audit_rows = conn.execute(text("""
            select entity_type, action, actor, content_hash, created_at
            from audit_log where tenant_id = :t order by created_at desc limit 20
        """), {"t": tenant_id}).fetchall()
    if audit_rows:
        st.dataframe([dict(r._mapping) for r in audit_rows], use_container_width=True)
    else:
        st.caption("No audit entries yet.")

# ============ TAB 4: SQL Agent ============
with tab4:
    st.subheader("Ask a natural-language question over the procurement database")
    st.caption("Read-only, tenant-scoped, guardrailed against unsafe SQL.")
    nl_question = st.text_input("Your question", placeholder="Which suppliers have the highest total invoiced amount?")
    if st.button("Ask") and nl_question:
        with engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with st.spinner("Generating and executing SQL..."):
                result = run_query(conn, tenant_id, nl_question)
        st.code(result["generated_sql"], language="sql")
        if "error" in result:
            st.error(f"Blocked/Failed: {result['error']}")
        else:
            st.dataframe(result["rows"], use_container_width=True)

# ============ TAB 5: Telemetry ============
with tab5:
    st.subheader("Agent Telemetry")
    with engine.connect() as conn:
        set_tenant(conn, tenant_id)
        summary = telemetry_summary(conn, tenant_id)
    if summary:
        st.dataframe(summary, use_container_width=True)
    else:
        st.caption("No telemetry recorded yet.")
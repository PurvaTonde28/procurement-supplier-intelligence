"""
Phase 13: Streamlit dashboard tying together reconciliation, RAG citations,
negotiation drafting + HITL approval, SQL agent, and live telemetry.
Theme: .streamlit/config.toml sets the base light theme (fixes native
widget text color); CSS below only adds custom accents on top of it.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

if hasattr(st, "secrets") and "DATABASE_URL" in st.secrets:
    for key in ["DATABASE_URL", "SQL_AGENT_DATABASE_URL", "GROQ_API_KEY", "GOOGLE_API_KEY"]:
        if key in st.secrets:
            os.environ[key] = st.secrets[key]
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


def set_tenant(conn, tenant_id):
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})


with engine.connect() as conn:
    tenants = conn.execute(text("select id, name from tenants order by name")).fetchall()

tenant_options = {t.name: str(t.id) for t in tenants}
selected_tenant_name = st.sidebar.selectbox("Tenant", list(tenant_options.keys()))
tenant_id = tenant_options[selected_tenant_name]

st.sidebar.markdown("---")
st.sidebar.caption("Procurement Supplier Intelligence — multi-agent value-leakage engine")

# ============ STYLING (accents only — base theme comes from .streamlit/config.toml) ============
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.02em; }

.hero-title { font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 2.1rem; color: #0F172A; margin-bottom: 0; }
.accent-bar { height: 4px; width: 100%; margin: 10px 0 18px 0; border-radius: 4px;
  background: linear-gradient(90deg, #4F46E5, #0D9488, #4F46E5);
  background-size: 200% 100%; animation: shimmer 6s ease-in-out infinite; }
@keyframes shimmer { 0% {background-position: 0% 50%;} 50% {background-position: 100% 50%;} 100% {background-position: 0% 50%;} }

.tenant-line { display: flex; align-items: center; gap: 8px; color: #64748B; font-size: 0.95rem; margin-bottom: 6px; }
.pulse-dot { width: 9px; height: 9px; border-radius: 50%; background: #0D9488;
  box-shadow: 0 0 0 rgba(13,148,136,0.5); animation: pulse 2s infinite; }
@keyframes pulse { 0% {box-shadow: 0 0 0 0 rgba(13,148,136,0.5);} 70% {box-shadow: 0 0 0 8px rgba(13,148,136,0);} 100% {box-shadow: 0 0 0 0 rgba(13,148,136,0);} }

.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid #E2E8F0; }
.stTabs [data-baseweb="tab"] { font-weight: 500; padding: 10px 16px; transition: color 0.2s; }
.stTabs [aria-selected="true"] { color: #4F46E5 !important; border-bottom: 2px solid #4F46E5 !important; }

.stButton>button { background: #4F46E5; color: #FFFFFF !important; border: none; border-radius: 8px;
  padding: 0.5rem 1.1rem; font-weight: 500; transition: transform 0.15s, box-shadow 0.15s; }
.stButton>button:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(79,70,229,0.25); }
.stButton>button p { color: #FFFFFF !important; }

div[data-testid="stVerticalBlockBorderWrapper"] { border: 1px solid #E2E8F0 !important; border-radius: 10px !important;
  transition: box-shadow 0.2s; }
div[data-testid="stVerticalBlockBorderWrapper"]:hover { box-shadow: 0 4px 16px rgba(15,23,42,0.06); }

.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 600; }
.badge-high { background: #FEE2E2; color: #DC2626; }
.badge-medium { background: #FEF3C7; color: #D97706; }
.badge-low { background: #DCFCE7; color: #16A34A; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="hero-title">📦 Procurement Supplier Intelligence</div>', unsafe_allow_html=True)
st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
st.markdown(f'<div class="tenant-line"><span class="pulse-dot"></span>Active tenant: <b>{selected_tenant_name}</b></div>', unsafe_allow_html=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔍 Flagged Invoices", "📄 Contract Search", "✉️ Negotiation + Approval", "💬 Ask the Data", "📊 Telemetry"
])

# ============ TAB 1 ============
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
        badge_map = {"HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low"}
        html = "<table style='width:100%; border-collapse:collapse;'>"
        html += ("<tr style='text-align:left; color:#64748B; font-size:0.85rem;'>"
                 "<th>Invoice</th><th>Supplier</th><th>Check</th><th>Expected</th><th>Actual</th><th>Severity</th></tr>")
        for r in rows:
            cls = badge_map.get(r.severity, "badge-low")
            html += (f"<tr style='border-top:1px solid #E2E8F0; color:#0F172A;'>"
                     f"<td style='padding:8px 4px;'>{r.invoice_number}</td><td>{r.supplier_name}</td>"
                     f"<td>{r.check_type}</td><td>{r.expected_value if r.expected_value is not None else '—'}</td>"
                     f"<td>{r.actual_value if r.actual_value is not None else '—'}</td>"
                     f"<td><span class='badge {cls}'>{r.severity}</span></td></tr>")
        html += "</table>"
        st.markdown(html, unsafe_allow_html=True)
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
                    st.caption("Note: extracted data is validated but not yet inserted into the invoices table in this demo flow.")
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

# ============ TAB 2 ============
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

# ============ TAB 3 ============
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
            st.session_state.draft_state = {"entity_id": entity_id, "original": draft_text, "supplier": supplier_choice}

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

# ============ TAB 4 ============
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

# ============ TAB 5 ============
with tab5:
    st.subheader("Agent Telemetry")
    with engine.connect() as conn:
        set_tenant(conn, tenant_id)
        summary = telemetry_summary(conn, tenant_id)
    if summary:
        st.dataframe(summary, use_container_width=True)
    else:
        st.caption("No telemetry recorded yet.")
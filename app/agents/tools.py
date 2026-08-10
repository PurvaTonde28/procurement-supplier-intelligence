"""Tool wrappers around Phase 3/5/6 logic. Agents never touch the DB
or LLM providers directly — everything routes through these, keeping
every call tenant-scoped and logged through the existing router."""
from langchain_core.tools import tool
from sqlalchemy import text as sql_text

from app.reconciliation.engine import run_reconciliation_for_tenant
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.rerank import rerank_top3
from app.llm.router import call_llm
from app.guardrails.pii import redact_pii

# conn is injected per-request via functools.partial in the graph builder,
# since LangChain tools can't easily take extra runtime args otherwise.


def make_tools(conn, tenant_id: str):

    @tool
    def get_flagged_invoices(limit: str = "10") -> str:
        """Returns invoices currently flagged as LEAKAGE_DETECTED, with their
        reconciliation_results (check_type, expected/actual price, severity).
        Args: limit - how many results to return, as a number (e.g. "10")."""
        try:
            limit_int = int(limit)
        except (ValueError, TypeError):
            limit_int = 10

        rows = conn.execute(sql_text("""
            select i.invoice_number, i.supplier_id, s.name as supplier_name,
                rr.check_type, rr.expected_value, rr.actual_value, rr.severity
            from invoices i
            join reconciliation_results rr on rr.invoice_id = i.id
            join suppliers s on s.id = i.supplier_id
            where i.tenant_id = :t and i.reconciliation_status = 'LEAKAGE_DETECTED'
            order by rr.detected_at desc
            limit :lim
        """), {"t": tenant_id, "lim": limit_int}).fetchall()
        if not rows:
            return "No flagged invoices found."
        return "\n".join(
            f"{r.invoice_number} | {r.supplier_name} | {r.check_type} | "
            f"expected={r.expected_value} actual={r.actual_value} severity={r.severity}"
            for r in rows
        )
    

    @tool
    def run_reconciliation() -> str:
        """Re-runs the deterministic SQL reconciliation engine for this tenant
        and returns how many invoices are now flagged."""
        flagged_count = run_reconciliation_for_tenant(conn, tenant_id)
        conn.commit()
        return f"Reconciliation complete. {flagged_count} invoices currently flagged."

    @tool
    def search_contract_clauses(question: str) -> str:
        """Searches contract text for clauses relevant to a question, returning
        cited excerpts with contract number and page number."""
        candidates = hybrid_search(conn, tenant_id, question, top_k=8)
        if not candidates:
            return "No contract clauses found."
        result = rerank_top3(conn, tenant_id, question, candidates)
        conn.commit()
        return "\n\n".join(
            f"[{r.contract_number}, Page {r.page_number}]: {r.relevant_excerpt}\nWhy relevant: {r.relevance_reason}"
            for r in result.results
        )

    @tool
    def get_supplier_info(supplier_name: str) -> str:
        """Looks up a supplier's category, risk score, and active contracts by name."""
        row = conn.execute(sql_text("""
            select id, name, category, risk_score from suppliers
            where tenant_id = :t and name ilike :n limit 1
        """), {"t": tenant_id, "n": f"%{supplier_name}%"}).fetchone()
        if not row:
            return f"No supplier found matching '{supplier_name}'."
        contracts = conn.execute(sql_text("""
            select contract_number, status from contracts where tenant_id = :t and supplier_id = :s
        """), {"t": tenant_id, "s": row.id}).fetchall()
        contracts_str = ", ".join(f"{c.contract_number} ({c.status})" for c in contracts) or "none"
        return f"{row.name} | category={row.category} | risk_score={row.risk_score} | contracts: {contracts_str}"

    return [get_flagged_invoices, run_reconciliation, search_contract_clauses, get_supplier_info]


def make_negotiation_tool(conn, tenant_id: str):
    @tool
    def draft_negotiation_email(supplier_name: str, issue_summary: str) -> str:
        """Drafts a professional dispute/negotiation email to a supplier about
        a billing discrepancy. Does NOT send anything — output requires human approval."""
        prompt = f"""Draft a professional, firm but courteous email to {supplier_name}'s billing team regarding the following issue: {issue_summary}

        Keep it under 150 words. Do not include a signature block.
        End with a note that this draft requires human review and approval before sending."""

        result = call_llm(conn, tenant_id, "negotiation_agent", prompt)
        conn.commit()

        redaction = redact_pii(result["text"])
        if redaction["findings"]:
            print(f"⚠️  PII redacted from draft before review: {redaction['findings']}")

        return redaction["redacted_text"]
        # return result["text"]

    return [draft_negotiation_email]
import os
import subprocess
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


from app.eval.extraction_eval import eval_extraction
from app.eval.citation_eval import eval_citations
from app.eval.judge import judge_draft
from app.agents.tools import make_negotiation_tool

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

    print("=" * 60)
    print("PHASE 11 EVAL HARNESS — procurement-supplier-intelligence")
    print("=" * 60)

    print("\n[1] Reconciliation Engine (see scripts/validate_reconciliation.py for full detail)")
    subprocess.run(["python", "-m", "scripts.validate_reconciliation"], check=True)

    print("\n[2] Extraction Accuracy")
    ext = eval_extraction(conn, str(tenant_id))
    print(f"  Contract extraction accuracy: {ext['contract_accuracy']:.0%}")
    print(f"  Invoice extraction accuracy:  {ext['invoice_accuracy']:.0%}")
    for r in ext["details"]["contracts"] + ext["details"]["invoices"]:
        print(f"   - {r}")

    print("\n[3] Citation Correctness")
    cit = eval_citations(conn, str(tenant_id))
    print(f"  Citation accuracy: {cit['citation_accuracy']:.0%}")
    for r in cit["details"]:
        status = "✅" if r["correct"] else "❌"
        print(f"   {status} '{r['question']}' -> expected {r['expected']}, got {r['actual']}")

    print("\n[4] LLM-as-Judge: Negotiation Draft Quality")
    negotiation_tools = make_negotiation_tool(conn, str(tenant_id))
    draft_tool = negotiation_tools[0]
    issue = "Apex Packaging invoiced PKG-BOX-XL at INR 42.00, contracted price is INR 35.50"
    draft = draft_tool.invoke({"supplier_name": "Apex Packaging", "issue_summary": issue})
    judgment = judge_draft(conn, str(tenant_id), issue, draft)
    conn.commit()
    print(f"  Professional tone: {judgment.professional_tone}/5")
    print(f"  Factually grounded: {judgment.factually_grounded}/5")
    print(f"  States approval needed: {judgment.clearly_states_approval_needed}")
    print(f"  Reasoning: {judgment.reasoning}")

    print("\n" + "=" * 60)
    print("EVAL COMPLETE")
    print("=" * 60)
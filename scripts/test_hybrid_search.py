import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.rerank import rerank_top3

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

TEST_QUESTIONS = [
    "What is the agreed unit price for extra large shipping crates?",
    "What happens if a supplier invoices above the contracted price?",
    "What is the delivery lead time for laptops?",
]

with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

    for q in TEST_QUESTIONS:
        print(f"\n=== Q: {q} ===")
        candidates = hybrid_search(conn, str(tenant_id), q, top_k=8)
        print(f"  Hybrid search returned {len(candidates)} candidates")

        result = rerank_top3(conn, str(tenant_id), q, candidates)
        conn.commit()
        for r in result.results:
            print(f"  📄 {r.contract_number}, Page {r.page_number}: \"{r.relevant_excerpt}\"")
            print(f"     Why: {r.relevance_reason}")
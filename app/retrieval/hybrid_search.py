"""Hybrid search: weighted blend of pgvector cosine similarity + Postgres
full-text ts_rank, computed in a single SQL query — no external BM25 library."""
from sqlalchemy import text as sql_text
from app.retrieval.embeddings import embed_text, to_pgvector_literal

VECTOR_WEIGHT = 0.7
TEXT_WEIGHT = 0.3


def hybrid_search(conn, tenant_id: str, query: str, top_k: int = 8) -> list[dict]:
    query_embedding = embed_text(query, task_type="retrieval_query")
    embedding_literal = to_pgvector_literal(query_embedding)

    rows = conn.execute(sql_text(f"""
        select cc.id, cc.contract_id, c.contract_number, cc.page_number, cc.chunk_text,
               1 - (cc.embedding <=> CAST(:emb AS vector)) as vector_score,
               ts_rank(cc.tsv, plainto_tsquery('english', :q)) as text_score,
               ({VECTOR_WEIGHT} * (1 - (cc.embedding <=> CAST(:emb AS vector)))
                + {TEXT_WEIGHT} * ts_rank(cc.tsv, plainto_tsquery('english', :q))) as hybrid_score
        from contract_chunks cc
        join contracts c on c.id = cc.contract_id
        where cc.tenant_id = :t
        order by hybrid_score desc
        limit :k
    """), {"emb": embedding_literal, "q": query, "t": tenant_id, "k": top_k}).fetchall()

    return [
        {
            "chunk_id": str(r.id), "contract_id": str(r.contract_id),
            "contract_number": r.contract_number, "page_number": r.page_number,
            "chunk_text": r.chunk_text, "vector_score": float(r.vector_score),
            "text_score": float(r.text_score), "hybrid_score": float(r.hybrid_score),
        }
        for r in rows
    ]
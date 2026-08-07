"""Chunks each contract PDF, embeds each chunk, stores in contract_chunks.
Matches PDF filenames to contracts by contract_number embedded in the filename."""
import os
import glob
import uuid
import time
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.retrieval.chunker import chunk_pdf_by_page
from app.retrieval.embeddings import embed_text, to_pgvector_literal, log_embedding_run

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

CONTRACT_NUMBER_PATTERN = re.compile(r"(CON-\d+)")


def main():
    with engine.connect() as conn:
        tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
        conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})

        # clear prior chunks for idempotency
        conn.execute(text("delete from contract_chunks where tenant_id = :t"), {"t": tenant_id})
        conn.commit()

        for path in glob.glob("data/synthetic/contracts/*.pdf"):
            match = CONTRACT_NUMBER_PATTERN.search(os.path.basename(path))
            if not match:
                print(f"⚠️  Skipping {path} — couldn't parse contract number from filename")
                continue
            contract_number = match.group(1)

            contract_id = conn.execute(text("""
                select id from contracts where tenant_id = :t and contract_number = :cn
            """), {"t": tenant_id, "cn": contract_number}).scalar()

            if not contract_id:
                print(f"⚠️  No contract found in DB for {contract_number} — skipping {path}")
                continue

            chunks = chunk_pdf_by_page(path)
            succeeded = 0
            for chunk in chunks:
                run_id = uuid.uuid4()
                start = time.time()
                try:
                    embedding = embed_text(chunk["chunk_text"], task_type="retrieval_document")
                    latency_ms = int((time.time() - start) * 1000)
                    log_embedding_run(conn, tenant_id, run_id, "SUCCESS", latency_ms)

                    conn.execute(text("""
                        insert into contract_chunks (tenant_id, contract_id, page_number, chunk_text, embedding)
                        values (:t, :c, :p, :txt, :emb)
                    """), {
                        "t": tenant_id, "c": contract_id, "p": chunk["page_number"],
                        "txt": chunk["chunk_text"], "emb": to_pgvector_literal(embedding)
                    })
                    succeeded += 1
                except Exception as e:
                    latency_ms = int((time.time() - start) * 1000)
                    log_embedding_run(conn, tenant_id, run_id, "ERROR", latency_ms, str(e))
                    print(f"❌ Failed to embed chunk from {path} page {chunk['page_number']}: {e}")

            conn.commit()
            print(f"{'✅' if succeeded == len(chunks) else '⚠️ '} Ingested {succeeded}/{len(chunks)} chunks from {contract_number} ({path})")
    print("\n✅ Contract chunk ingestion complete.")


if __name__ == "__main__":
    main()
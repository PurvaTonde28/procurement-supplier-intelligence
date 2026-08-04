# Verify pgvector End-to-End: Smoke test
# This table is only for testing and is deleted at the end.

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    # Create test table
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pgvector_smoke_test (
            id SERIAL PRIMARY KEY,
            content TEXT,
            embedding vector(3)
        )
    """))
    conn.commit()

    # Insert sample vectors
    conn.execute(text("""
        INSERT INTO pgvector_smoke_test (content, embedding)
        VALUES
            ('apple', '[1,0,0]'),
            ('orange', '[0.9,0.1,0]'),
            ('truck', '[0,0,1]')
    """))
    conn.commit()

    # Similarity search
    result = conn.execute(text("""
        SELECT content, embedding <=> '[1,0,0]' AS distance
        FROM pgvector_smoke_test
        ORDER BY distance
        LIMIT 3
    """))

    print("Nearest vectors:\n")
    for row in result:
        print(f"{row.content} -> {row.distance}")

    # Cleanup
    conn.execute(text("DROP TABLE pgvector_smoke_test"))
    conn.commit()

print("\n✅ pgvector smoke test passed!")
from app.sql_agent.guardrails import validate_and_limit, SQLSafetyError

ADVERSARIAL_SQL = [
    "SELECT * FROM suppliers; DROP TABLE suppliers;",
    "DELETE FROM invoices WHERE 1=1",
    "SELECT * FROM suppliers -- ; DROP TABLE suppliers;",
    "UPDATE suppliers SET risk_score = 0",
    "SELECT * FROM pg_catalog.pg_tables",
]

for sql in ADVERSARIAL_SQL:
    try:
        result = validate_and_limit(sql)
        print(f"⚠️  NOT BLOCKED: {sql} -> {result}")
    except SQLSafetyError as e:
        print(f"✅ Blocked: {sql} -> {e}")
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from app.eval.telemetry import telemetry_summary

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tenant_id = conn.execute(text("select id from tenants limit 1")).scalar()
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})  # ← added

    summary = telemetry_summary(conn, str(tenant_id))
    print(f"{'agent_name':35} {'calls':>6} {'success':>8} {'errors':>7} {'avg_ms':>8} {'cost_usd':>10}")
    for row in summary:
        print(f"{row['agent_name']:35} {row['call_count']:>6} {row['success_count']:>8} {row['error_count']:>7} {row['avg_latency_ms']:>8} {row['total_cost_usd']:>10}")
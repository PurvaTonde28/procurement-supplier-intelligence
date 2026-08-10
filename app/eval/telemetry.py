from sqlalchemy import text as sql_text

def telemetry_summary(conn, tenant_id: str) -> dict:
    rows = conn.execute(sql_text("""
        select agent_name,
               count(*) as call_count,
               sum(case when status = 'SUCCESS' then 1 else 0 end) as success_count,
               sum(case when status like '%ERROR%' or status like '%BLOCKED%' then 1 else 0 end) as error_count,
               round(coalesce(avg(latency_ms), 0)) as avg_latency_ms,
               round(coalesce(sum(estimated_cost_usd), 0)::numeric, 6) as total_cost_usd
        from agent_runs
        where tenant_id = :t
        group by agent_name
        order by call_count desc
    """), {"t": tenant_id}).fetchall()
    return [dict(r._mapping) for r in rows]
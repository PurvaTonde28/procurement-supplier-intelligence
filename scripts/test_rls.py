import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    print("Connected as:", conn.execute(text("select current_user")).scalar())

    # tenants table has no RLS (it's the root entity), so this is fine as-is
    t1 = conn.execute(text("insert into tenants (name) values ('Tenant A') returning id")).scalar()
    t2 = conn.execute(text("insert into tenants (name) values ('Tenant B') returning id")).scalar()
    conn.commit()

    # Set context to Tenant A BEFORE inserting Tenant A's supplier
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(t1)})
    conn.execute(text("insert into suppliers (tenant_id, name) values (:t, 'Acme Logistics')"), {"t": t1})
    conn.commit()

    # Set context to Tenant B BEFORE inserting Tenant B's supplier
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(t2)})
    conn.execute(text("insert into suppliers (tenant_id, name) values (:t, 'Globex Freight')"), {"t": t2})
    conn.commit()

    # Now read back as Tenant A
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(t1)})
    result = conn.execute(text("select name from suppliers"))
    print("Visible to Tenant A:", [row.name for row in result])

    # Now read back as Tenant B
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(t2)})
    result = conn.execute(text("select name from suppliers"))
    print("Visible to Tenant B:", [row.name for row in result])

print("\nIf each tenant sees only its own supplier, RLS is working ✅")
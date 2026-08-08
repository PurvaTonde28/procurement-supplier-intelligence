"""One-time migration: creates LangGraph's checkpoint tables.
Run this ONCE, connected as the admin/postgres role — app_user
deliberately has no CREATE privilege on public schema (Phase 1
least-privilege decision), same as it can't ALTER/CREATE anything else."""
import os
from dotenv import load_dotenv
from langgraph.checkpoint.postgres import PostgresSaver

load_dotenv(override=True)

# Use the ADMIN connection string here, not DATABASE_URL (which is app_user)
admin_url = os.getenv("ADMIN_DATABASE_URL")
if not admin_url:
    raise SystemExit("Set ADMIN_DATABASE_URL in .env (postgres superuser connection) before running this.")

with PostgresSaver.from_conn_string(admin_url) as checkpointer:
    checkpointer.setup()

print("✅ Checkpoint tables created.")
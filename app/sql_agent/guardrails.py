"""App-level SQL safety checks — a second, independent layer on top of the
sql_agent_ro role's DB-level SELECT-only grants. Rejects anything that
isn't a single, safe SELECT before it ever reaches the database."""
import re

FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "truncate", "grant",
    "revoke", "create", "execute", "call", "copy", "vacuum", "reindex",
    "--", "/*", "*/", "pg_", "information_schema",
]

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class SQLSafetyError(Exception):
    pass


def validate_and_limit(sql: str) -> str:
    stripped = sql.strip().rstrip(";")

    if ";" in stripped:
        raise SQLSafetyError("Multiple statements are not allowed.")

    if not re.match(r"^\s*select\b", stripped, re.IGNORECASE):
        raise SQLSafetyError("Only SELECT statements are allowed.")

    lowered = stripped.lower()
    for kw in FORBIDDEN_KEYWORDS:
        if kw in lowered:
            raise SQLSafetyError(f"Forbidden keyword detected: '{kw}'")

    if not re.search(r"\blimit\s+\d+\b", lowered):
        stripped += f" LIMIT {DEFAULT_LIMIT}"
    else:
        match = re.search(r"\blimit\s+(\d+)\b", lowered)
        if match and int(match.group(1)) > MAX_LIMIT:
            stripped = re.sub(r"\blimit\s+\d+\b", f"LIMIT {MAX_LIMIT}", stripped, flags=re.IGNORECASE)

    return stripped
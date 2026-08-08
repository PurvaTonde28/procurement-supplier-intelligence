"""Audit trail helpers: diff computation, content hashing, and the single
write path into audit_log. Every HITL decision — approve, edit, reject —
goes through log_decision, so there is exactly one place that writes
to this table."""
import hashlib
import difflib
import json
import uuid
from sqlalchemy import text as sql_text


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_diff(original: str, edited: str) -> list[str]:
    """Line-level unified diff, stored as JSON — human-readable in a DB
    browser without needing a diff tool to interpret it."""
    return list(difflib.unified_diff(
        original.splitlines(), edited.splitlines(),
        lineterm="", n=1
    ))


def log_decision(conn, tenant_id: str, entity_type: str, entity_id: str,
                  action: str, original_text: str, edited_text: str | None,
                  actor: str) -> str:
    """action: DRAFTED | APPROVED | EDITED | REJECTED
    Returns the audit_log row id."""
    edited_text = edited_text if edited_text is not None else original_text
    diff = compute_diff(original_text, edited_text) if edited_text != original_text else []

    row_id = conn.execute(sql_text("""
        insert into audit_log
            (tenant_id, entity_type, entity_id, action, original_payload,
             edited_payload, diff, actor, content_hash)
        values (:t, :et, :eid, :act, :orig, :edit, :diff, :actor, :hash)
        returning id
    """), {
        "t": tenant_id, "et": entity_type, "eid": entity_id, "act": action,
        "orig": json.dumps({"text": original_text}),
        "edit": json.dumps({"text": edited_text}),
        "diff": json.dumps(diff),
        "actor": actor,
        "hash": content_hash(edited_text),
    }).scalar()
    return str(row_id)
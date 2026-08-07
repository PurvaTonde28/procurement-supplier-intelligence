"""
Phase 3: Deterministic reconciliation engine.
No LLM calls here — this is the ground-truth layer that later agent
phases will explain and act on, never override.
"""
import os
from decimal import Decimal
from sqlalchemy import text

PRICE_VARIANCE_THRESHOLD = Decimal("0.05")  # 5% — below this is tolerated as rounding/FX noise


def set_tenant(conn, tenant_id):
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})


def reset_reconciliation(conn, tenant_id):
    """Idempotency: clear previous results for this tenant before re-running."""
    conn.execute(text("delete from reconciliation_results where tenant_id = :t"), {"t": tenant_id})
    conn.execute(text("update invoices set reconciliation_status = 'PENDING_AUDIT' where tenant_id = :t"), {"t": tenant_id})


def check_price_and_contract(conn, tenant_id):
    """Single pass: flags MISSING_CONTRACT (no active contract_item match)
    and PRICE_VARIANCE (invoiced price exceeds contracted price beyond threshold)."""
    rows = conn.execute(text("""
        select i.id as invoice_id, i.supplier_id, i.item_sku,
               i.invoice_unit_price, ci.agreed_unit_price
        from invoices i
        left join contract_items ci
          on ci.tenant_id = i.tenant_id
         and ci.supplier_id = i.supplier_id
         and ci.item_sku = i.item_sku
         and ci.is_active = true
        where i.tenant_id = :t
    """), {"t": tenant_id}).fetchall()

    flagged_invoice_ids = set()

    for row in rows:
        if row.agreed_unit_price is None:
            conn.execute(text("""
                insert into reconciliation_results
                    (tenant_id, invoice_id, check_type, expected_value, actual_value, variance_amount, severity)
                values (:t, :inv, 'MISSING_CONTRACT', null, :actual, null, 'HIGH')
            """), {"t": tenant_id, "inv": row.invoice_id, "actual": row.invoice_unit_price})
            flagged_invoice_ids.add(row.invoice_id)
            continue

        agreed = Decimal(str(row.agreed_unit_price))
        invoiced = Decimal(str(row.invoice_unit_price))
        variance_pct = (invoiced - agreed) / agreed if agreed != 0 else Decimal("0")

        if variance_pct > PRICE_VARIANCE_THRESHOLD:
            severity = "HIGH" if variance_pct > Decimal("0.15") else "MEDIUM"
            conn.execute(text("""
                insert into reconciliation_results
                    (tenant_id, invoice_id, check_type, expected_value, actual_value, variance_amount, severity)
                values (:t, :inv, 'PRICE_VARIANCE', :expected, :actual, :variance, :sev)
            """), {"t": tenant_id, "inv": row.invoice_id, "expected": agreed,
                    "actual": invoiced, "variance": invoiced - agreed, "sev": severity})
            flagged_invoice_ids.add(row.invoice_id)

    return flagged_invoice_ids


def check_duplicate_po(conn, tenant_id):
    """Flags every invoice that shares a po_id with at least one other invoice."""
    dupes = conn.execute(text("""
        select po_id, array_agg(id) as invoice_ids
        from invoices
        where tenant_id = :t and po_id is not null
        group by po_id
        having count(*) > 1
    """), {"t": tenant_id}).fetchall()

    flagged_invoice_ids = set()
    for row in dupes:
        for invoice_id in row.invoice_ids:
            conn.execute(text("""
                insert into reconciliation_results
                    (tenant_id, invoice_id, check_type, severity)
                values (:t, :inv, 'DUPLICATE_PO', 'HIGH')
            """), {"t": tenant_id, "inv": invoice_id})
            flagged_invoice_ids.add(invoice_id)

    return flagged_invoice_ids


def apply_final_status(conn, tenant_id, flagged_invoice_ids):
    all_ids = conn.execute(text("select id from invoices where tenant_id = :t"), {"t": tenant_id}).fetchall()
    for row in all_ids:
        status = "LEAKAGE_DETECTED" if row.id in flagged_invoice_ids else "APPROVED"
        conn.execute(text("update invoices set reconciliation_status = :s where id = :i"),
                     {"s": status, "i": row.id})


def run_reconciliation_for_tenant(conn, tenant_id):
    set_tenant(conn, tenant_id)
    reset_reconciliation(conn, tenant_id)

    flagged = set()
    flagged |= check_price_and_contract(conn, tenant_id)
    flagged |= check_duplicate_po(conn, tenant_id)
    apply_final_status(conn, tenant_id, flagged)

    return len(flagged)
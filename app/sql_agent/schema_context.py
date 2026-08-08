"""Hand-written schema description given to the LLM — deliberately not a
raw DDL dump. Keeps the agent's knowledge scoped to what's safe and useful
to expose, and lets us describe relationships in plain language."""

SCHEMA_CONTEXT = """
You are querying a procurement database. Available tables (all filtered
to the current tenant automatically — never add a tenant_id filter yourself):

suppliers(id, name, category, risk_score)
contracts(id, supplier_id, contract_number, start_date, end_date, status)
contract_items(id, contract_id, supplier_id, item_sku, item_description, agreed_unit_price)
purchase_orders(id, po_number, supplier_id, item_sku, quantity, unit_price, order_date, status)
invoices(id, invoice_number, supplier_id, po_id, item_sku, quantity_billed,
         invoice_unit_price, total_amount, invoice_date, reconciliation_status)
reconciliation_results(id, invoice_id, check_type, expected_value, actual_value,
                        variance_amount, severity, resolved)

Relationships:
- contracts.supplier_id -> suppliers.id
- contract_items.contract_id -> contracts.id
- purchase_orders.supplier_id -> suppliers.id
- invoices.supplier_id -> suppliers.id, invoices.po_id -> purchase_orders.id
- reconciliation_results.invoice_id -> invoices.id

reconciliation_status values: 'PENDING_AUDIT', 'APPROVED', 'LEAKAGE_DETECTED'
check_type values: 'PRICE_VARIANCE', 'DUPLICATE_PO', 'MISSING_CONTRACT'
severity values: 'LOW', 'MEDIUM', 'HIGH'
"""
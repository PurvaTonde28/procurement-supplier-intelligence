"""
Phase 2: Synthetic data generator for procurement-supplier-intelligence.
Generates tenants, suppliers, contracts, contract_items, purchase_orders,
and invoices with DELIBERATE, LABELED outcomes so Phase 3's reconciliation
engine and Phase 11's eval harness both have known ground truth to check against.
"""
import os
import json
import random
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from faker import Faker

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))
fake = Faker()
random.seed(42)  # reproducible dataset

SKU_CATALOG = [
    ("LOG-DIS-01", "Standard Freight Routing", 450.00),
    ("PKG-BOX-XL", "Extra Large Shipping Crate", 35.50),
    ("PKG-BOX-SM", "Small Shipping Carton", 8.75),
    ("FRT-SEA-20", "20ft Sea Freight Container", 12000.00),
    ("RAW-STEEL-01", "Steel Coil (per ton)", 68000.00),
    ("IT-LAPTOP-01", "Business Laptop Unit", 52000.00),
    ("OFC-CHAIR-01", "Ergonomic Office Chair", 9500.00),
    ("MRO-BEARING-01", "Industrial Bearing Set", 1450.00),
]

TENANT_NAMES = ["Meridian Manufacturing Pvt Ltd", "Northbridge Retail Group"]


def set_tenant(conn, tenant_id):
    conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})


def create_tenant(conn, name):
    return conn.execute(
        text("insert into tenants (name) values (:n) returning id"), {"n": name}
    ).scalar()


def create_suppliers(conn, tenant_id, n=8):
    supplier_ids = []
    for _ in range(n):
        name = fake.company()
        sid = conn.execute(
            text("""insert into suppliers (tenant_id, name, category, risk_score)
                     values (:t, :n, :c, :r) returning id"""),
            {"t": tenant_id, "n": name, "c": random.choice(["Logistics", "Packaging", "IT", "Raw Materials", "Facilities"]),
             "r": round(random.uniform(0.1, 0.9), 2)}
        ).scalar()
        supplier_ids.append(sid)
    return supplier_ids


def create_contracts_and_items(conn, tenant_id, supplier_ids):
    """Each supplier gets 1 contract covering 2-3 SKUs. Returns list of
    dicts with contract_id, supplier_id, and {sku: agreed_price}."""
    contract_data = []
    for i, sid in enumerate(supplier_ids):
        contract_number = f"CON-{i+1:03d}"
        start = date(2025, 1, 1)
        end = date(2026, 12, 31)
        cid = conn.execute(
            text("""insert into contracts (tenant_id, supplier_id, contract_number, start_date, end_date, currency, status)
                     values (:t, :s, :cn, :sd, :ed, 'INR', 'ACTIVE') returning id"""),
            {"t": tenant_id, "s": sid, "cn": contract_number, "sd": start, "ed": end}
        ).scalar()

        items = random.sample(SKU_CATALOG, k=random.randint(2, 3))
        sku_prices = {}
        for sku, desc, base_price in items:
            conn.execute(
                text("""insert into contract_items (tenant_id, contract_id, supplier_id, item_sku, item_description, agreed_unit_price, currency)
                         values (:t, :c, :s, :sku, :desc, :price, 'INR')"""),
                {"t": tenant_id, "c": cid, "s": sid, "sku": sku, "desc": desc, "price": base_price}
            )
            sku_prices[sku] = base_price
        contract_data.append({"contract_id": cid, "supplier_id": sid, "items": sku_prices})
    return contract_data


def create_po(conn, tenant_id, po_number, supplier_id, sku, qty, unit_price, order_date):
    """Inserts a PO and returns its generated UUID (the real primary key),
    which is what invoices.po_id must reference — NOT the human-readable po_number."""
    return conn.execute(
        text("""insert into purchase_orders (tenant_id, po_number, supplier_id, item_sku, quantity, unit_price, order_date, status)
                 values (:t, :po, :s, :sku, :q, :p, :d, 'OPEN') returning id"""),
        {"t": tenant_id, "po": po_number, "s": supplier_id, "sku": sku, "q": qty, "p": unit_price, "d": order_date}
    ).scalar()


def create_invoice(conn, tenant_id, invoice_number, supplier_id, po_id, sku, qty, unit_price, invoice_date):
    """po_id must be the PO's UUID primary key, not its po_number string."""
    total = round(qty * unit_price, 2)
    conn.execute(
        text("""insert into invoices (tenant_id, invoice_number, supplier_id, po_id, item_sku, quantity_billed, invoice_unit_price, total_amount, invoice_date, source_filename)
                 values (:t, :inv, :s, :po, :sku, :q, :p, :tot, :d, :fn)"""),
        {"t": tenant_id, "inv": invoice_number, "s": supplier_id, "po": po_id, "sku": sku,
         "q": qty, "p": unit_price, "tot": total, "d": invoice_date, "fn": f"{invoice_number}.pdf"}
    )


def generate_invoices_for_tenant(conn, tenant_id, contract_data, tenant_label, ground_truth):
    """Generates 40 invoices per tenant across 5 labeled categories."""
    invoice_counter = 1
    po_counter = 1

    def next_invoice_no():
        nonlocal invoice_counter
        n = f"INV-{tenant_label}-{invoice_counter:04d}"
        invoice_counter += 1
        return n

    def next_po_no():
        nonlocal po_counter
        n = f"PO-{tenant_label}-{po_counter:04d}"
        po_counter += 1
        return n

    outcomes = (
        ["CLEAN"] * 24 +
        ["PRICE_VARIANCE"] * 8 +
        ["DUPLICATE_PO"] * 4 +
        ["MISSING_CONTRACT"] * 2 +
        ["EDGE_CASE"] * 2
    )
    random.shuffle(outcomes)

    last_po_for_duplicate = None  # holds (po_no, po_id) tuple while pairing up a duplicate

    for outcome in outcomes:
        contract = random.choice(contract_data)
        supplier_id = contract["supplier_id"]
        sku, agreed_price = random.choice(list(contract["items"].items()))
        qty = random.randint(5, 50)
        order_date = date(2026, random.randint(1, 6), random.randint(1, 28))
        invoice_date = order_date + timedelta(days=random.randint(3, 20))

        if outcome == "CLEAN":
            po_no = next_po_no()
            po_id = create_po(conn, tenant_id, po_no, supplier_id, sku, qty, agreed_price, order_date)
            inv_no = next_invoice_no()
            create_invoice(conn, tenant_id, inv_no, supplier_id, po_id, sku, qty, agreed_price, invoice_date)
            ground_truth.append({"invoice_number": inv_no, "expected_status": "APPROVED", "reason": "Matches contract price exactly"})

        elif outcome == "PRICE_VARIANCE":
            po_no = next_po_no()
            po_id = create_po(conn, tenant_id, po_no, supplier_id, sku, qty, agreed_price, order_date)
            inflated_price = round(agreed_price * random.uniform(1.08, 1.35), 2)
            inv_no = next_invoice_no()
            create_invoice(conn, tenant_id, inv_no, supplier_id, po_id, sku, qty, inflated_price, invoice_date)
            ground_truth.append({
                "invoice_number": inv_no, "expected_status": "LEAKAGE_DETECTED", "check_type": "PRICE_VARIANCE",
                "expected_agreed_price": agreed_price, "expected_invoiced_price": inflated_price,
                "reason": f"Billed {inflated_price} vs contracted {agreed_price}"
            })

        elif outcome == "DUPLICATE_PO":
            # Reuse the SAME po (same UUID) across two invoices to force a real duplicate
            if last_po_for_duplicate is None:
                po_no = next_po_no()
                po_id = create_po(conn, tenant_id, po_no, supplier_id, sku, qty, agreed_price, order_date)
                last_po_for_duplicate = (po_no, po_id)
            else:
                po_no, po_id = last_po_for_duplicate
                last_po_for_duplicate = None
            inv_no = next_invoice_no()
            create_invoice(conn, tenant_id, inv_no, supplier_id, po_id, sku, qty, agreed_price, invoice_date)
            ground_truth.append({
                "invoice_number": inv_no, "expected_status": "LEAKAGE_DETECTED", "check_type": "DUPLICATE_PO",
                "reason": f"PO {po_no} billed on more than one invoice"
            })
            

        elif outcome == "MISSING_CONTRACT":
            off_catalog_sku = random.choice([s for s, _, _ in SKU_CATALOG if s not in contract["items"]])
            po_no = next_po_no()
            fallback_price = round(random.uniform(100, 5000), 2)
            po_id = create_po(conn, tenant_id, po_no, supplier_id, off_catalog_sku, qty, fallback_price, order_date)
            inv_no = next_invoice_no()
            create_invoice(conn, tenant_id, inv_no, supplier_id, po_id, off_catalog_sku, qty, fallback_price, invoice_date)
            ground_truth.append({
                "invoice_number": inv_no, "expected_status": "LEAKAGE_DETECTED", "check_type": "MISSING_CONTRACT",
                "reason": f"SKU {off_catalog_sku} has no active contract with this supplier"
            })

        elif outcome == "EDGE_CASE":
            # Deliberately ambiguous: tiny variance within plausible rounding/FX tolerance (~1.5%)
            po_no = next_po_no()
            po_id = create_po(conn, tenant_id, po_no, supplier_id, sku, qty, agreed_price, order_date)
            borderline_price = round(agreed_price * random.uniform(1.005, 1.02), 2)
            inv_no = next_invoice_no()
            create_invoice(conn, tenant_id, inv_no, supplier_id, po_id, sku, qty, borderline_price, invoice_date)
            ground_truth.append({
                "invoice_number": inv_no, "expected_status": "AMBIGUOUS", "check_type": "PRICE_VARIANCE_MINOR",
                "expected_agreed_price": agreed_price, "expected_invoiced_price": borderline_price,
                "reason": "Held out for Phase 11 eval — tests reconciliation engine's variance threshold, not used to tune it",
                "note": "DO NOT use this case to calibrate Phase 3 logic"
            })


def main():
    ground_truth = {"tenants": []}

    with engine.connect() as conn:
        print("Connected as:", conn.execute(text("select current_user")).scalar())

        for idx, tenant_name in enumerate(TENANT_NAMES):
            tenant_label = "MM" if idx == 0 else "NR"
            tenant_id = create_tenant(conn, tenant_name)
            conn.commit()

            set_tenant(conn, tenant_id)
            supplier_ids = create_suppliers(conn, tenant_id, n=8)
            conn.commit()

            contract_data = create_contracts_and_items(conn, tenant_id, supplier_ids)
            conn.commit()

            tenant_ground_truth = []
            generate_invoices_for_tenant(conn, tenant_id, contract_data, tenant_label, tenant_ground_truth)
            conn.commit()

            ground_truth["tenants"].append({
                "tenant_id": str(tenant_id),
                "tenant_name": tenant_name,
                "tenant_label": tenant_label,
                "invoices": tenant_ground_truth
            })
            print(f"Generated tenant '{tenant_name}' ({tenant_label}): "
                  f"{len(supplier_ids)} suppliers, {len(contract_data)} contracts, "
                  f"{len(tenant_ground_truth)} invoices")

    os.makedirs("data/synthetic", exist_ok=True)
    with open("data/synthetic/ground_truth.json", "w") as f:
        json.dump(ground_truth, f, indent=2, default=str)

    print("\n✅ Synthetic data generated. Ground truth saved to data/synthetic/ground_truth.json")


if __name__ == "__main__":
    main()
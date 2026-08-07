# checks the engine against our labeled ground truth
import os
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(override=True)
engine = create_engine(os.getenv("DATABASE_URL"))

with open("data/synthetic/ground_truth.json") as f:
    ground_truth = json.load(f)

tp = fp = tn = fn = 0
check_type_correct = 0
check_type_total = 0
ambiguous_results = []

with engine.connect() as conn:
    for tenant in ground_truth["tenants"]:
        conn.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": tenant["tenant_id"]})

        for expected in tenant["invoices"]:
            actual = conn.execute(text("""
                select reconciliation_status from invoices where invoice_number = :inv
            """), {"inv": expected["invoice_number"]}).scalar()

            if expected["expected_status"] == "AMBIGUOUS":
                ambiguous_results.append({
                    "invoice": expected["invoice_number"],
                    "engine_decided": actual,
                    "note": "Held-out edge case — not scored, informational only"
                })
                continue

            expected_positive = expected["expected_status"] == "LEAKAGE_DETECTED"
            actual_positive = actual == "LEAKAGE_DETECTED"

            if expected_positive and actual_positive:
                tp += 1
            elif not expected_positive and not actual_positive:
                tn += 1
            elif not expected_positive and actual_positive:
                fp += 1
            elif expected_positive and not actual_positive:
                fn += 1

            if expected_positive and actual_positive:
                check_type_total += 1
                actual_types = conn.execute(text("""
                    select check_type from reconciliation_results
                    where invoice_id = (select id from invoices where invoice_number = :inv)
                """), {"inv": expected["invoice_number"]}).fetchall()
                found_types = {r.check_type for r in actual_types}
                if expected["check_type"] in found_types:
                    check_type_correct += 1

precision = tp / (tp + fp) if (tp + fp) else 0
recall = tp / (tp + fn) if (tp + fn) else 0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

print("=== Reconciliation Engine Evaluation ===")
print(f"TP={tp}  FP={fp}  TN={tn}  FN={fn}")
print(f"Precision: {precision:.2%}")
print(f"Recall:    {recall:.2%}")
print(f"F1:        {f1:.2%}")
print(f"Check-type accuracy (correct category among true positives): {check_type_correct}/{check_type_total}")

print("\n=== Held-out ambiguous edge cases (informational — not scored) ===")
for r in ambiguous_results:
    print(f"  {r['invoice']}: engine decided '{r['engine_decided']}'")
"""Scores Phase 4's extraction pipeline against hand-labeled ground truth."""
import json
from app.ingestion.pdf_parser import parse_pdf_text
from app.ingestion.extractor import extract_with_retry


def eval_extraction(conn, tenant_id: str) -> dict:
    with open("data/eval/extraction_golden.json") as f:
        golden = json.load(f)

    results = {"contracts": [], "invoices": []}

    for case in golden["contracts"]:
        content = parse_pdf_text(case["file"])
        try:
            extracted = extract_with_retry(conn, tenant_id, content, "contract")
            conn.commit()
            correct = extracted.contract_number == case["expected"]["contract_number"]
            price_matches = all(
                any(ei.item_sku == exp["item_sku"] and abs(ei.agreed_unit_price - exp["agreed_unit_price"]) < 0.01
                    for ei in extracted.items)
                for exp in case["expected"]["items"]
            )
            results["contracts"].append({"file": case["file"], "contract_number_correct": correct, "prices_correct": price_matches})
        except Exception as e:
            results["contracts"].append({"file": case["file"], "error": str(e)})

    for case in golden["invoices"]:
        content = parse_pdf_text(case["file"])
        try:
            extracted = extract_with_retry(conn, tenant_id, content, "invoice")
            conn.commit()
            exp = case["expected"]
            correct = (
                extracted.invoice_number == exp["invoice_number"] and
                extracted.quantity_billed == exp["quantity_billed"] and
                abs(extracted.invoice_unit_price - exp["invoice_unit_price"]) < 0.01 and
                abs(extracted.total_amount - exp["total_amount"]) < 0.01
            )
            results["invoices"].append({"file": case["file"], "fully_correct": correct})
        except Exception as e:
            results["invoices"].append({"file": case["file"], "error": str(e)})

    contract_accuracy = sum(1 for r in results["contracts"] if r.get("contract_number_correct") and r.get("prices_correct")) / len(results["contracts"])
    invoice_accuracy = sum(1 for r in results["invoices"] if r.get("fully_correct")) / len(results["invoices"])

    return {"contract_accuracy": contract_accuracy, "invoice_accuracy": invoice_accuracy, "details": results}
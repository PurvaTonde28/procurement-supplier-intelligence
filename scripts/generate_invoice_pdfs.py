"""Generates deliberately messy/realistic invoice PDFs to test the
extraction pipeline against real formatting noise, not clean data."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

OUTPUT_DIR = "data/synthetic/invoices"
os.makedirs(OUTPUT_DIR, exist_ok=True)
styles = getSampleStyleSheet()

INVOICES = [
    {
        "filename": "sample_invoice_clean.pdf",
        "body": """ACME LOGISTICS INC.<br/>Invoice ID: ACME-2026-9811   Date: 2026-08-01<br/><br/>
Bill To: Enterprise Operations Hub<br/>
Item SKU: LOG-DIS-01   Description: Standard Routing   Qty: 10   Unit Price: 450.00 INR<br/>
Total Balance Due: 4500.00 INR<br/>Payment Terms: Net 30"""
    },
    {
        "filename": "sample_invoice_messy.pdf",
        "body": """*** APEX PACKAGING CORP ***<br/>
Inv# APX-99821-X    dated  28/07/2026<br/><br/>
Ship-to: Procurement Division Central<br/>
SKU PKG-BOX-XL  ..... Extra Large Crates ..... qty=500 ..... @ 42.00 INR each<br/>
TOTAL: Rs. 21,000.00<br/>"""
    },
    {
        "filename": "sample_invoice_ambiguous_date.pdf",
        "body": """Globex Freight Corp — Tax Invoice<br/>
Reference No: GLX-2026-0456<br/>
Date: 03-08-2026<br/><br/>
FRT-SEA-20 x 2 units, unit rate 12500.00 INR<br/>
Grand Total = 25000.00 INR"""
    },
]


def build_pdf(inv):
    path = os.path.join(OUTPUT_DIR, inv["filename"])
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm)
    story = [Paragraph(inv["body"], styles["BodyText"]), Spacer(1, 0.4*cm)]
    doc.build(story)
    print(f"Created {path}")


if __name__ == "__main__":
    for inv in INVOICES:
        build_pdf(inv)
    print("\n✅ Sample invoice PDFs generated.")
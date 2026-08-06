"""
Generates a small set of realistic contract PDF documents with actual
clause text, matched to specific contract_items already in the DB.
Used later by Phase 4 (extraction) and Phase 5 (RAG/citations).
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

OUTPUT_DIR = "data/synthetic/contracts"
os.makedirs(OUTPUT_DIR, exist_ok=True)

styles = getSampleStyleSheet()

CONTRACTS = [
    {
        "filename": "CON-001_MeridianLogistics.pdf",
        "title": "Master Supply Agreement — Contract No. CON-001",
        "clauses": [
            ("1. Parties", "This agreement is entered into between Meridian Manufacturing Pvt Ltd (\"Buyer\") "
                            "and the designated Supplier for the provision of freight and logistics services."),
            ("2. Pricing", "The Supplier agrees to provide Standard Freight Routing (SKU: LOG-DIS-01) at a fixed "
                            "unit price of INR 450.00 per shipment for the duration of this agreement. Any price "
                            "revision requires 30 days written notice and mutual approval."),
            ("3. Payment Terms", "Payment terms are Net 30 from the date of invoice receipt, subject to "
                                  "reconciliation against agreed contract pricing."),
            ("4. Penalties", "Any invoice submitted above the agreed unit price without prior written amendment "
                              "to this contract shall be treated as a billing discrepancy subject to audit and "
                              "recovery."),
        ]
    },
    {
        "filename": "CON-002_PackagingSupply.pdf",
        "title": "Master Supply Agreement — Contract No. CON-002",
        "clauses": [
            ("1. Parties", "This agreement covers the supply of packaging materials between Meridian "
                            "Manufacturing Pvt Ltd and the Supplier."),
            ("2. Pricing", "Extra Large Shipping Crate (SKU: PKG-BOX-XL) is priced at INR 35.50 per unit. "
                            "Small Shipping Carton (SKU: PKG-BOX-SM) is priced at INR 8.75 per unit. These rates "
                            "are fixed and non-negotiable except through formal contract amendment."),
            ("3. Volume Commitments", "Buyer commits to a minimum monthly order volume of 200 units combined "
                                       "across both SKUs."),
            ("4. Quality Standards", "All packaging materials must meet ISTA 3A transit testing standards."),
        ]
    },
    {
        "filename": "CON-003_ITEquipment.pdf",
        "title": "Master Supply Agreement — Contract No. CON-003",
        "clauses": [
            ("1. Parties", "This agreement governs the supply of IT equipment to Northbridge Retail Group."),
            ("2. Pricing", "Business Laptop Unit (SKU: IT-LAPTOP-01) is contracted at INR 52,000.00 per unit, "
                            "inclusive of a 3-year standard warranty."),
            ("3. Delivery", "Standard delivery lead time is 15 business days from purchase order confirmation."),
            ("4. Price Protection", "The Supplier warrants that pricing under this contract will not be exceeded "
                                     "on any invoice without prior written change order signed by both parties."),
        ]
    },
]


def build_pdf(contract):
    path = os.path.join(OUTPUT_DIR, contract["filename"])
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm)
    story = [Paragraph(contract["title"], styles["Title"]), Spacer(1, 0.5*cm)]
    for heading, text in contract["clauses"]:
        story.append(Paragraph(heading, styles["Heading2"]))
        story.append(Paragraph(text, styles["BodyText"]))
        story.append(Spacer(1, 0.4*cm))
    doc.build(story)
    print(f"Created {path}")


if __name__ == "__main__":
    for c in CONTRACTS:
        build_pdf(c)
    print("\n✅ Contract PDFs generated.")
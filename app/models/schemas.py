"""Pydantic schemas for validated extraction output. tenant_id is required
on every schema — extracted data is tenant-scoped from creation, not after."""
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import date


class ContractItemExtraction(BaseModel):
    item_sku: str
    item_description: Optional[str] = None
    agreed_unit_price: float = Field(gt=0)


class ContractExtraction(BaseModel):
    tenant_id: str
    contract_number: str
    supplier_name: str
    items: List[ContractItemExtraction]
    payment_terms: Optional[str] = None

    @field_validator("items")
    @classmethod
    def must_have_at_least_one_item(cls, v):
        if not v:
            raise ValueError("A contract must extract at least one priced item")
        return v


class InvoiceExtraction(BaseModel):
    tenant_id: str
    invoice_number: str
    supplier_name: str
    item_sku: str
    quantity_billed: int = Field(gt=0)
    invoice_unit_price: float = Field(gt=0)
    total_amount: float = Field(gt=0)
    invoice_date: date

    @field_validator("total_amount")
    @classmethod
    def total_should_roughly_match_qty_times_price(cls, v, info):
        qty = info.data.get("quantity_billed")
        price = info.data.get("invoice_unit_price")
        if qty and price:
            expected = qty * price
            if abs(v - expected) > max(1.0, expected * 0.02):  # 2% tolerance for rounding
                raise ValueError(
                    f"total_amount {v} doesn't match quantity_billed * invoice_unit_price ({expected:.2f})"
                )
        return v
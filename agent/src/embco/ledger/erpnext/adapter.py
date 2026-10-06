"""Read-only LedgerAdapter backed by ERPNext.

Every request goes through the same permission and validation layer a person would use,
with a dedicated low-privilege API user.
"""

import httpx

from embco.ledger.erpnext import mappers
from embco.ledger.erpnext.client import FrappeClient
from embco.ledger.models import (
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    Supplier,
)

DEFAULT_WALLET_FIELD = "custom_wallet_address"
DEFAULT_PAYEE_WALLET_FIELD = "custom_payee_wallet"


class ErpnextAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        wallet_field: str = DEFAULT_WALLET_FIELD,
        payee_wallet_field: str = DEFAULT_PAYEE_WALLET_FIELD,
        company: str | None = None,
        client: httpx.Client | None = None,
        timeout: int = 20,
    ) -> None:
        self._wallet_field = wallet_field
        self._payee_wallet_field = payee_wallet_field
        self._company_filter = [["company", "=", company]] if company else []
        self._frappe = FrappeClient(base_url, api_key, api_secret, client=client, timeout=timeout)

    def get_supplier(self, name: str) -> Supplier:
        return mappers.to_supplier(self._frappe.get_doc("Supplier", name), self._wallet_field)

    def get_purchase_order(self, name: str) -> PurchaseOrder:
        return mappers.to_purchase_order(self._frappe.get_doc("Purchase Order", name))

    def get_purchase_receipt(self, name: str) -> PurchaseReceipt:
        return mappers.to_purchase_receipt(self._frappe.get_doc("Purchase Receipt", name))

    def get_purchase_invoice(self, name: str) -> PurchaseInvoice:
        return mappers.to_purchase_invoice(self._frappe.get_doc("Purchase Invoice", name))

    def list_unpaid_purchase_invoices(self) -> list[PurchaseInvoice]:
        names = self._frappe.list_names(
            "Purchase Invoice",
            filters=[["docstatus", "=", 1], ["outstanding_amount", ">", 0], *self._company_filter],
            order_by="due_date asc",
        )
        return [self.get_purchase_invoice(name) for name in names]

    def list_supplier_invoices(self, supplier: str) -> list[PurchaseInvoice]:
        names = self._frappe.list_names(
            "Purchase Invoice",
            filters=[["docstatus", "=", 1], ["supplier", "=", supplier], *self._company_filter],
            order_by="posting_date asc, name asc",
        )
        return [self.get_purchase_invoice(name) for name in names]

    def list_payments(self, supplier: str) -> list[PaymentRecord]:
        rows = self._frappe.list_rows(
            "Payment Entry",
            fields=["name", "party", "posting_date", "paid_amount", self._payee_wallet_field],
            filters=[
                ["docstatus", "=", 1],
                ["party_type", "=", "Supplier"],
                ["party", "=", supplier],
                *self._company_filter,
            ],
            order_by="posting_date asc, name asc",
        )
        return [mappers.to_payment(row, self._payee_wallet_field) for row in rows]

"""LedgerAdapter backed by ERPNext, plus the one write the agent makes: a payment entry for a
payment that is final on chain.

Every request goes through the same permission and validation layer a person would use,
with a dedicated low-privilege API user.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from embco.ledger.base import LedgerError
from embco.ledger.erpnext import mappers
from embco.ledger.erpnext.client import FrappeClient
from embco.ledger.models import (
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    SettledPayment,
    Supplier,
)

DEFAULT_WALLET_FIELD = "custom_wallet_address"
DEFAULT_PAYEE_WALLET_FIELD = "custom_payee_wallet"
TX_HASH_FIELD = "custom_tx_hash"
DECISION_FIELD = "custom_agent_decision"
DRAFT_PAYMENT = "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry"


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
        paid_from: str | None = None,
        client: httpx.Client | None = None,
        timeout: int = 20,
    ) -> None:
        self._wallet_field = wallet_field
        self._payee_wallet_field = payee_wallet_field
        self._company_filter = [["company", "=", company]] if company else []
        self._paid_from = paid_from
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

    def record_payment(self, payment: SettledPayment) -> str:
        """ERPNext drafts the entry from the invoice (accounts, party, references), so the agent
        does not pick ledger accounts itself, except the account the money left from when
        `paid_from` is set. The draft must match what was paid on chain, or nothing is written."""
        existing = self._frappe.list_names(
            "Payment Entry",
            filters=[[TX_HASH_FIELD, "=", payment.tx_hash], ["docstatus", "!=", 2]],
            order_by="creation asc",
        )
        if existing:
            return existing[0]
        draft = self._frappe.call_method(
            DRAFT_PAYMENT, {"dt": "Purchase Invoice", "dn": payment.invoice}
        )
        if not isinstance(draft, dict):
            raise LedgerError(f"ERPNext drafted no payment entry for {payment.invoice}")
        _check_draft(draft, payment)
        if self._paid_from:
            # ERPNext fills the account's currency and balance again when it validates
            draft.update(paid_from=self._paid_from, paid_from_account_currency=None,
                         paid_from_account_balance=None)
        draft.update({
            "reference_no": payment.tx_hash,
            "reference_date": payment.paid_on.isoformat(),
            "posting_date": payment.paid_on.isoformat(),
            TX_HASH_FIELD: payment.tx_hash,
            self._payee_wallet_field: payment.payee_wallet,
            DECISION_FIELD: payment.note,
            "remarks": f"Paid in USDC on Arc by the embco agent. {payment.note}",
            "docstatus": 1,
        })
        return self._frappe.insert_doc(draft)["name"]


def _check_draft(draft: dict[str, Any], payment: SettledPayment) -> None:
    refs = draft.get("references") or []
    matches = (
        draft.get("party") == payment.supplier
        and _decimal(draft.get("paid_amount")) == payment.amount
        and len(refs) == 1
        and refs[0].get("reference_name") == payment.invoice
        and _decimal(refs[0].get("allocated_amount")) == payment.amount
    )
    if not matches:
        raise LedgerError(
            f"ERPNext's payment draft for {payment.invoice} does not match the payment made on "
            "chain; record it by hand"
        )


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None

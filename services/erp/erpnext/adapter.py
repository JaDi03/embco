"""LedgerAdapter backed by ERPNext, plus the one write the agent makes: a payment entry for a
payment that is final on chain.

Every request goes through the same permission and validation layer a person would use,
with a dedicated low-privilege API user.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from services.erp.base import LedgerError
from services.erp.erpnext import bank_wallets, mappers
from services.erp.erpnext.client import FrappeClient
from services.erp.models import (
    OwnerMark,
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    SettledPayment,
    Supplier,
    WalletChange,
)

DEFAULT_WALLET_FIELD = "custom_wallet_address"
DEFAULT_PAYEE_WALLET_FIELD = "custom_payee_wallet"
TX_HASH_FIELD = "custom_tx_hash"
DECISION_FIELD = "custom_agent_decision"
DRAFT_PAYMENT = "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry"
OWNER_ANSWER_FIELD = "custom_owner_answer"
OWNER_NOTE_FIELD = "custom_owner_note"
# What a payment's extra fields may not touch: the draft is checked against the chain.
PROTECTED_PAYMENT_FIELDS = frozenset({
    "payment_type", "party", "party_type", "paid_amount", "received_amount", "references",
    "reference_no", "reference_date", "posting_date", "docstatus",
})
TESTNET_REMARK = "TESTNET: paid in test USDC, no real money moved. Do not submit."
DOC_INFO = "frappe.desk.form.load.get_docinfo"  # change history of a document the user can read


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
        payment_extra: Mapping[str, str] | None = None,
        wallet_bank: str | None = None,
        draft_payments: bool = False,
        client: httpx.Client | None = None,
        timeout: int = 20,
    ) -> None:
        self._wallet_field = wallet_field
        self._payee_wallet_field = payee_wallet_field
        self._company_filter = [["company", "=", company]] if company else []
        self._paid_from = paid_from
        self._payment_extra = dict(payment_extra or {})
        self._wallet_bank = wallet_bank  # set: wallets are Bank Account rows, no custom fields
        self._draft_payments = draft_payments  # testnet: test USDC must not settle real invoices
        clash = sorted(PROTECTED_PAYMENT_FIELDS & self._payment_extra.keys())
        if clash:
            raise ValueError(f"payment fields the agent sets itself: {', '.join(clash)}")
        self._frappe = FrappeClient(base_url, api_key, api_secret, client=client, timeout=timeout)

    def get_supplier(self, name: str) -> Supplier:
        doc = self._frappe.get_doc("Supplier", name)
        if not self._wallet_bank:
            return mappers.to_supplier(doc, self._wallet_field)
        accounts = bank_wallets.active_accounts(self._frappe, self._wallet_bank, name)
        wallet, problem = bank_wallets.wallet_on_file(accounts, self._wallet_bank)
        return Supplier(name=doc["name"], wallet_address=wallet, wallet_problem=problem,
                        disabled=bool(doc.get("disabled")))

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
        """Submitted payments; on testnet also the agent's own drafts, which stand for a payment
        that is final on chain but must not settle the invoice in the ERP."""
        wallet_field = "remarks" if self._wallet_bank else self._payee_wallet_field
        fields = ["name", "party", "posting_date", "paid_amount", "docstatus", "remarks",
                  wallet_field]
        rows = self._frappe.list_rows(
            "Payment Entry",
            fields=list(dict.fromkeys(fields)),
            filters=[
                ["docstatus", "in", [0, 1] if self._draft_payments else [1]],
                ["party_type", "=", "Supplier"],
                ["party", "=", supplier],
                *self._company_filter,
            ],
            order_by="posting_date asc, name asc",
        )
        rows = [row for row in rows if int(row.get("docstatus", 1)) == 1
                or (self._draft_payments
                    and str(row.get("remarks") or "").startswith(TESTNET_REMARK))]
        if self._wallet_bank:
            rows = [{**row, "remarks": bank_wallets.wallet_from_remarks(row.get("remarks"))}
                    for row in rows]
        return [mappers.to_payment(row, wallet_field) for row in rows]

    def wallet_changes(self, supplier: str) -> list[WalletChange]:
        if self._wallet_bank:
            return self._bank_wallet_changes(supplier)
        return [WalletChange(old=old or None, new=new or None, changed_by=by, changed_at=at)
                for old, new, by, at, _ in self._field_changes("Supplier", supplier,
                                                              self._wallet_field)]

    def _bank_wallet_changes(self, supplier: str) -> list[WalletChange]:
        """Edits of each active account's number, plus its creation, newest first."""
        changes = []
        for account in bank_wallets.active_accounts(self._frappe, self._wallet_bank, supplier):
            edits = self._field_changes("Bank Account", account["name"], bank_wallets.WALLET_FIELD)
            changes += [WalletChange(old=old or None, new=new or None, changed_by=by, changed_at=at)
                        for old, new, by, at, _ in edits]
            first = edits[-1][0] if edits else account.get(bank_wallets.WALLET_FIELD)
            created = bank_wallets.creation_change(account, first or None)
            if created:
                changes.append(created)
        return sorted(changes, key=lambda c: c.changed_at, reverse=True)

    def owner_mark(self, invoice: str) -> OwnerMark | None:
        doc = self._frappe.get_doc("Purchase Invoice", invoice)
        verdict = str(doc.get(OWNER_ANSWER_FIELD) or "").strip()
        if not verdict:
            return None
        for _, new, by, at, change_id in self._field_changes("Purchase Invoice", invoice,
                                                             OWNER_ANSWER_FIELD):
            if str(new or "").strip() == verdict:
                note = str(doc.get(OWNER_NOTE_FIELD) or "").strip()
                return OwnerMark(verdict=verdict, note=note, set_by=by, set_at=at,
                                 change_id=change_id)
        return None  # no recorded change: who set it is unknown

    def _field_changes(self, doctype: str, name: str, fieldname: str) -> list[tuple]:
        """(old, new, who, when, change id) for each edit of one field, newest first."""
        info = self._frappe.get_method(DOC_INFO, {"doctype": doctype, "name": name})
        versions = info.get("versions") if isinstance(info, dict) else None
        changes = []
        for version in versions or []:
            try:
                changed = json.loads(version.get("data") or "{}").get("changed") or []
                at = datetime.fromisoformat(str(version["creation"]))
            except (ValueError, KeyError, AttributeError):
                continue
            for field, old, new in (c for c in changed if len(c) == 3):
                if field == fieldname:
                    changes.append((old, new, str(version.get("owner") or "unknown"), at,
                                    str(version.get("name") or "")))
        return sorted(changes, key=lambda c: c[3], reverse=True)

    def record_payment(self, payment: SettledPayment) -> str:
        """ERPNext drafts the entry from the invoice (accounts, party, references), so the agent
        does not pick ledger accounts itself, except the account the money left from when
        `paid_from` is set. The draft must match what was paid on chain, or nothing is written."""
        existing = self._frappe.list_names(
            "Payment Entry",
            filters=[["reference_no", "=", payment.tx_hash], ["docstatus", "!=", 2]],
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
        draft.update(self._payment_extra)  # fields this ERP requires, e.g. a payment form
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
        if self._wallet_bank:
            accounts = bank_wallets.active_accounts(self._frappe, self._wallet_bank,
                                                    payment.supplier)
            draft.update({
                "custom_remarks": 1,  # keep these remarks: they hold the wallet that was paid
                "remarks": "Paid in USDC on Arc by the embco agent "
                           f"{bank_wallets.paid_wallet_remark(payment.payee_wallet)}. "
                           f"{payment.note}",
            })
            account = bank_wallets.account_for(accounts, payment.payee_wallet)
            if account:
                draft["party_bank_account"] = account
        if self._draft_payments:
            draft.update({
                "docstatus": 0,
                "custom_remarks": 1,
                "remarks": f"{TESTNET_REMARK} {draft['remarks']}",
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

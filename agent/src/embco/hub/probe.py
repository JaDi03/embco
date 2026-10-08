"""Before a shop is saved, check what its ERP keys can do, without writing anything.

The owner sees each check in the dashboard. Reading purchases is required; drafting a payment
and reading supplier wallets are reported so the owner knows what is missing.
"""

from dataclasses import dataclass, field
from typing import Any

from embco.ledger.base import LedgerError
from embco.ledger.erpnext.adapter import DRAFT_PAYMENT
from embco.ledger.erpnext.client import FrappeClient


@dataclass
class ProbeResult:
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks if c["required"])

    def add(self, name: str, ok: bool, detail: str, *, required: bool = False) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail, "required": required})

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": self.checks}


def probe(frappe: FrappeClient, company: str, wallet_bank: str | None) -> ProbeResult:
    result = ProbeResult()
    try:
        frappe.get_method("frappe.auth.get_logged_user", {})
        result.add("keys", True, "the ERP accepted the API key", required=True)
    except LedgerError as error:
        result.add("keys", False, str(error), required=True)
        return result
    try:
        found = frappe.list_rows("Company", ["name"], [["name", "=", company]], "name asc")
        result.add("company", bool(found), f"company {company} " +
                   ("found" if found else "not found or not visible to this user"),
                   required=True)
        unpaid = frappe.list_rows(
            "Purchase Invoice", ["name"],
            [["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["company", "=", company]],
            "due_date asc", limit=500)
        result.add("invoices", True, f"{len(unpaid)} unpaid purchase invoices", required=True)
    except LedgerError as error:
        result.add("invoices", False, str(error), required=True)
        return result
    if unpaid:
        try:
            frappe.call_method(DRAFT_PAYMENT, {"dt": "Purchase Invoice",
                                               "dn": unpaid[0]["name"]})
            result.add("payments", True, "ERPNext drafts a payment for an invoice (nothing saved)")
        except LedgerError as error:
            result.add("payments", False, str(error))
    if wallet_bank:
        try:
            filters = [["bank", "=", wallet_bank], ["party_type", "=", "Supplier"],
                       ["disabled", "=", 0]]
            accounts = frappe.list_rows("Bank Account", ["name"], filters, "name asc", limit=500)
            result.add("wallets", True, f"{len(accounts)} supplier wallets at {wallet_bank}")
        except LedgerError as error:
            result.add("wallets", False, str(error))
    return result

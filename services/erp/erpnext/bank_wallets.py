"""Supplier wallets kept in standard ERPNext Bank Account rows, for sites without custom fields.

The shop adds one Bank Account per supplier at a bank named for the network (for example
"USDC on Arc") with the wallet as its account number. The supplier's real bank account and its
default account stay as they are. Which wallet was paid is written in the Payment Entry remarks,
which ERPNext does not allow to change once the entry is submitted.
"""

import re
from datetime import datetime
from typing import Any

from services.erp.erpnext.client import FrappeClient
from services.erp.models import WalletChange

WALLET_FIELD = "bank_account_no"
_PAID_TO = re.compile(r"to wallet (0x[0-9a-fA-F]{40})\b")


def active_accounts(frappe: FrappeClient, bank: str, supplier: str) -> list[dict[str, Any]]:
    return frappe.list_rows(
        "Bank Account",
        fields=["name", WALLET_FIELD, "owner", "creation"],
        filters=[["party_type", "=", "Supplier"], ["party", "=", supplier],
                 ["bank", "=", bank], ["disabled", "=", 0]],
        order_by="creation asc",
    )


def wallet_on_file(accounts: list[dict[str, Any]], bank: str) -> tuple[str | None, str | None]:
    """(wallet, problem). Two different wallets are never chosen between."""
    distinct: dict[str, str] = {}
    for a in accounts:  # oldest first: the same wallet in other case keeps its first spelling
        wallet = str(a.get(WALLET_FIELD) or "").strip()
        if wallet:
            distinct.setdefault(wallet.lower(), wallet)
    if len(distinct) > 1:
        return None, (f"{len(accounts)} active accounts at {bank} with different wallets; "
                      "leave one active in the ERP")
    return (next(iter(distinct.values())) if distinct else None), None


def account_for(accounts: list[dict[str, Any]], wallet: str) -> str | None:
    for a in accounts:
        if str(a.get(WALLET_FIELD) or "").strip().lower() == wallet.lower():
            return a["name"]
    return None


def creation_change(account: dict[str, Any], first_wallet: str | None) -> WalletChange | None:
    """Whoever created the account set its first wallet."""
    try:
        at = datetime.fromisoformat(str(account["creation"]))
    except (KeyError, ValueError):
        return None
    if not first_wallet:
        return None
    return WalletChange(old=None, new=first_wallet,
                        changed_by=str(account.get("owner") or "unknown"), changed_at=at)


def paid_wallet_remark(wallet: str) -> str:
    return f"to wallet {wallet}"


def wallet_from_remarks(remarks: Any) -> str | None:
    match = _PAID_TO.search(str(remarks or ""))
    return match.group(1) if match else None

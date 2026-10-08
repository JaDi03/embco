"""Email a supplier through the shop's own ERPNext, so the message leaves from the shop's
address and stays in the supplier's history in the ERP.

The email carries no link on purpose: it tells the supplier which address to type. A supplier
used to signing only on a site it typed itself is hard to phish with a look-alike email.
"""

from html import escape

from embco.ledger.base import LedgerError
from embco.ledger.erpnext.client import FrappeClient

SEND_EMAIL = "frappe.core.doctype.communication.email.make"


def email_supplier_notice(frappe: FrappeClient, supplier: str, company: str, site: str,
                          wallet: str | None, signature_needed: bool) -> None:
    """Tell the supplier to visit its page, at the email on its record in the ERP."""
    doc = frappe.get_doc("Supplier", supplier)
    recipient = str(doc.get("email_id") or "").strip()
    if not recipient:
        raise LedgerError(f"{supplier} has no email in the ERP; add one to its contact")
    if signature_needed:
        subject = f"{company}: confirm the wallet you are paid to"
        ask = (f"{escape(company)} asks you to confirm the wallet you are paid to"
               + (f": <b>{escape(wallet)}</b>" if wallet else "") + ".")
    else:
        subject = f"{company}: your invoices and payments"
        ask = f"You can follow your invoices and payments from {escape(company)}."
    content = (
        f"<p>Hello {escape(supplier)},</p>"
        f"<p>{ask}</p>"
        f"<p>Type <b>{escape(site)}</b> in your browser yourself and sign in with that wallet. "
        "Do not use links in emails that ask you to sign.</p>"
        "<p>Signing in or confirming your wallet moves no money. Nobody from embco or the shop "
        "will ever ask for your private key or recovery phrase.</p>"
    )
    frappe.call_method(SEND_EMAIL, {
        "doctype": "Supplier",
        "name": supplier,
        "recipients": recipient,
        "subject": subject,
        "content": content,
        "communication_medium": "Email",
        "sent_or_received": "Sent",
        "send_email": "1",
    })

"""Prepare an ERPNext instance for the embco agent. Safe to run more than once.

Run inside an ERPNext bench console, from /home/frappe:
    from connector.prereqs import apply; apply()
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

OWNER_LEVEL = 5  # unused by ERPNext's Purchase Invoice

CUSTOM_FIELDS = [
    {"dt": "Supplier", "fieldname": "custom_wallet_address", "label": "Wallet address (Arc)",
     "fieldtype": "Data", "insert_after": "supplier_name",
     "description": "USDC wallet of this supplier on Arc"},
    {"dt": "Payment Entry", "fieldname": "custom_tx_hash", "label": "Arc transaction hash",
     "fieldtype": "Data", "insert_after": "reference_no", "read_only": 1},
    {"dt": "Payment Entry", "fieldname": "custom_payee_wallet", "label": "Payee wallet used",
     "fieldtype": "Data", "insert_after": "custom_tx_hash", "read_only": 1},
    {"dt": "Payment Entry", "fieldname": "custom_agent_decision", "label": "Agent decision",
     "fieldtype": "Small Text", "insert_after": "custom_payee_wallet", "read_only": 1},
    # the owner answers the agent's questions on the invoice; a permission level only the
    # roles in LEVEL_PERMISSIONS reach keeps everyday staff from writing the answer
    {"dt": "Purchase Invoice", "fieldname": "custom_owner_answer",
     "label": "Owner answer (embco)", "fieldtype": "Select", "options": "\nApprove\nReject",
     "insert_after": "due_date",
     "allow_on_submit": 1, "permlevel": OWNER_LEVEL,
     "description": "Answers the embco agent's question about this invoice"},
    {"dt": "Purchase Invoice", "fieldname": "custom_owner_note", "label": "Owner note (embco)",
     "fieldtype": "Small Text", "insert_after": "custom_owner_answer", "allow_on_submit": 1,
     "permlevel": OWNER_LEVEL},
]

# (doctype, role, permission level, rights): the owner writes the answer, the agent reads it
LEVEL_PERMISSIONS = [
    ("Purchase Invoice", "System Manager", OWNER_LEVEL, ("read", "write")),
    ("Purchase Invoice", "Accounts User", OWNER_LEVEL, ("read",)),
]

REQUIRED_SETTINGS = [("Buying Settings", "po_required", "Yes"),
                     ("Buying Settings", "pr_required", "Yes")]


def apply() -> None:
    for field in CUSTOM_FIELDS:
        name = f"{field['dt']}-{field['fieldname']}"
        if frappe.db.exists("Custom Field", name):
            print("R|exists", name)
            continue
        frappe.get_doc({"doctype": "Custom Field", **field}).insert(ignore_permissions=True)
        print("R|created", name)
    for doctype, role, level, rights in LEVEL_PERMISSIONS:
        # copies the standard rules into custom ones first, so nothing else changes
        add_permission(doctype, role, level)
        for right in rights:
            update_permission_property(doctype, role, level, right, 1)
        print("R|permission", doctype, role, level, rights)
    for doctype, fieldname, value in REQUIRED_SETTINGS:
        frappe.db.set_single_value(doctype, fieldname, value)
    frappe.db.commit()
    frappe.clear_cache()
    print("R|prereqs applied")

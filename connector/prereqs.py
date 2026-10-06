"""Prepare an ERPNext instance for the embco agent. Safe to run more than once.

Run inside an ERPNext bench console, from /home/frappe:
    from connector.prereqs import apply; apply()
"""

import frappe

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
    for doctype, fieldname, value in REQUIRED_SETTINGS:
        frappe.db.set_single_value(doctype, fieldname, value)
    frappe.db.commit()
    frappe.clear_cache()
    print("R|prereqs applied")

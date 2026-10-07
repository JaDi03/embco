"""The only surface the decision core may use to reach an accounting system."""

from typing import Protocol

from embco.ledger.models import (
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    SettledPayment,
    Supplier,
    WalletChange,
)


class LedgerError(Exception):
    """Raised when the accounting system cannot be read or rejects a request."""


class LedgerAdapter(Protocol):
    def get_supplier(self, name: str) -> Supplier: ...

    def get_purchase_order(self, name: str) -> PurchaseOrder: ...

    def get_purchase_receipt(self, name: str) -> PurchaseReceipt: ...

    def get_purchase_invoice(self, name: str) -> PurchaseInvoice: ...

    def list_unpaid_purchase_invoices(self) -> list[PurchaseInvoice]: ...

    def list_supplier_invoices(self, supplier: str) -> list[PurchaseInvoice]:
        """Every submitted invoice of a supplier, oldest first."""
        ...

    def list_payments(self, supplier: str) -> list[PaymentRecord]:
        """Payments already made to a supplier, oldest first."""
        ...

    def wallet_changes(self, supplier: str) -> list[WalletChange]:
        """Edits of the supplier's wallet in the ERP, newest first (who and when)."""
        ...


class PaymentWriter(Protocol):
    """The one write the agent makes to the accounting system: a payment that is final on chain."""

    def record_payment(self, payment: SettledPayment) -> str:
        """Create and submit the payment entry and return its name. Writing the same
        transaction twice returns the existing entry instead of a second one."""
        ...

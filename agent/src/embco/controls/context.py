"""Everything a control needs about one invoice, loaded once so controls stay pure."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from embco.controls.confirmations import WalletProof, WalletProofSource
from embco.ledger import LedgerAdapter
from embco.ledger.models import (
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    Supplier,
)


@dataclass(frozen=True)
class Context:
    invoice: PurchaseInvoice
    supplier: Supplier
    orders: Mapping[str, PurchaseOrder]
    receipts: Mapping[str, PurchaseReceipt]
    supplier_invoices: tuple[PurchaseInvoice, ...]
    payments: tuple[PaymentRecord, ...]
    wallet_proof: WalletProof | None = None


@dataclass
class ContextBuilder:
    """Builds contexts and caches what is shared between invoices of one supplier."""

    ledger: LedgerAdapter
    proofs: WalletProofSource | None = None
    _suppliers: dict[str, Supplier] = field(default_factory=dict)
    _invoices: dict[str, tuple[PurchaseInvoice, ...]] = field(default_factory=dict)
    _payments: dict[str, tuple[PaymentRecord, ...]] = field(default_factory=dict)
    _proofs: dict[str, WalletProof | None] = field(default_factory=dict)

    def build(self, invoice: PurchaseInvoice) -> Context:
        name = invoice.supplier
        if name not in self._suppliers:
            self._suppliers[name] = self.ledger.get_supplier(name)
            self._invoices[name] = tuple(self.ledger.list_supplier_invoices(name))
            self._payments[name] = tuple(self.ledger.list_payments(name))
            self._proofs[name] = self.proofs.latest_wallet_proof(name) if self.proofs else None
        order_names = {line.purchase_order for line in invoice.lines if line.purchase_order}
        receipt_names = {line.purchase_receipt for line in invoice.lines if line.purchase_receipt}
        return Context(
            invoice=invoice,
            supplier=self._suppliers[name],
            orders={n: self.ledger.get_purchase_order(n) for n in sorted(order_names)},
            receipts={n: self.ledger.get_purchase_receipt(n) for n in sorted(receipt_names)},
            supplier_invoices=self._invoices[name],
            payments=self._payments[name],
            wallet_proof=self._proofs[name],
        )

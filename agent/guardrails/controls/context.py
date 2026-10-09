"""Everything a control needs about one invoice, loaded once so controls stay pure."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from agent.guardrails.controls.confirmations import WalletProof, WalletProofSource
from services.erp import LedgerAdapter
from services.erp.models import (
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    Supplier,
    WalletChange,
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
    wallet_changes: tuple[WalletChange, ...] = ()


@dataclass
class ContextBuilder:
    """Builds contexts and caches what is shared between invoices of one supplier."""

    ledger: LedgerAdapter
    proofs: WalletProofSource | None = None
    _suppliers: dict[str, Supplier] = field(default_factory=dict)
    _invoices: dict[str, tuple[PurchaseInvoice, ...]] = field(default_factory=dict)
    _payments: dict[str, tuple[PaymentRecord, ...]] = field(default_factory=dict)
    _proofs: dict[str, WalletProof | None] = field(default_factory=dict)
    _changes: dict[str, tuple[WalletChange, ...]] = field(default_factory=dict)

    def build(self, invoice: PurchaseInvoice) -> Context:
        name = invoice.supplier
        if name not in self._suppliers:
            self._suppliers[name] = self.ledger.get_supplier(name)
            self._invoices[name] = tuple(self.ledger.list_supplier_invoices(name))
            self._payments[name] = tuple(self.ledger.list_payments(name))
            self._proofs[name] = self.proofs.latest_wallet_proof(name) if self.proofs else None
            self._changes[name] = self._wallet_changes(name)
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
            wallet_changes=self._changes[name],
        )

    def _wallet_changes(self, name: str) -> tuple[WalletChange, ...]:
        """Who changed the wallet, read only when it is not the one paid last time."""
        wallet = (self._suppliers[name].wallet_address or "").lower()
        paid = [p.payee_wallet for p in self._payments[name] if p.payee_wallet]
        if not wallet or (paid and paid[-1].lower() == wallet):
            return ()
        return tuple(self.ledger.wallet_changes(name))

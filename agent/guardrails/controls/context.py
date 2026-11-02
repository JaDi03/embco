"""Everything a control needs about one invoice, loaded once so controls stay pure."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

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
from services.fx.rates import PAYABLE_CURRENCY, ExchangeRate, RateError, RateSource, to_usd


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
    today: date = date.min  # the day (UTC) the checks run; the earliest day keeps any hold
    rate: ExchangeRate | None = None  # for an invoice in another currency, when one was had
    rate_problem: str = ""  # why there is no rate for it

    @property
    def usdc_amount(self) -> Decimal | None:
        """What paying the amount due takes in USDC; None for another currency with no rate."""
        due = self.invoice.outstanding_amount
        if self.invoice.currency == PAYABLE_CURRENCY:
            return due
        return to_usd(due, self.rate) if self.rate else None


@dataclass
class ContextBuilder:
    """Builds contexts and caches what is shared between invoices of one supplier."""

    ledger: LedgerAdapter
    proofs: WalletProofSource | None = None
    today: date = field(default_factory=lambda: datetime.now(UTC).date())
    rates: RateSource | None = None
    _suppliers: dict[str, Supplier] = field(default_factory=dict)
    _invoices: dict[str, tuple[PurchaseInvoice, ...]] = field(default_factory=dict)
    _payments: dict[str, tuple[PaymentRecord, ...]] = field(default_factory=dict)
    _proofs: dict[str, WalletProof | None] = field(default_factory=dict)
    _changes: dict[str, tuple[WalletChange, ...]] = field(default_factory=dict)
    _rates: dict[str, tuple[ExchangeRate | None, str]] = field(default_factory=dict)

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
            today=self.today,
            **self._rate_for(invoice.currency),
        )

    def _rate_for(self, currency: str) -> dict:
        """The rate of an invoice's currency, asked once per currency; nothing for USD."""
        if currency == PAYABLE_CURRENCY:
            return {}
        if currency not in self._rates:
            if self.rates is None:
                self._rates[currency] = (None, "the agent has no exchange rate source")
            else:
                try:
                    self._rates[currency] = (self.rates.usd_rate(currency), "")
                except RateError as error:
                    self._rates[currency] = (None, str(error))
        rate, problem = self._rates[currency]
        return {"rate": rate, "rate_problem": problem}

    def _wallet_changes(self, name: str) -> tuple[WalletChange, ...]:
        """Who changed the wallet, read only when it is not the one paid last time."""
        wallet = (self._suppliers[name].wallet_address or "").lower()
        paid = [p.payee_wallet for p in self._payments[name] if p.payee_wallet]
        if not wallet or (paid and paid[-1].lower() == wallet):
            return ()
        return tuple(self.ledger.wallet_changes(name))

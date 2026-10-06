"""Run every control on an invoice and combine the findings with a fixed rule.

The rule is deterministic: any HOLD means HOLD, otherwise any ASK means ASK, otherwise PAY.
"""

from embco.controls import (
    ContextBuilder,
    Control,
    DuplicateInvoice,
    Finding,
    Outcome,
    PayeeWallet,
    PaymentLimit,
    PriceAnomaly,
    SupplierStatus,
    ThreeWayMatch,
)
from embco.decision.config import PolicyConfig
from embco.decision.models import Action, Decision
from embco.ledger import LedgerAdapter
from embco.ledger.models import PurchaseInvoice


def standard_controls(config: PolicyConfig) -> list[Control]:
    return [
        SupplierStatus(),
        ThreeWayMatch(config.rate_tolerance),
        DuplicateInvoice(),
        PayeeWallet(),
        PriceAnomaly(config.max_price_increase),
        PaymentLimit(config.max_per_payment),
    ]


def combine(findings: list[Finding]) -> tuple[Action, tuple[str, ...]]:
    problems = [f for f in findings if f.outcome is not Outcome.PASS]
    if any(f.outcome is Outcome.HOLD for f in problems):
        return Action.HOLD, tuple(f.reason for f in problems)
    if problems:
        return Action.ASK, tuple(f.reason for f in problems)
    return Action.PAY, (f"all {len(findings)} controls passed",)


class DecisionEngine:
    def __init__(self, ledger: LedgerAdapter, config: PolicyConfig) -> None:
        self._ledger = ledger
        self._controls = standard_controls(config)
        self._contexts = ContextBuilder(ledger)

    def decide(self, invoice: PurchaseInvoice) -> Decision:
        context = self._contexts.build(invoice)
        findings = [control.check(context) for control in self._controls]
        action, reasons = combine(findings)
        return Decision(
            invoice=invoice.name,
            supplier=invoice.supplier,
            amount=invoice.outstanding_amount,
            due_date=invoice.due_date,
            action=action,
            reasons=reasons,
            findings=tuple(findings),
        )

    def decide_all(self) -> list[Decision]:
        return [self.decide(i) for i in self._ledger.list_unpaid_purchase_invoices()]

"""Run every control on an invoice and combine the findings with a fixed rule.

The rule is deterministic: any HOLD means HOLD, otherwise any ASK means ASK, otherwise PAY. A
NOTE changes nothing here: it is evidence the agent weighs when it decides what to do.
"""

from datetime import date
from decimal import Decimal

from agent.guardrails.controls import (
    ContextBuilder,
    Control,
    DuplicateInvoice,
    ExchangeRateCheck,
    Finding,
    Outcome,
    PayeeWallet,
    PaymentLimit,
    PriceAnomaly,
    SupplierStatus,
    ThreeWayMatch,
    WalletProofSource,
)
from agent.guardrails.rules.config import PolicyConfig
from agent.guardrails.rules.models import Action, Decision
from services.erp import LedgerAdapter
from services.erp.models import PurchaseInvoice
from services.fx.rates import PAYABLE_CURRENCY, RateSource


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
    if any(f.outcome is Outcome.ASK for f in problems):
        return Action.ASK, tuple(f.reason for f in problems)
    if problems:
        return Action.PAY, tuple(f"to weigh: {f.reason}" for f in problems)
    return Action.PAY, (f"all {len(findings)} controls passed",)


class DecisionEngine:
    def __init__(
        self,
        ledger: LedgerAdapter,
        config: PolicyConfig,
        *,
        proofs: WalletProofSource | None = None,
        today: date | None = None,
        rates: RateSource | None = None,
    ) -> None:
        self._ledger = ledger
        self._controls = standard_controls(config)
        # only for invoices in another currency: a USD invoice's checks stay as they were
        self._exchange = ExchangeRateCheck(config.max_rate_age_days)
        self._contexts = ContextBuilder(ledger, proofs, **({"today": today} if today else {}),
                                        rates=rates)

    def decide(self, invoice: PurchaseInvoice) -> Decision:
        context = self._contexts.build(invoice)
        local = invoice.currency != PAYABLE_CURRENCY
        controls = [*self._controls, self._exchange] if local else self._controls
        findings = [control.check(context) for control in controls]
        action, reasons = combine(findings)
        return Decision(
            invoice=invoice.name,
            supplier=invoice.supplier,
            amount=context.usdc_amount if context.usdc_amount is not None else Decimal(0),
            due_date=invoice.due_date,
            action=action,
            reasons=reasons,
            findings=tuple(findings),
            **({"invoice_amount": invoice.outstanding_amount, "currency": invoice.currency,
                "rate": context.rate} if local else {}),
        )

    def decide_all(self) -> list[Decision]:
        return [self.decide(i) for i in self._ledger.list_unpaid_purchase_invoices()]

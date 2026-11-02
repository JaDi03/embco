"""Invoices in a local currency: embco asks for the day's rate, converts to USDC, pays that and
writes it in the ERP as a payment in two currencies. Numbers from the NGN test site: an invoice
of 171,500 NGN at 1345.58 NGN per USD is 127.45 USDC."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from agent.guardrails.controls.base import Outcome
from agent.guardrails.rules import Action, DecisionEngine, PolicyConfig, fingerprint
from agent.memory import SqliteJournal, remember
from agent.tools.toolbox import Toolbox
from services.erp import ErpnextAdapter, LedgerError
from services.erp.models import SettledPayment
from services.fx import ExchangeRate, Frankfurter, RateError, to_usd
from services.payments import PaymentStatus
from services.payments.encoding import usdc_units
from support import FakeLedger, make_invoice
from test_payments import FakeWriter, journal, make_payer, plan  # noqa: F401 (fixture)

TODAY = date(2026, 11, 2)
NGN = ExchangeRate(currency="NGN", per_usd=Decimal("1345.58"), on=TODAY,
                   source="frankfurter.dev")
POLICY = PolicyConfig(max_per_payment=Decimal(500), weekly_budget=Decimal(2000))


class FakeRates:
    def __init__(self, rate: ExchangeRate | None = NGN, error: str = "") -> None:
        self.rate, self.error, self.asked = rate, error, []

    def usd_rate(self, currency):
        self.asked.append(currency)
        if self.rate is None:
            raise RateError(self.error or "the exchange rate source did not answer")
        return self.rate


def naira_ledger(outstanding="171500", **kw) -> FakeLedger:
    ledger = FakeLedger()
    ledger.pending = [make_invoice(qty="10", rate="17150", currency="NGN",
                                   outstanding=outstanding, **kw)]
    for docs in (ledger.orders, ledger.receipts):
        for name, doc in docs.items():
            docs[name] = doc.model_copy(update={
                "currency": "NGN", "grand_total": Decimal(171500),
                "lines": tuple(line.model_copy(update={"rate": Decimal(17150),
                                                        "amount": Decimal(171500)})
                               for line in doc.lines)})
    return ledger  # its earlier payment to the same wallet stays: the wallet is known


def decide(ledger, rates=None, policy=POLICY, today=TODAY):
    return DecisionEngine(ledger, policy, today=today, rates=rates).decide_all()[0]


# the rate


def frankfurter(handler, day=TODAY) -> Frankfurter:
    return Frankfurter(client=httpx.Client(transport=httpx.MockTransport(handler)),
                       today=lambda: day)


def test_frankfurter_gives_the_days_rate_exactly_and_asks_once_a_day():
    asked = []

    def handler(request):
        asked.append(request.url.path)
        return httpx.Response(200, text='{"date":"2026-11-02","base":"USD","quote":"NGN",'
                                        '"rate":1345.58}')

    source = frankfurter(handler)
    assert source.usd_rate("ngn") == NGN
    assert source.usd_rate("NGN") == NGN
    assert asked == ["/v2/rate/USD/NGN"]


@pytest.mark.parametrize("status, text, says", [
    (422, '{"status":422,"message":"invalid currency: XXX"}', "no rate for XXX"),
    (200, '{"date":"2026-11-02","base":"USD","quote":"XXX"}', "unreadable"),
    (200, '{"date":"2026-11-02","base":"USD","quote":"EUR","rate":0.9}', "unusable"),
    (200, '{"date":"2026-11-02","base":"USD","quote":"XXX","rate":0}', "unusable"),
])
def test_frankfurter_refuses_what_it_cannot_use(status, text, says):
    source = frankfurter(lambda request: httpx.Response(status, text=text))
    with pytest.raises(RateError, match=says):
        source.usd_rate("XXX")


def test_frankfurter_down_is_a_rate_error():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(RateError, match="did not answer"):
        frankfurter(handler).usd_rate("NGN")


def test_usd_is_rounded_to_the_cent_as_the_erp_records_it():
    assert to_usd(Decimal(171500), NGN) == Decimal("127.45")
    assert to_usd(Decimal("6.7279"), NGN) == Decimal("0.01")  # half a cent rounds up


# the decision


def test_a_naira_invoice_is_decided_in_usdc_at_the_days_rate():
    rates = FakeRates()
    d = decide(naira_ledger(), rates)
    assert (d.amount, d.invoice_amount, d.currency, d.rate) == (
        Decimal("127.45"), Decimal(171500), "NGN", NGN)
    assert d.billed == Decimal(171500)
    [rate] = [f for f in d.findings if f.control == "exchange_rate"]
    assert rate.outcome is Outcome.PASS
    assert rate.reason == ("171500 NGN is 127.45 USDC at 1345.58 NGN per USD "
                           "(frankfurter.dev, 2026-11-02)")
    assert rates.asked == ["NGN"]


def test_a_usd_invoice_keeps_its_checks_and_asks_for_no_rate():
    ledger = FakeLedger()
    rates = FakeRates()
    d = decide(ledger, rates)
    assert d.invoice_amount is None and d.currency == "USD" and d.rate is None
    assert "exchange_rate" not in [f.control for f in d.findings]
    assert rates.asked == []


@pytest.mark.parametrize("rates, says", [
    (None, "no exchange rate for NGN: the agent has no exchange rate source"),
    (FakeRates(None), "no exchange rate for NGN: the exchange rate source did not answer"),
    (FakeRates(replace(NGN, on=date(2026, 10, 29))),
     "the NGN rate is from 2026-10-29 (4 days old; at most 3 allowed)"),
])
def test_no_rate_or_an_old_one_holds_the_invoice(rates, says):
    d = decide(naira_ledger(), rates)
    assert d.action is Action.HOLD and says in d.reasons


def test_a_rate_three_days_old_still_pays():
    d = decide(naira_ledger(), FakeRates(replace(NGN, on=date(2026, 10, 30))))
    assert d.action is Action.PAY


def test_with_no_rate_there_is_no_usdc_amount():
    d = decide(naira_ledger(), FakeRates(None))
    assert d.amount == 0 and d.invoice_amount == Decimal(171500)


def test_an_invoice_not_in_the_companys_currency_holds():
    d = decide(naira_ledger(conversion_rate=Decimal("0.00074")), FakeRates())
    assert d.action is Action.HOLD
    assert "the invoice is in NGN, not the company's own currency; pay it by hand" in d.reasons


def test_the_per_payment_limit_weighs_the_usdc():
    small = replace(POLICY, max_per_payment=Decimal(100))
    d = decide(naira_ledger(), FakeRates(), policy=small)
    assert ("to weigh: amount 127.45 USDC (171500 NGN) is above the per-payment limit of 100"
            in d.reasons)


def test_the_days_rate_does_not_change_what_the_decision_is():
    first = decide(naira_ledger(), FakeRates())
    moved = decide(naira_ledger(), FakeRates(replace(NGN, per_usd=Decimal("1350.10"))))
    assert first.amount != moved.amount and first.action is moved.action is Action.PAY
    assert fingerprint(first) == fingerprint(moved)  # an owner's answer still stands


def test_the_journal_keeps_the_invoices_own_amount_and_the_rate():
    d = decide(naira_ledger(), FakeRates())
    with SqliteJournal(":memory:") as j:
        remember(j, [d], POLICY, at=datetime(2026, 11, 2, 8, tzinfo=UTC))
        [entry] = j.history(d.invoice)
        assert entry.amount == Decimal("127.45") and entry.billed == Decimal(171500)
        j.verify()


# the payment


def naira_decision():
    return decide(naira_ledger(), FakeRates())


def test_a_naira_invoice_is_paid_in_usdc_and_written_with_both_amounts(journal):  # noqa: F811
    payer, _, circle = make_payer(naira_ledger())
    payer.writer = writer = NairaWriter()
    result = payer.settle(plan(naira_decision()), journal)
    assert [e.status for e in result.events] == [
        PaymentStatus.SUBMITTED, PaymentStatus.COMPLETE, PaymentStatus.RECORDED]
    [sent] = circle.sent
    assert usdc_units(Decimal("127.45")).to_bytes(32, "big") in sent["data"]
    [settled] = writer.recorded
    assert (settled.amount, settled.invoice_amount, settled.rate) == (
        Decimal("127.45"), Decimal(171500), Decimal("1345.58"))
    last = journal.latest_payment("PINV-1")
    assert (last.amount, last.invoice_amount, last.currency, last.rate) == (
        Decimal("127.45"), Decimal(171500), "NGN", Decimal("1345.58"))
    journal.verify()


class NairaWriter(FakeWriter):
    def __init__(self, problem: str | None = None) -> None:
        super().__init__()
        self.problem = problem

    def payment_account_problem(self, currency):
        return self.problem


def test_nothing_is_paid_that_the_books_cannot_take(journal):  # noqa: F811
    payer, _, circle = make_payer(naira_ledger())
    payer.writer = NairaWriter("the ERP has no active Bank accounts in USD; it needs exactly "
                               "one (USDC on Arc)")
    result = payer.settle(plan(naira_decision()), journal)
    assert circle.sent == [] and "no active Bank accounts in USD" in result.events[0].reason


def test_a_naira_decision_with_no_rate_is_never_paid(journal):  # noqa: F811
    payer, _, circle = make_payer(naira_ledger())
    d = replace(decide(naira_ledger(), FakeRates(None)), action=Action.PAY)
    result = payer.settle(plan(d), journal)
    assert circle.sent == [] and result.events[0].reason == "no exchange rate for NGN"


def test_a_changed_naira_amount_is_decided_again(journal):  # noqa: F811
    payer, _, circle = make_payer(naira_ledger(outstanding="100000"))
    result = payer.settle(plan(naira_decision()), journal)
    assert circle.sent == [] and "amount due changed" in result.events[0].reason


# the ERP


TX = "0x" + "e7" * 32
WALLET = "0x" + "21" * 20
PAID = SettledPayment(invoice="ACC-PINV-2026-00001", supplier="Ikeja Fresh Produce Ltd",
                      amount=Decimal("127.45"), paid_on=TODAY, tx_hash=TX, payee_wallet=WALLET,
                      note="PAY decided by the embco agent", invoice_amount=Decimal(171500),
                      rate=Decimal("1345.58"))
# what ERPNext v16 drafted on the NGN test site with bank_amount 127.45
DRAFT = """{"message": {"doctype": "Payment Entry", "payment_type": "Pay",
  "party": "Ikeja Fresh Produce Ltd", "paid_from": "USDC on Arc - NGT",
  "paid_from_account_currency": "USD", "paid_to": "Creditors - NGT",
  "paid_to_account_currency": "NGN", "paid_amount": 127.45, "received_amount": 171500.0,
  "source_exchange_rate": 1345.58, "target_exchange_rate": 1.0, "docstatus": 0,
  "references": [{"reference_doctype": "Purchase Invoice",
                  "reference_name": "ACC-PINV-2026-00001", "allocated_amount": 171500.0}]}}"""


class NairaErp:
    def __init__(self, accounts=("USDC on Arc - NGT",), draft=DRAFT):
        self.accounts, self.draft = list(accounts), draft
        self.drafted: list[bytes] = []
        self.account_filters: list = []
        self.inserted: list[dict] = []

    def __call__(self, request):
        path = request.url.path
        if request.method == "GET" and path == "/api/resource/Payment Entry":
            return httpx.Response(200, json={"data": []})
        if request.method == "GET" and path == "/api/resource/Account":
            self.account_filters.append(json.loads(request.url.params["filters"]))
            return httpx.Response(200, json={"data": [{"name": a} for a in self.accounts]})
        if path.endswith("get_payment_entry"):
            self.drafted.append(request.content)
            return httpx.Response(200, text=self.draft)
        if request.method == "POST" and path == "/api/resource/Payment Entry":
            self.inserted.append(json.loads(request.content, parse_float=Decimal))
            return httpx.Response(200, json={"data": {"name": "ACC-PAY-2026-00001"}})
        raise AssertionError(f"unexpected {request.method} {path}")


def erp_adapter(erp, **kwargs) -> ErpnextAdapter:
    return ErpnextAdapter("https://erp.test", "k", "s", company="NGN Test",
                          client=httpx.Client(transport=httpx.MockTransport(erp)), **kwargs)


def test_the_payment_is_drafted_from_the_usd_account_with_the_usdc_paid():
    erp = NairaErp()
    assert erp_adapter(erp).record_payment(PAID) == "ACC-PAY-2026-00001"
    [form] = erp.drafted
    assert b"bank_account=USDC+on+Arc+-+NGT" in form and b"bank_amount=127.45" in form
    [doc] = erp.inserted
    assert doc["paid_amount"] == Decimal("127.45") and doc["received_amount"] == 171500
    assert doc["source_exchange_rate"] == Decimal("1345.58")
    [filters] = erp.account_filters
    assert ["account_currency", "=", "USD"] in filters and ["account_type", "=", "Bank"] in filters
    assert ["company", "=", "NGN Test"] in filters


def test_a_draft_that_settles_another_naira_amount_is_not_written():
    erp = NairaErp(draft=DRAFT.replace('"received_amount": 171500.0', '"received_amount": 1715.0'))
    with pytest.raises(LedgerError, match="does not match"):
        erp_adapter(erp).record_payment(PAID)
    assert erp.inserted == []


@pytest.mark.parametrize("accounts, says", [
    ((), "has no active Bank accounts in USD"),
    (("USDC on Arc - NGT", "Dollars - NGT"), "has 2 active Bank accounts in USD"),
])
def test_the_usd_account_must_be_one_and_only_one(accounts, says):
    erp = NairaErp(accounts)
    problem = erp_adapter(erp).payment_account_problem("NGN")
    assert says in problem
    assert erp_adapter(erp).payment_account_problem("USD") is None


def test_the_account_set_for_the_shop_is_used_when_it_is_in_usd():
    erp = NairaErp()
    assert erp_adapter(erp, paid_from="USDC on Arc - NGT").payment_account_problem("NGN") is None
    [filters] = erp.account_filters
    assert ["name", "=", "USDC on Arc - NGT"] in filters
    assert erp_adapter(NairaErp(()), paid_from="Cash - NGT").payment_account_problem("NGN") == (
        "the account set to pay from (Cash - NGT) is not an active USD account in the ERP")


# what the agent sees


def test_the_agent_sees_the_naira_amount_and_the_rate_beside_the_usdc():
    d = naira_decision()
    with SqliteJournal(":memory:") as j:
        box = Toolbox(decisions=[d], ledger=None, journal=j, policy=POLICY,
                      now=datetime(2026, 11, 2, 8, tzinfo=UTC), zone=UTC)
        found = box.call("search_invoices", dict.fromkeys(
            ("supplier", "due_from", "due_to", "checks_say"), ""))
        [row] = json.loads(found)["invoices"]
    assert (row["amount"], row["invoice_amount"], row["currency"]) == ("127.45", "171500", "NGN")
    assert row["exchange_rate"] == {"per_usd": "1345.58", "on": "2026-11-02",
                                    "source": "frankfurter.dev"}

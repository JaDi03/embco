import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from embco.circle import CircleTransaction, CircleWallet
from embco.controls import WalletProof
from embco.controls.base import Finding, Outcome
from embco.decision import Action, Decision, PaymentPlan, PolicyConfig
from embco.journal import SqliteJournal
from embco.ledger import LedgerError
from embco.ledger.models import Supplier
from embco.payments import (
    Payer,
    PaymentSetupError,
    PaymentStatus,
    Reverted,
)
from embco.payments.chain import revert_reason
from embco.payments.encoding import (
    MEMO_CONTRACT,
    EncodingError,
    invoice_ref,
    memo_call,
    pay_call,
    usdc_units,
)
from embco.payments.payer import CHAIN_APPROVER, MAX_ATTEMPTS, idempotency_key
from embco.runner import format_report, run_cycle
from support import WALLET_A, WALLET_B, FakeLedger, make_invoice

WALLET_C = "0x" + "c3" * 20

SHOP = "0x" + "5c" * 20
AGENT = to_checksum_address("0x" + "a9" * 20)
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


class FakeChain:
    def __init__(self) -> None:
        self.agent = AGENT
        self.paid: set[bytes] = set()
        self.remaining = usdc_units(Decimal("2000"))
        self.revert: str | None = None
        self.unapproved: set[str] = set()
        self.simulated: list[tuple[str, bytes, str | None]] = []

    def agent_of(self, shop):
        return self.agent

    def is_paid(self, shop, ref):
        return ref in self.paid

    def is_approved(self, shop, payee):
        return payee.lower() not in self.unapproved

    def remaining_this_week(self, shop):
        return self.remaining

    def call(self, to, data, *, sender=None):
        self.simulated.append((to, data, sender))
        if self.revert:
            raise Reverted(self.revert)
        return b""


class FakeCircle:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.final_state = "COMPLETE"

    def get_wallet(self, wallet_id):
        return CircleWallet(id=wallet_id, address=AGENT.lower(), blockchain="ARC-TESTNET",
                            wallet_set_id="ws")

    def execute_contract(self, wallet_id, contract, call_data, *, idempotency_key, ref_id=None):
        self.sent.append({"wallet_id": wallet_id, "contract": contract, "data": call_data,
                          "key": idempotency_key, "ref_id": ref_id})
        return CircleTransaction(id=f"tx-{len(self.sent)}", state="INITIATED")

    def get_transaction(self, transaction_id):
        return CircleTransaction(id=transaction_id, state=self.final_state,
                                 tx_hash="0x" + "11" * 32,
                                 error_reason=None if self.final_state == "COMPLETE" else "boom")


def usd_ledger(amount="125.5") -> FakeLedger:
    ledger = FakeLedger()
    ledger.pending = [make_invoice(qty="1", rate=amount, currency="USD")]
    return ledger


def decision(amount="125.5", invoice="PINV-1") -> Decision:
    return Decision(invoice=invoice, supplier="S", amount=Decimal(amount), due_date=None,
                    action=Action.PAY, reasons=("all 6 controls passed",), findings=())


def plan(*decisions: Decision) -> PaymentPlan:
    return PaymentPlan(pay_now=decisions, deferred=(), held=(), asked=(), budget_left=Decimal(0))


@pytest.fixture
def journal():
    with SqliteJournal(":memory:") as j:
        yield j


def make_payer(ledger=None, chain=None, circle=None) -> tuple[Payer, FakeChain, FakeCircle]:
    chain, circle = chain or FakeChain(), circle or FakeCircle()
    payer = Payer(ledger=ledger or usd_ledger(), chain=chain, circle=circle, shop=SHOP,
                  wallet_id="w-1", wait=timedelta(seconds=0), sleep=lambda _: None,
                  clock=lambda: NOW)
    return payer, chain, circle


# encoding


def test_usdc_units_are_exact_and_never_rounded():
    assert usdc_units(Decimal("125.5")) == 125_500_000
    assert usdc_units(Decimal("0.000001")) == 1
    for bad in ("0.0000001", "0", "-1", "NaN"):
        with pytest.raises(EncodingError):
            usdc_units(Decimal(bad))


def test_memo_call_wraps_the_pay_call_with_the_invoice_number():
    ref = invoice_ref("ACC-PINV-2026-00001")
    assert ref == keccak(text="ACC-PINV-2026-00001")
    inner = pay_call(WALLET_A, 125_500_000, ref)
    data = memo_call(SHOP, inner, ref, "ACC-PINV-2026-00001")
    assert data[:4] == keccak(text="memo(address,bytes,bytes32,bytes)")[:4]
    target, forwarded, memo_id, memo = decode(["address", "bytes", "bytes32", "bytes"], data[4:])
    assert (target.lower(), forwarded, memo_id, memo) == (SHOP, inner, ref,
                                                          b"ACC-PINV-2026-00001")
    assert inner[:4] == keccak(text="pay(address,uint256,bytes32)")[:4]
    assert decode(["address", "uint256", "bytes32"], inner[4:])[1] == 125_500_000


def test_revert_reason_unwraps_memo_and_names_the_shop_error():
    inner = keccak(text="PayeeNotApproved(address)")[:4] + encode(["address"], [WALLET_A])
    wrapped = keccak(text="MemoFailed(bytes)")[:4] + encode(["bytes"], [inner])
    assert revert_reason(wrapped) == "PayeeNotApproved"
    message = keccak(text="Error(string)")[:4] + encode(["string"], ["allowance exceeded"])
    assert revert_reason(message) == "allowance exceeded"
    assert revert_reason(b"") == "reverted without a reason"


def test_idempotency_key_is_a_stable_uuid4_that_changes_with_the_attempt():
    ref = invoice_ref("PINV-1")
    key = idempotency_key(SHOP, ref, WALLET_A, 1, 0)
    assert uuid.UUID(key).version == 4
    assert key == idempotency_key(SHOP.upper().replace("0X", "0x"), ref, WALLET_A.upper(), 1, 0)
    assert key != idempotency_key(SHOP, ref, WALLET_A, 1, 1)


# paying


def test_a_planned_invoice_is_simulated_sent_and_completed(journal):
    payer, chain, circle = make_payer()
    result = payer.settle(plan(decision()), journal)
    assert [e.status for e in result.events] == [PaymentStatus.SUBMITTED, PaymentStatus.COMPLETE]
    [sent] = circle.sent
    [(to, simulated, sender)] = chain.simulated
    assert (to, sender) == (MEMO_CONTRACT, AGENT)
    assert sent["data"] == simulated and sent["contract"] == MEMO_CONTRACT
    assert sent["ref_id"] == "PINV-1"
    last = journal.latest_payment("PINV-1")
    assert last.status is PaymentStatus.COMPLETE and last.tx_hash == "0x" + "11" * 32
    assert last.amount == Decimal("125.5") and last.payee == to_checksum_address(WALLET_A)
    journal.verify()


def test_a_paid_or_sent_invoice_is_never_sent_again(journal):
    payer, _, circle = make_payer()
    payer.settle(plan(decision()), journal)
    payer.settle(plan(decision()), journal)
    assert len(circle.sent) == 1


def test_a_call_the_contract_would_refuse_is_not_sent_and_recorded_once(journal):
    payer, chain, circle = make_payer()
    chain.revert = "PayeeNotApproved"
    first = payer.settle(plan(decision()), journal)
    second = payer.settle(plan(decision()), journal)
    assert circle.sent == []
    assert [e.status for e in first.events] == [PaymentStatus.BLOCKED]
    assert "PayeeNotApproved" in first.events[0].reason
    assert second.events == ()


def test_no_room_under_the_weekly_cap_waits_without_sending(journal):
    payer, chain, circle = make_payer()
    chain.remaining = usdc_units(Decimal("100"))
    result = payer.settle(plan(decision()), journal)
    assert result.waiting == ("PINV-1",) and result.events == ()
    assert circle.sent == [] and journal.latest_payment("PINV-1") is None


def test_the_weekly_room_shrinks_with_each_payment_in_the_same_cycle(journal):
    ledger = usd_ledger("100")
    ledger.pending.append(make_invoice("PINV-2", "B-2", qty="1", rate="100", currency="USD"))
    payer, chain, circle = make_payer(ledger)
    chain.remaining = usdc_units(Decimal("150"))
    result = payer.settle(plan(decision("100"), decision("100", "PINV-2")), journal)
    assert len(circle.sent) == 1 and result.waiting == ("PINV-2",)


@pytest.mark.parametrize("change, reason", [
    ({"currency": "NGN"}, "contract pays USD"),
    ({"outstanding": "100"}, "amount due changed"),
])
def test_the_invoice_is_read_again_before_paying(journal, change, reason):
    ledger = FakeLedger()
    ledger.pending = [make_invoice(qty="1", rate="125.5", **{"currency": "USD", **change})]
    payer, _, circle = make_payer(ledger)
    result = payer.settle(plan(decision()), journal)
    assert circle.sent == [] and reason in result.events[0].reason


def test_an_invoice_the_contract_already_paid_is_recorded_without_sending(journal):
    payer, chain, circle = make_payer()
    chain.paid.add(invoice_ref("PINV-1"))
    result = payer.settle(plan(decision()), journal)
    assert circle.sent == [] and result.events[0].status is PaymentStatus.COMPLETE


def test_a_failed_transaction_is_retried_with_a_new_key_up_to_the_limit(journal):
    payer, _, circle = make_payer()
    circle.final_state = "FAILED"
    for _ in range(MAX_ATTEMPTS + 1):
        payer.settle(plan(decision()), journal)
    assert len(circle.sent) == MAX_ATTEMPTS
    assert len({s["key"] for s in circle.sent}) == MAX_ATTEMPTS
    last = journal.latest_payment("PINV-1")
    assert last.status is PaymentStatus.BLOCKED and "check it by hand" in last.reason


def test_a_sent_payment_is_followed_up_in_the_next_cycle(journal):
    payer, _, circle = make_payer()
    circle.final_state = "SENT"  # not final yet
    payer.settle(plan(decision()), journal)
    assert journal.latest_payment("PINV-1").status is PaymentStatus.SUBMITTED
    circle.final_state = "COMPLETE"
    result = payer.settle(plan(), journal)
    assert [e.status for e in result.events] == [PaymentStatus.COMPLETE]
    assert len(circle.sent) == 1


def test_nothing_is_paid_when_the_contract_has_another_agent(journal):
    payer, chain, circle = make_payer()
    chain.agent = to_checksum_address("0x" + "ee" * 20)
    with pytest.raises(PaymentSetupError, match="dashboard"):
        payer.settle(plan(decision()), journal)
    assert circle.sent == []


# inside the cycle


POLICY = PolicyConfig(max_per_payment=Decimal("1500"), weekly_budget=Decimal("2000"))


def matching_ledger() -> FakeLedger:
    """The default invoice matches its order and receipt, now in USD: the agent decides PAY."""
    ledger = FakeLedger()
    ledger.pending = [make_invoice(currency="USD")]
    return ledger


def test_the_cycle_pays_then_stops_planning_what_it_paid(journal):
    ledger = matching_ledger()
    payer, _, circle = make_payer(ledger)
    first = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    assert [d.action for d in first.decisions] == [Action.PAY]
    assert [e.status for e in first.settlement.events][-1] is PaymentStatus.COMPLETE
    second = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW + timedelta(minutes=15),
                       payments=payer)
    assert second.plan.pay_now == () and second.already_paid == ("PINV-1",)
    assert len(circle.sent) == 1
    assert "not yet closed in the ERP: PINV-1" in format_report(second)


def test_a_payment_problem_is_reported_and_the_agent_still_decides(journal):
    ledger = matching_ledger()
    payer, chain, _ = make_payer(ledger)
    chain.agent = to_checksum_address("0x" + "ee" * 20)
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    assert report.decisions and "dashboard" in report.settlement.problem
    assert "payments skipped" in format_report(report)


# recording in the ERP


class FakeWriter:
    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.recorded = []

    def record_payment(self, payment):
        if self.fail:
            self.fail -= 1
            raise LedgerError("ERPNext returned HTTP 502")
        self.recorded.append(payment)
        return f"ACC-PAY-{len(self.recorded):04d}"


def test_a_completed_payment_is_recorded_in_the_erp(journal):
    payer, _, circle = make_payer()
    payer.writer = writer = FakeWriter()
    result = payer.settle(plan(decision()), journal)
    assert [e.status for e in result.events] == [
        PaymentStatus.SUBMITTED, PaymentStatus.COMPLETE, PaymentStatus.RECORDED]
    [settled] = writer.recorded
    assert settled.tx_hash == "0x" + "11" * 32 and settled.amount == Decimal("125.5")
    assert settled.payee_wallet == to_checksum_address(WALLET_A)
    assert "tx-1" in settled.note
    last = journal.latest_payment("PINV-1")
    assert last.status is PaymentStatus.RECORDED and last.erp_entry == "ACC-PAY-0001"
    payer.settle(plan(decision()), journal)
    assert len(circle.sent) == 1 and len(writer.recorded) == 1
    journal.verify()


def test_an_erp_outage_leaves_the_payment_to_record_next_cycle(journal):
    payer, _, circle = make_payer()
    payer.writer = writer = FakeWriter(fail=1)
    payer.settle(plan(decision()), journal)
    assert journal.latest_payment("PINV-1").status is PaymentStatus.COMPLETE
    result = payer.settle(plan(), journal)
    assert [e.status for e in result.events] == [PaymentStatus.RECORDED]
    assert len(circle.sent) == 1 and len(writer.recorded) == 1


def test_a_payment_without_a_known_transaction_is_not_recorded(journal):
    payer, chain, _ = make_payer()
    payer.writer = writer = FakeWriter()
    chain.paid.add(invoice_ref("PINV-1"))  # paid by someone else, transaction unknown
    payer.settle(plan(decision()), journal)
    assert writer.recorded == []


def test_the_report_does_not_call_open_what_was_recorded_in_the_same_cycle(journal):
    ledger = matching_ledger()
    payer, _, _ = make_payer(ledger)
    payer.writer = FakeWriter(fail=1)
    run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    text = format_report(report)
    assert "RECORDED" in text and "not yet closed" not in text



# wallets waiting for the owner's approval


def test_an_unapproved_wallet_is_listed_for_the_dashboard_and_not_tried(journal, tmp_path):
    payer, chain, circle = make_payer()
    chain.unapproved.add(WALLET_A.lower())
    payer.approvals_file = tmp_path / "public" / "pending.json"
    result = payer.settle(plan(decision()), journal)
    assert circle.sent == [] and chain.simulated == []
    [pending] = result.needs_approval
    assert (pending.wallet, pending.invoice, pending.amount) == (
        to_checksum_address(WALLET_A), "PINV-1", Decimal("125.5"))
    published = json.loads(payer.approvals_file.read_text())
    assert published["shop"] == SHOP
    assert published["pending"] == [{"wallet": to_checksum_address(WALLET_A),
                                     "invoice": "PINV-1", "amount": "125.5"}]
    assert "S" not in json.dumps(published["pending"])  # no supplier names
    assert "approve wallet" in journal.latest_payment("PINV-1").reason


def test_once_approved_the_wallet_leaves_the_list_and_is_paid(journal, tmp_path):
    payer, chain, circle = make_payer()
    chain.unapproved.add(WALLET_A.lower())
    payer.approvals_file = tmp_path / "pending.json"
    payer.settle(plan(decision()), journal)
    chain.unapproved.clear()  # the owner signed setPayee in the dashboard
    result = payer.settle(plan(decision()), journal)
    assert result.needs_approval == () and len(circle.sent) == 1
    assert json.loads(payer.approvals_file.read_text())["pending"] == []


def test_the_report_tells_the_owner_which_wallet_to_approve(journal):
    ledger = matching_ledger()
    payer, chain, _ = make_payer(ledger)
    chain.unapproved.add(WALLET_A.lower())
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    wallet = to_checksum_address(WALLET_A)
    assert f"approve wallet {wallet} in the dashboard" in format_report(report)


# one approval for a new wallet: the owner's signature in the contract answers the question


def new_wallet_ledger(journal) -> FakeLedger:
    """The supplier moved to WALLET_B and proved it controls it: the agent asks the owner."""
    ledger = matching_ledger()
    ledger.supplier = Supplier(name="S", wallet_address=WALLET_B)
    journal.record_wallet_proof(WalletProof(supplier="S", wallet=WALLET_B, nonce="n",
                                            signature="0x", signed_at=NOW))
    return ledger


def test_a_new_wallet_is_listed_and_its_approval_answers_the_question(journal, tmp_path):
    ledger = new_wallet_ledger(journal)
    payer, chain, circle = make_payer(ledger)
    chain.unapproved.add(WALLET_B.lower())
    payer.approvals_file = tmp_path / "pending.json"
    first = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    assert [d.action for d in first.decisions] == [Action.ASK]
    assert [p.wallet for p in first.settlement.needs_approval] == [to_checksum_address(WALLET_B)]
    assert circle.sent == []
    chain.unapproved.clear()  # the owner signed setPayee in the dashboard, nothing else
    second = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW + timedelta(minutes=15),
                       payments=payer)
    [paid] = second.decisions
    assert paid.action is Action.PAY and "approved by owner (approved the wallet" in paid.reasons[0]
    assert len(circle.sent) == 1
    assert journal.latest_answer("PINV-1").answered_by == CHAIN_APPROVER


def test_a_wallet_approved_before_the_question_does_not_answer_it(journal):
    ledger = new_wallet_ledger(journal)
    payer, _, circle = make_payer(ledger)  # WALLET_B already approved in the contract
    for minutes in (0, 15):
        report = run_cycle(ledger, journal, POLICY, "TEST Shop",
                           at=NOW + timedelta(minutes=minutes), payments=payer)
        assert [d.action for d in report.decisions] == [Action.ASK]
        assert report.settlement.needs_approval == ()
    assert circle.sent == [] and journal.latest_answer("PINV-1") is None


def test_a_wallet_changed_after_listing_is_not_answered_by_the_old_approval(journal):
    ledger = new_wallet_ledger(journal)
    payer, chain, circle = make_payer(ledger)
    chain.unapproved.add(WALLET_B.lower())
    run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer)
    ledger.supplier = Supplier(name="S", wallet_address=WALLET_C)
    journal.record_wallet_proof(WalletProof(supplier="S", wallet=WALLET_C, nonce="m",
                                            signature="0x", signed_at=NOW))
    chain.unapproved.clear()  # WALLET_B approved, WALLET_C too, but nobody was asked about C
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW + timedelta(minutes=15),
                       payments=payer)
    assert [d.action for d in report.decisions] == [Action.ASK] and circle.sent == []


def test_a_question_with_other_reasons_is_not_answered_by_a_wallet_approval(journal, tmp_path):
    payer, chain, _ = make_payer()
    chain.unapproved.add(WALLET_A.lower())
    payer.approvals_file = tmp_path / "pending.json"
    asked = replace(decision(), action=Action.ASK, findings=(
        Finding("payee_wallet", Outcome.ASK, "new wallet"),
        Finding("price_anomaly", Outcome.ASK, "price up 40%"),
    ))
    result = payer.settle(PaymentPlan(pay_now=(), deferred=(), held=(), asked=(asked,),
                                      budget_left=Decimal(0)), journal)
    assert result.needs_approval == () and journal.latest_payment("PINV-1") is None

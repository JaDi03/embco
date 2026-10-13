"""The agent's questions reach the owner's chat once, while they stand: with buttons when only the
owner's yes is missing, as a reminder to use the dashboard when the owner's wallet is needed;
and never as an answer to a message the owner is still waiting on."""

from datetime import UTC, date, datetime
from decimal import Decimal

from agent.guardrails.controls.base import Finding, Outcome
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.models import AgentDecision, Choice
from services.shops import chat
from services.shops.questions import NO, YES, answer_text, new_questions

NOW = datetime(2026, 10, 13, 19, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal("250"), weekly_budget=Decimal("2000"))
RICH = {"owner_balance": "1000", "available_to_pay": "1000"}


def decision(invoice="ACC-PINV-2026-00012", action=Action.PAY, amount="184.0", findings=None):
    return Decision(invoice=invoice, supplier="Northern Grains", amount=Decimal(amount),
                    due_date=date(2026, 10, 19), action=action, reasons=("to weigh: receipt",),
                    findings=findings or (Finding("three_way_match", Outcome.NOTE, "no receipt"),))


def asks(d, question="Did the flour arrive?", fingerprint_of=None):
    return AgentDecision(invoice=d.invoice, fingerprint=fingerprint(fingerprint_of or d),
                         choice=Choice.ASK, reason="no receipt", question=question,
                         recommendation="Pay it if it arrived.")


def questions(folder, d, agent=None, funds=RICH, room=Decimal("2000"), paused=False):
    return new_questions(folder, [d], {d.invoice: agent or asks(d)}, NOW, policy=POLICY,
                         funds=lambda: funds, room=lambda: room, paused=paused)


def test_only_a_yes_missing_comes_with_buttons_short_and_once(tmp_path):
    d = decision()
    first = questions(tmp_path, d)
    assert [e["text"] for e in first] == [
        "Pay 184 USDC to Northern Grains (ACC-PINV-2026-00012)?\nDid the flour arrive?"]
    assert first[0]["buttons"] is True
    chat.append_chat(tmp_path, first)
    assert questions(tmp_path, d, asks(d, "Asked again?")) == []


def test_what_needs_the_wallet_is_a_reminder_to_use_the_dashboard(tmp_path):
    d = decision(invoice="ACC-PINV-2026-00017", amount="476.0")
    [entry] = questions(tmp_path, d, funds={"owner_balance": "165.959618",
                                            "available_to_pay": "165.959618"})
    assert entry["buttons"] is False
    assert entry["text"] == (
        "Northern Grains, 476 USDC (ACC-PINV-2026-00017): needs you in the dashboard: raise the "
        "per-payment limit (now 250 USDC), add USDC to your wallet (165.95 there).")


def test_each_wallet_step_is_named(tmp_path):
    first = decision(action=Action.ASK, findings=(
        Finding("payee_wallet", Outcome.ASK, "first payment to this supplier"),))
    assert "approve Northern Grains's wallet" in questions(tmp_path, first)[0]["text"]
    week = questions(tmp_path / "w", decision(), room=Decimal("100.004"))[0]["text"]
    assert "raise the weekly limit or wait for it to renew (100 USDC left)" in week
    owed = questions(tmp_path / "a", decision(),
                     funds={"owner_balance": "500", "available_to_pay": "50"})[0]["text"]
    assert "authorize payments again" in owed
    assert "resume payments" in questions(tmp_path / "p", decision(), paused=True)[0]["text"]


def test_when_the_wallet_step_is_done_the_question_comes_again_with_buttons(tmp_path):
    d = decision()
    chat.append_chat(tmp_path, questions(tmp_path, d, paused=True))
    [again] = questions(tmp_path, d)
    assert again["buttons"] is True


def test_only_a_question_that_still_stands_is_sent(tmp_path):
    d = decision()
    other = decision(action=Action.HOLD)
    assert questions(tmp_path, d, asks(d, fingerprint_of=other)) == []
    assert questions(tmp_path, other) == []
    held = AgentDecision(invoice=d.invoice, fingerprint=fingerprint(d), choice=Choice.HOLD,
                         reason="wait")
    assert questions(tmp_path, d, held) == []


def test_the_chain_is_read_only_while_a_question_stands(tmp_path):
    read = []
    d = decision()
    held = AgentDecision(invoice=d.invoice, fingerprint=fingerprint(d), choice=Choice.HOLD,
                         reason="wait")
    new_questions(tmp_path, [d], {d.invoice: held}, NOW, policy=POLICY,
                  funds=lambda: read.append(1), room=lambda: read.append(1))
    assert read == []


def test_a_question_is_not_an_answer_to_the_owner(tmp_path):
    chat.append_chat(tmp_path, [{"from": chat.OWNER, "text": "What is due?"}])
    chat.append_chat(tmp_path, questions(tmp_path, decision()))
    assert [m["text"] for m in chat.unanswered(tmp_path)] == ["What is due?"]


def test_a_quick_answer_carries_the_question_it_answers():
    assert answer_text("ACC-PINV-2026-00012", YES, "Did the flour arrive?") == (
        "About ACC-PINV-2026-00012 (you asked: Did the flour arrive?): yes, go ahead and pay it.")
    assert answer_text("ACC-PINV-2026-00012", NO) == (
        "About ACC-PINV-2026-00012: no, hold it for now.")

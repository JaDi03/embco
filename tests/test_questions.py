"""The agent's questions reach the owner's chat once, while they stand, and never count as an
answer to a message the owner is still waiting on."""

from datetime import UTC, date, datetime
from decimal import Decimal

from agent.guardrails.controls.base import Finding, Outcome
from agent.guardrails.rules import Action, Decision, fingerprint
from agent.models import AgentDecision, Choice
from services.shops import chat
from services.shops.questions import NO, YES, answer_text, new_questions

NOW = datetime(2026, 10, 13, 19, 0, tzinfo=UTC)


def decision(invoice="ACC-PINV-2026-00012", action=Action.PAY):
    return Decision(invoice=invoice, supplier="Northern Grains", amount=Decimal("184"),
                    due_date=date(2026, 10, 19), action=action, reasons=("to weigh: receipt",),
                    findings=(Finding("three_way_match", Outcome.NOTE, "no receipt"),))


def asks(d, question="Did the flour arrive?", fingerprint_of=None):
    return AgentDecision(invoice=d.invoice, fingerprint=fingerprint(fingerprint_of or d),
                         choice=Choice.ASK, reason="no receipt", question=question,
                         recommendation="Pay it if it arrived.")


def test_a_question_is_sent_once_with_the_invoice_and_the_recommendation(tmp_path):
    d = decision()
    first = new_questions(tmp_path, [d], {d.invoice: asks(d)}, NOW)
    assert len(first) == 1
    assert first[0]["text"].startswith(
        "Question about ACC-PINV-2026-00012 (Northern Grains, 184 USDC): Did the flour arrive?")
    assert "I recommend: Pay it if it arrived." in first[0]["text"]
    chat.append_chat(tmp_path, first)
    assert new_questions(tmp_path, [d], {d.invoice: asks(d, "Asked again?")}, NOW) == []


def test_only_a_question_that_still_stands_is_sent(tmp_path):
    d = decision()
    other = decision(action=Action.HOLD)
    assert new_questions(tmp_path, [d], {d.invoice: asks(d, fingerprint_of=other)}, NOW) == []
    assert new_questions(tmp_path, [other], {other.invoice: asks(other)}, NOW) == []
    held = AgentDecision(invoice=d.invoice, fingerprint=fingerprint(d), choice=Choice.HOLD,
                         reason="wait")
    assert new_questions(tmp_path, [d], {d.invoice: held}, NOW) == []


def test_a_question_is_not_an_answer_to_the_owner(tmp_path):
    chat.append_chat(tmp_path, [{"from": chat.OWNER, "text": "What is due?"}])
    d = decision()
    chat.append_chat(tmp_path, new_questions(tmp_path, [d], {d.invoice: asks(d)}, NOW))
    assert [m["text"] for m in chat.unanswered(tmp_path)] == ["What is due?"]


def test_the_quick_answers_say_what_the_dashboard_buttons_say():
    assert answer_text("ACC-PINV-2026-00012", YES) == (
        "About ACC-PINV-2026-00012: yes, go ahead and pay it.")
    assert answer_text("ACC-PINV-2026-00012", NO) == (
        "About ACC-PINV-2026-00012: no, hold it for now.")

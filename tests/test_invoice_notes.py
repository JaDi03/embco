"""The agent's decision on an open invoice is written in that invoice's timeline in the ERP, once
per change, in the model's words."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from agent.guardrails.rules import DecisionEngine, PolicyConfig, fingerprint
from agent.models import AgentDecision, Choice
from services.erp import ErpnextAdapter, LedgerError
from services.shops.invoice_notes import NOTES_FILE, current_decisions, note_html, write_notes
from support import FakeLedger

AT = datetime(2026, 11, 2, 9, 15, tzinfo=UTC)


def decided(choice=Choice.SCHEDULE, reason="Due on the 10th; paying on the 8th keeps room.",
            fingerprint="f1", **extra) -> AgentDecision:
    return AgentDecision(invoice="ACC-PINV-2026-00005", fingerprint=fingerprint, choice=choice,
                         reason=reason, decided_at=AT, **extra)


class Timeline:
    def __init__(self, fail: int = 0) -> None:
        self.comments: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, invoice, content):
        if self.fail:
            self.fail -= 1
            raise LedgerError("ERPNext returned HTTP 502")
        self.comments.append((invoice, content))


def test_a_decision_is_written_once_and_again_only_when_it_changes(tmp_path):
    timeline = Timeline()
    scheduled = decided(pay_on=date(2026, 11, 8))
    assert write_notes(tmp_path, [scheduled], timeline) == 1
    assert write_notes(tmp_path, [scheduled], timeline) == 0  # the same check again
    reworded = decided(pay_on=date(2026, 11, 8), reason="Same plan, other words.")
    assert write_notes(tmp_path, [reworded], timeline) == 0  # the decision did not change
    assert write_notes(tmp_path, [decided(Choice.HOLD, "The receipt is missing.")], timeline) == 1
    assert write_notes(tmp_path, [decided(Choice.HOLD, "x", fingerprint="f2")], timeline) == 1
    assert len(timeline.comments) == 3


def test_the_note_carries_the_models_reason_escaped_and_when_it_was_decided():
    note = note_html(decided(pay_on=date(2026, 11, 8), reason="Pay <b>later</b> & keep room"))
    assert note.startswith("<b>embco agent: Pay on 2026-11-08</b><br>")
    assert "Pay &lt;b&gt;later&lt;/b&gt; &amp; keep room" in note
    assert "Decided 2026-11-02 09:15 UTC" in note
    ask = note_html(decided(Choice.ASK, "Billed 2, received 1: pay it?",
                            recommendation="Ask the supplier for a credit note."))
    assert "embco agent: Ask the owner" in ask and "Recommendation: Ask the supplier" in ask


def test_an_erp_that_does_not_answer_gets_the_note_at_the_next_check(tmp_path):
    timeline = Timeline(fail=1)
    hold = decided(Choice.HOLD, "The receipt is missing.")
    assert write_notes(tmp_path, [hold], timeline) == 0
    assert not (tmp_path / NOTES_FILE).exists()
    assert write_notes(tmp_path, [hold], timeline) == 1


def test_only_decisions_made_on_todays_facts_are_written():
    policy = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))
    [today] = DecisionEngine(FakeLedger(), policy).decide_all()
    fresh = AgentDecision(invoice=today.invoice, fingerprint=fingerprint(today),
                          choice=Choice.HOLD, reason="r")
    stale = AgentDecision(invoice=today.invoice, fingerprint="old", choice=Choice.HOLD, reason="r")
    assert current_decisions([today], {today.invoice: fresh}) == [fresh]
    assert current_decisions([today], {today.invoice: stale}) == []


def test_the_adapter_comments_as_erpnexts_own_add_comment():
    sent = []

    def erp(request):
        sent.append((request.url.path, dict(httpx.QueryParams(request.content.decode()))))
        return httpx.Response(200, json={"message": {"name": "c1"}})

    adapter = ErpnextAdapter("https://erp.test", "k", "s",
                             client=httpx.Client(transport=httpx.MockTransport(erp)))
    adapter.comment_on_invoice("ACC-PINV-2026-00005", "<b>embco agent: Hold</b>")
    [(path, form)] = sent
    assert path == "/api/method/frappe.desk.form.utils.add_comment"
    assert form["reference_doctype"] == "Purchase Invoice"
    assert form["reference_name"] == "ACC-PINV-2026-00005"
    assert json.dumps(form["content"]) == json.dumps("<b>embco agent: Hold</b>")

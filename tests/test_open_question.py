"""An open question to the owner stays open: a session woken by something else cannot hold the
invoice over it and close the question the owner is about to answer."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.memory import SqliteJournal
from agent.models import AgentDecision, Choice
from agent.tools import Toolbox, ToolError

NOW = datetime(2026, 10, 24, 7, 19, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))
# Glocery, 2026-10-24: the checks hold Ikeja's first invoice (wallet not signed); at 02:49 the
# agent asked the owner; at 07:19, woken by the supplier's new email, it held the invoice while
# saying it still waited for the owner's yes, and the question closed
HELD = Decision(invoice="ACC-PINV-2026-00001", supplier="Ikeja Fresh Produce Ltd",
                amount=Decimal(75), due_date=None, action=Action.HOLD,
                reasons=("the supplier has not signed for its wallet",), findings=())
ASKED = AgentDecision(invoice=HELD.invoice, fingerprint=fingerprint(HELD), choice=Choice.ASK,
                      reason="Should I ask the supplier to confirm the wallet now?",
                      question="Should I ask the supplier to confirm the wallet now?")


def box(journal, decision=HELD, messages=()):
    return Toolbox(decisions=[decision], ledger=None, journal=journal, policy=POLICY, now=NOW,
                   zone=UTC, standing={HELD.invoice: ASKED}, messages=tuple(messages))


def test_a_session_woken_by_something_else_leaves_the_open_question(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        quiet = box(journal)
        assert quiet.undecided() == []  # the question already decides it: nothing to do
        with pytest.raises(ToolError, match="still open"):
            quiet.call("hold", {"invoice": HELD.invoice,
                                "reason": "Email on file; still waiting for the owner's yes."})
        assert quiet.current(HELD.invoice) == ASKED


def test_the_owner_s_answer_or_a_new_decision_lets_it_hold(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        answered = box(journal, messages=["About ACC-PINV-2026-00001: no."])
        assert answered.call("hold", {"invoice": HELD.invoice, "reason": "The owner said no."})
        changed = Decision(**{**HELD.__dict__, "reasons": ("a duplicate bill",)})
        moved = box(journal, decision=changed)  # the question was about another decision
        assert moved.call("hold", {"invoice": HELD.invoice, "reason": "A duplicate bill."})

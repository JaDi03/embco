"""What reaches the owner is plain words: the model's tool-call markup never does, and a question
is short enough for a card in the chat."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.tools import Toolbox, ToolError

# Seen on 2026-10-20: the recommendation leaked into the question with the tool-call syntax.
LEAKED = ("Did the flour arrive? Reply yes to pay now, or no to hold it.</question>\n"
          '<parameter name="recommendation">Pay only if you confirm the flour arrived.')


@pytest.fixture
def box(tmp_path):
    d = Decision(invoice="PINV-1", supplier="Northern Grains & Cereals Co.", amount=Decimal(92),
                 due_date=None, action=Action.PAY, reasons=("r",), findings=())
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        yield Toolbox(decisions=[d], ledger=None, journal=journal,
                      policy=PolicyConfig(max_per_payment=Decimal(300),
                                          weekly_budget=Decimal(2000)),
                      now=datetime(2026, 10, 20, 2, 23, tzinfo=UTC), zone=UTC,
                      messages=("Did you pay Northern?",))


def ask(box, question, recommendation="Hold it until the delivery is confirmed."):
    return box.call("ask_owner", {"invoice": "PINV-1", "question": question,
                                  "recommendation": recommendation})


def test_tool_markup_in_a_question_is_refused_and_the_model_rewrites_it(box):
    with pytest.raises(ToolError, match="plain words"):
        ask(box, LEAKED)
    ask(box, "Northern Grains billed 2 sacks of flour but no delivery is recorded. Did it arrive?")
    assert box.chosen["PINV-1"].question.startswith("Northern Grains billed")


def test_markup_is_refused_in_messages_and_the_summary_too(box):
    with pytest.raises(ToolError, match="plain words"):
        box.call("reply_owner", {"text": "Not yet.</text>"})
    ask(box, "Northern Grains billed 2 sacks of flour but no delivery is recorded. Did it arrive?")
    box.call("reply_owner", {"text": "Not yet: I asked you about the flour first."})
    with pytest.raises(ToolError, match="plain words"):
        box.call("finish", {"summary": 'Done.<parameter name="x">'})


def test_a_question_fits_a_card(box):
    with pytest.raises(ToolError, match="too long"):
        ask(box, "Northern Grains " + "x" * 320)


def test_ordinary_words_with_angle_brackets_still_pass(box):
    ask(box, "Northern Grains billed 92 USDC, <5% above usual, but no delivery is recorded. "
             "Did it arrive?")

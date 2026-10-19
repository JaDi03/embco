"""Every tool the model calls is kept, in order, with what came back, and shows in the activity."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.models import Session, Wake
from agent.tools import Toolbox, ToolError
from agent.tools.trace import line, step
from services.shops import activity

TX = "0x" + "ab" * 32
NOW = datetime(2026, 10, 19, 9, 10, tzinfo=UTC)


def test_a_step_keeps_the_values_the_model_chose_but_not_its_long_words():
    s = step("schedule_payment", {"invoice": "ACC-PINV-2026-00017", "pay_on": "2026-10-20",
                                  "reason": "Due 24 Oct; paying on the 20th keeps room."},
             result="ok")
    assert s == {"tool": "schedule_payment", "ok": True,
                 "args": {"invoice": "ACC-PINV-2026-00017", "pay_on": "2026-10-20"}}
    assert line(s) == "schedule_payment(invoice=ACC-PINV-2026-00017, pay_on=2026-10-20) -> done"


def test_a_payment_step_carries_its_transaction_and_a_refusal_its_reason():
    paid = step("pay_now", {"invoice": "ACC-PINV-2026-00018", "reason": "due today"},
                result=json.dumps({"status": "paid", "tx": TX}))
    assert line(paid) == f"pay_now(invoice=ACC-PINV-2026-00018) -> done, tx {TX}"
    refused = step("pay_now", {"invoice": "X", "reason": "r"},
                   refused="the checks hold X: duplicate bill")
    assert line(refused) == "pay_now(invoice=X) -> refused: the checks hold X: duplicate bill"


def test_the_toolbox_keeps_every_call_in_order_refusals_included(tmp_path):
    d = Decision(invoice="PINV-1", supplier="S", amount=Decimal(50), due_date=None,
                 action=Action.PAY, reasons=("r",), findings=())
    policy = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        box = Toolbox(decisions=[d], ledger=None, journal=journal, policy=policy, now=NOW, zone=UTC,
                      funds=lambda: {"weekly_room_resets_at_utc": "2026-10-21 00:00",
                                     "owner_balance": "263.74", "payments_authorized": "yes"})
        box.call("cash_position", {})
        with pytest.raises(ToolError):
            box.call("no_such_tool", {})
    assert [(s["tool"], s["ok"]) for s in box.trace] == [("cash_position", True),
                                                         ("no_such_tool", False)]


def test_the_trace_is_remembered_and_shown_between_the_wake_and_the_decisions(tmp_path):
    trace = (step("cash_position", {}, result="{}"),
             step("pay_now", {"invoice": "PINV-1", "reason": "due"}, result=json.dumps({"tx": TX})))
    session = Session(at=NOW, wakes=(Wake("round", "The daily round."),), finished=True,
                      summary="Paid one bill.", steps=2, trace=trace)
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        journal.record_session(session)
        [kept] = journal.sessions()
    assert kept.trace == trace
    events = activity.session_events(kept)
    assert [e["kind"] for e in events][:3] == ["reflex", "tool", "tool"]
    assert events[2]["text"] == f"pay_now(invoice=PINV-1) -> done, tx {TX}"
    assert "tx" not in events[2]  # the payment's own line carries the transaction for the receipt

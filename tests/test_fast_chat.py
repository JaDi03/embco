"""Talking to the agent does not re-read the whole ERP: between two checks it answers on the last
check's decisions and looks up only what the question needs."""

from datetime import UTC, date, datetime
from decimal import Decimal

from agent.agent import SessionRun
from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.models import Usage
from agent.prompt import chat_briefing
from agent.tools import Toolbox
from services.shops import chat, run_shop
from support import FakeLedger
from test_hub import auth, clock  # noqa: F401
from test_owner_actions import SHOP, hub, journal, owner  # noqa: F401

NOW = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))


def decision(name, supplier, due, action=Action.PAY, amount="50"):
    return Decision(invoice=name, supplier=supplier, amount=Decimal(amount), due_date=due,
                    action=action, reasons=("r",), findings=())


DECISIONS = [
    decision("PINV-1", "Central Provisions Ltd", date(2026, 10, 10)),
    decision("PINV-2", "Central Provisions Ltd", date(2026, 10, 20), Action.HOLD),
    decision("PINV-3", "Arches Bakery", date(2026, 10, 10), Action.ASK, "125"),
]


def box(tmp_path, **kw):
    journal = SqliteJournal(tmp_path / "j.sqlite3")  # noqa: F811
    return Toolbox(decisions=DECISIONS, ledger=None, journal=journal, policy=POLICY, now=NOW,
                   zone=UTC, **kw)


def search(b, supplier="", due_from="", due_to="", checks_say=""):
    import json
    return json.loads(b.call("search_invoices", {"supplier": supplier, "due_from": due_from,
                                                 "due_to": due_to, "checks_say": checks_say}))


def test_the_agent_finds_invoices_by_supplier_date_or_state_without_reading_all(tmp_path):
    b = box(tmp_path)
    tomorrow = search(b, due_from="2026-10-10", due_to="2026-10-10")
    assert tomorrow["count"] == 2 and tomorrow["total"] == "175"
    assert [r["invoice"] for r in search(b, supplier="central")["invoices"]] == ["PINV-1", "PINV-2"]
    assert search(b, checks_say="ask")["invoices"][0]["invoice"] == "PINV-3"


def test_a_conversation_can_end_without_deciding_every_invoice(tmp_path):
    b = box(tmp_path, messages=("hi",), require_all=False)
    b.call("reply_owner", {"text": "Hello."})
    assert b.call("finish", {"summary": "Answered."}) == "session finished"


def test_the_chat_briefing_is_a_short_picture_not_every_invoice(tmp_path):
    text = chat_briefing(box(tmp_path, messages=("What is due tomorrow?",)), (), ())
    assert "open_invoices\": 3" in text and "due_within_7_days" in text
    assert "search_invoices" in text and "PINV-2" not in text  # not due within 7 days


class CountingLedger(FakeLedger):
    def __init__(self):
        super().__init__()
        self.full_reads = 0

    def list_unpaid_purchase_invoices(self):
        self.full_reads += 1
        return super().list_unpaid_purchase_invoices()


class Answering:
    """Stands in for Claude: answers chats; decides every invoice in a check."""

    def __init__(self, model="claude-haiku-5-5", effort=None, pay=False, **_):
        self.model, self.pay = model, pay

    def run(self, toolbox, briefing):
        if toolbox.messages:
            toolbox.call("reply_owner", {"text": "Nothing is due tomorrow."})
            if self.pay:
                toolbox.call("pay_now", {"invoice": "PINV-1", "reason": "the owner asked"})
        else:
            for invoice in toolbox.undecided():
                toolbox.call("hold", {"invoice": invoice, "reason": "checking"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def test_a_message_between_checks_is_answered_without_reading_the_whole_erp(hub, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agent.wiring.ClaudeBrain", Answering)
    store, ledger = hub["store"], CountingLedger()
    folder = store.folder(SHOP)

    def owner_writes(seconds):
        chat.drop_message(folder, "What is due tomorrow?", "owner", NOW)

    run_shop(store, SHOP, cycles=2, sleep=owner_writes, erp=lambda settings: ledger)
    assert ledger.full_reads == 1  # the first check only; the chat did not read it again
    assert [e["from"] for e in chat.read_chat(folder)] == ["owner", "agent"]


def full_reads_with_owner_writing_each_time(store, monkeypatch, pay):
    from dataclasses import replace

    monkeypatch.setattr("agent.wiring.ClaudeBrain",
                        lambda *a, **kw: Answering(*a, **kw, pay=pay))
    store.save(replace(store.config(SHOP), max_per_payment="5000"), store.credentials(SHOP))
    ledger, folder = CountingLedger(), store.folder(SHOP)

    def owner_writes(seconds):
        chat.drop_message(folder, "Pay PINV-1 now", "owner", NOW)

    run_shop(store, SHOP, cycles=3, sleep=owner_writes, erp=lambda settings: ledger)
    return ledger.full_reads


def test_asking_the_agent_to_pay_brings_the_next_full_check_forward(hub, monkeypatch):  # noqa: F811
    assert full_reads_with_owner_writing_each_time(hub["store"], monkeypatch, pay=True) == 2


def test_without_a_payment_asked_chats_keep_waiting_for_the_check_on_its_clock(hub, monkeypatch):  # noqa: F811
    assert full_reads_with_owner_writing_each_time(hub["store"], monkeypatch, pay=False) == 1

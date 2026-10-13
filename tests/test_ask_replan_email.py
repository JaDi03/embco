"""Paying late is the owner's call; the agent plans again when the money changes; it writes to a
supplier only after the owner agreed in the conversation."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agent.agent import SessionRun
from agent.guardrails.controls.base import Finding, Outcome
from agent.guardrails.controls.payee_wallet import WAITING_FOR_SIGNATURE
from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.models import Session, Usage, Wake
from agent.reflexes.funds import money_wake
from agent.think import BrainSetup, think
from agent.tools import Toolbox, ToolError

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
LAGOS = timezone(timedelta(hours=1))
POLICY = PolicyConfig(max_per_payment=Decimal(420), weekly_budget=Decimal(2000))
OVERDUE = Decision(invoice="PINV-00016", supplier="Central Provisions Ltd", amount=Decimal(420),
                   due_date=date(2026, 10, 9), action=Action.PAY, reasons=("r",), findings=())
MONEY = {"available_to_pay": "283.44", "payments_authorized": "yes", "owner_balance": "283.44"}


def box(journal, **kw):
    return Toolbox(decisions=[OVERDUE], ledger=None, journal=journal, policy=POLICY, now=NOW,
                   zone=LAGOS, **kw)


# ---- paying late


def test_the_agent_cannot_choose_on_its_own_to_pay_after_the_due_date(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="the owner's call"):
            box(journal).call("schedule_payment", {"invoice": "PINV-00016",
                                                    "pay_on": "2026-10-14", "reason": "room"})


def test_after_the_owner_answers_in_the_conversation_it_may(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        b = box(journal, messages=("Pay it on the 14th, I'll add the funds",), require_all=False)
        out = b.call("schedule_payment", {"invoice": "PINV-00016", "pay_on": "2026-10-14",
                                          "reason": "the owner chose the 14th"})
        assert "after its due date" in out


# ---- planning again when the money changes


def session_with(money):
    return Session(at=NOW - timedelta(hours=1), wakes=(Wake("round", "r"),), finished=True,
                   money=money)


def test_added_funds_wake_the_agent_to_plan_again():
    wake = money_wake([session_with(MONEY)], {**MONEY, "available_to_pay": "520"}, [OVERDUE],
                      set())
    assert wake.kind == "money" and "520 USDC can be paid now (was 283.44)" in wake.text


def test_the_same_money_or_nothing_payable_wakes_nobody():
    assert money_wake([session_with(MONEY)], MONEY, [OVERDUE], set()) is None
    assert money_wake([session_with(MONEY)], {**MONEY, "available_to_pay": "520"}, [OVERDUE],
                      {"PINV-00016"}) is None
    assert money_wake([], {**MONEY, "available_to_pay": "520"}, [OVERDUE], set()) is None


def test_the_owners_own_network_fees_do_not_wake_the_agent():
    seen = {**MONEY, "available_to_pay": "349.947054"}
    fees = {**MONEY, "available_to_pay": "349.945618"}  # the gas of approving a wallet
    assert money_wake([session_with(seen)], fees, [OVERDUE], set()) is None
    paused = {**fees, "payments_authorized": "no"}  # the authorization always counts
    assert money_wake([session_with(seen)], paused, [OVERDUE], set()).kind == "money"
    added = {**MONEY, "available_to_pay": "350.947054"}  # one USDC more: worth a look
    assert money_wake([session_with(seen)], added, [OVERDUE], set()).kind == "money"


class Quiet:
    model = "scripted"

    def run(self, toolbox, briefing):
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "waiting"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def test_each_session_keeps_what_could_be_paid_when_it_ran(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        setup = BrainSetup(brain=Quiet(), zone=LAGOS, funds=lambda: MONEY)
        think(setup, journal=journal, ledger=None, decisions=[OVERDUE], changes=(),
              policy=POLICY, done=set(), now=NOW)
        assert journal.sessions()[-1].money == MONEY


# ---- writing to a supplier


def test_a_supplier_is_written_to_only_after_the_owner_agreed(tmp_path):
    sent = []

    def mailer(supplier, subject, body):
        sent.append((supplier, subject, body))
        return "orders@central.example"

    args = {"supplier": "Central Provisions Ltd", "subject": "Invoice CPL-6011",
            "body": "Invoice CPL-6011 will be paid on 14 October. Thank you."}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="owner agreed"):
            box(journal, mailer=mailer).call("email_supplier", args)
        agreed = box(journal, mailer=mailer, messages=("yes, tell them",), require_all=False)
        assert "orders@central.example" in agreed.call("email_supplier", args)
    assert sent == [("Central Provisions Ltd", "Invoice CPL-6011",
                     "Invoice CPL-6011 will be paid on 14 October. Thank you.")]


def test_emails_carry_no_link_and_are_few(tmp_path):
    def mailer(*args):
        return "orders@central.example"

    base = {"supplier": "Central Provisions Ltd", "subject": "Payment date"}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        b = box(journal, mailer=mailer, messages=("ok",), require_all=False, emails_today=2)
        with pytest.raises(ToolError, match="no links"):
            b.call("email_supplier", {**base, "body": "See https://pay.example now"})
        b.call("email_supplier", {**base, "body": "Paid on the 14th."})
        with pytest.raises(ToolError, match="at most 3"):
            b.call("email_supplier", {**base, "body": "Paid on the 14th."})
        with pytest.raises(ToolError, match="no open invoice"):
            box(journal, mailer=mailer, messages=("ok",)).call(
                "email_supplier", {**base, "supplier": "Someone Else", "body": "Hello."})


def test_the_email_leaves_from_the_erp_to_the_supplier_on_record_with_the_text_escaped():
    from services.erp.erpnext.mail import email_supplier_message

    class Frappe:
        def __init__(self):
            self.calls = []

        def get_doc(self, doctype, name):
            return {"email_id": "orders@central.example"}

        def call_method(self, method, form):
            self.calls.append(form)

    frappe = Frappe()
    to = email_supplier_message(frappe, "Central Provisions Ltd", "embcocompany",
                                "Invoice CPL-6011", "Paid on <b>14 Oct</b>.\n\nThank you.")
    [form] = frappe.calls
    assert to == "orders@central.example" and form["recipients"] == to
    assert form["subject"] == "embcocompany: Invoice CPL-6011"
    assert form["content"] == "<p>Paid on &lt;b&gt;14 Oct&lt;/b&gt;.</p><p>Thank you.</p>"


# ---- asking a supplier to confirm its wallet

UNSIGNED = Decision(
    invoice="PINV-00017", supplier="Lekki Oils & Spices Ltd", amount=Decimal(120),
    due_date=date(2026, 10, 22), action=Action.HOLD, reasons=("r",),
    findings=(Finding("payee_wallet", Outcome.HOLD,
                      f"first payment, wallet 0xE6Bb...Ec1A; {WAITING_FOR_SIGNATURE}"),))


def test_the_wallet_notice_goes_once_after_the_owner_agreed_and_only_to_a_wallet_waiting(tmp_path):
    sent = []

    def notice(supplier):
        sent.append(supplier)
        return "accounts@lekki.example"

    def notice_box(journal, **kw):
        return Toolbox(decisions=[OVERDUE, UNSIGNED], ledger=None, journal=journal, policy=POLICY,
                       now=NOW, zone=LAGOS, wallet_notice=notice, **kw)

    lekki = {"supplier": "Lekki Oils & Spices Ltd"}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="owner agreed"):
            notice_box(journal).call("ask_supplier_to_confirm_wallet", lekki)
        b = notice_box(journal, messages=("yes, ask them",), require_all=False, emails_today=3)
        with pytest.raises(ToolError, match="no wallet waiting"):
            b.call("ask_supplier_to_confirm_wallet", {"supplier": "Central Provisions Ltd"})
        assert "accounts@lekki.example" in b.call("ask_supplier_to_confirm_wallet", lekki)
        with pytest.raises(ToolError, match="already went"):
            b.call("ask_supplier_to_confirm_wallet", lekki)
    assert sent == ["Lekki Oils & Spices Ltd"] and b.notified_wallets == ["Lekki Oils & Spices Ltd"]


def test_the_wallet_notice_is_the_standard_one_with_the_wallet_on_record():
    from services.erp.erpnext.adapter import ErpnextAdapter

    class Frappe:
        def __init__(self):
            self.calls = []

        def get_doc(self, doctype, name):
            return {"email_id": "accounts@lekki.example"}

        def call_method(self, method, form):
            self.calls.append(form)

    class Supplier:
        wallet_address = "0xE6Bb000000000000000000000000000000Ec1A"

    adapter = ErpnextAdapter.__new__(ErpnextAdapter)
    adapter._frappe, adapter._company = Frappe(), "lopezgrocery"
    adapter.get_supplier = lambda name: Supplier()
    to = adapter.email_wallet_notice("Lekki Oils & Spices Ltd", "app.embco.xyz/supplier")
    [form] = adapter._frappe.calls
    assert to == "accounts@lekki.example"
    assert form["subject"] == "lopezgrocery: confirm the wallet you are paid to"
    assert "app.embco.xyz/supplier" in form["content"]
    assert Supplier.wallet_address in form["content"]
    assert "http" not in form["content"]

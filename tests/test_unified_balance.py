"""The owner's Unified Balance: the agent sees the USDC the owner keeps on other chains, chooses
how much to bring from each one, and the money always lands in the owner's own wallet on Arc.
Built on the pilot's 00017 (Adeyemi, 476 USDC) when the owner's wallet on Arc held 165.95."""

import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.models import Session, Wake
from agent.think import BrainSetup, money_after
from agent.tools import Toolbox, ToolError
from services.shops import activity
from services.shops.agent import unified_view
from services.unified_balance import (
    Allocation,
    ChainFunds,
    OwnerFunds,
    UnifiedBalance,
    UnifiedBalanceError,
)

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
OWNER = "0x2471658CD7A7B10d4C92113B0777dA59317e839a"
AGENT = "0xeAc91C9491F0D391c80f17C4267A3329Ad7Ba862"
KEY, SECRET = "fake-circle-key", "fake-entity-secret"
INVOICE = "ACC-PINV-2026-00017"
FEES = [{"type": "provider", "token": "USDC", "amount": "0.0155"},
        {"type": "gasFee", "token": "USDC", "amount": "0.048274"},
        {"type": "forwarder", "token": "USDC", "amount": "0.014162"}]


def kit_answers(*answers):
    """Stands in for `node kit/cli.mjs`: records each call, answers the next JSON line."""
    calls, queue = [], list(answers)

    def run(args, *, input, env, **_):
        calls.append((args[-1], json.loads(input), env))
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(queue.pop(0)) + "\n",
                                           stderr="")
    return calls, run


# ---- the kit, run from Python


def test_balances_come_from_the_kit_with_the_secrets_only_in_its_environment():
    calls, run = kit_answers({"ok": {"chains": [
        {"chain": "Base_Sepolia", "confirmed": "320.000000", "pending": "0.000000",
         "delegate": "ready"},
        {"chain": "Arbitrum_Sepolia", "confirmed": "50.000000", "pending": "10.000000",
         "delegate": "none"}]}})
    kit = UnifiedBalance(KEY, SECRET, run=run)
    chains = kit.balances(OWNER, AGENT)
    assert chains == [ChainFunds("Base_Sepolia", Decimal(320), Decimal(0), "ready"),
                      ChainFunds("Arbitrum_Sepolia", Decimal(50), Decimal(10), "none")]
    assert [c.agent_can_spend for c in chains] == [True, False]
    command, payload, env = calls[0]
    assert (command, payload) == ("balances", {"owner": OWNER, "agent": AGENT})
    assert env["CIRCLE_API_KEY"] == KEY and env["CIRCLE_ENTITY_SECRET"] == SECRET
    assert KEY not in repr(kit) and SECRET not in repr(kit)


def test_a_spend_sends_the_split_and_its_exact_total():
    calls, run = kit_answers({"ok": {"txHash": "0x" + "1" * 64, "fees": FEES}})
    UnifiedBalance(KEY, SECRET, run=run).spend(OWNER, AGENT, [
        Allocation("Base_Sepolia", Decimal("300")),
        Allocation("Arbitrum_Sepolia", Decimal("10.05"))])
    command, payload, _ = calls[0]
    assert command == "spend"
    assert payload == {"owner": OWNER, "agent": AGENT, "amount": "310.050000",
                       "allocations": [{"chain": "Base_Sepolia", "amount": "300.000000"},
                                       {"chain": "Arbitrum_Sepolia", "amount": "10.050000"}]}


def test_the_kits_error_comes_back_without_the_secrets():
    _, run = kit_answers({"error": f"Circle rejected the request with key {KEY}"})
    with pytest.raises(UnifiedBalanceError) as raised:
        UnifiedBalance(KEY, SECRET, run=run).balances(OWNER, AGENT)
    assert KEY not in str(raised.value) and "[hidden]" in str(raised.value)


def test_no_node_is_a_plain_error():
    def missing(*_, **__):
        raise FileNotFoundError("node")
    with pytest.raises(UnifiedBalanceError, match="did not run"):
        UnifiedBalance(KEY, SECRET, run=missing).balances(OWNER, AGENT)


def test_the_money_always_goes_to_the_contracts_owner():
    seen = []

    class Kit:
        def spend(self, owner, agent, allocations):
            seen.append((owner, agent))
            return {}

    payer = SimpleNamespace(shop="0x" + "5c" * 20, agent_address=lambda: AGENT,
                            chain=SimpleNamespace(owner_of=lambda shop: OWNER))
    OwnerFunds(Kit(), payer).bring([Allocation("Base_Sepolia", Decimal(1))])
    assert seen == [(OWNER, AGENT)]


# ---- the agent's tools


class Funds:
    """The owner's Unified Balance as the pilot would have it: 320 on Base Sepolia (agent
    authorized), 50 on Arbitrum Sepolia (not yet)."""

    def __init__(self):
        self.brought = []

    def chains(self):
        return [ChainFunds("Base_Sepolia", Decimal(320), Decimal(0), "ready"),
                ChainFunds("Arbitrum_Sepolia", Decimal(50), Decimal(0), "none"),
                ChainFunds("Unichain_Sepolia", Decimal(0), Decimal(0), "none")]

    def estimate(self, allocations):
        return {"fees": FEES}

    def bring(self, allocations):
        self.brought.append(allocations)
        return {"txHash": "0x" + "2" * 64, "fees": FEES}


def box(journal, owner_funds, *, can_pay=True, money=None):
    d = Decision(invoice=INVOICE, supplier="Adeyemi Foods Distribution Ltd",
                 amount=Decimal(476), due_date=None, action=Action.PAY, reasons=("r",),
                 findings=())
    return Toolbox(decisions=[d], ledger=None, journal=journal,
                   policy=PolicyConfig(max_per_payment=Decimal(500), weekly_budget=Decimal(2000)),
                   now=NOW, zone=UTC, owner_funds=owner_funds, can_pay=can_pay,
                   funds=lambda: money)


def test_cash_position_shows_the_owners_other_chains_and_where_the_agent_may_spend(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        seen = json.loads(box(journal, Funds()).call("cash_position", {}))
    assert seen["usdc_on_other_chains"] == [
        {"chain": "Base_Sepolia", "available": "320", "arriving": "0", "you_may_spend_it": "yes"},
        {"chain": "Arbitrum_Sepolia", "available": "50", "arriving": "0",
         "you_may_spend_it": "no: the owner authorizes you on this chain in the dashboard"}]


def test_without_a_unified_balance_the_agent_is_told_so(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        seen = json.loads(box(journal, None).call("cash_position", {}))
        with pytest.raises(ToolError, match="not connected"):
            box(journal, None).call("bring_funds", {"allocations": "Base_Sepolia:1",
                                                    "reason": "r"})
    assert seen["usdc_on_other_chains"] == "not connected for this shop"


def test_the_agent_brings_what_it_chose_and_sees_fees_and_what_arrived(tmp_path):
    funds = Funds()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        tools = box(journal, funds)
        estimate = json.loads(tools.call("estimate_bring_funds",
                                         {"allocations": "Base_Sepolia: 310.05"}))
        moved = json.loads(tools.call("bring_funds", {
            "allocations": "Base_Sepolia:310.05",
            "reason": "00017 needs 476 and the wallet on Arc holds 165.95."}))
    assert funds.brought == [[Allocation("Base_Sepolia", Decimal("310.05"))]]
    assert estimate["arrives_on_arc"] == "310.035838"  # the forwarder's fee comes off the mint
    assert moved["from"] == [{"chain": "Base_Sepolia", "amount": "310.05"}]
    assert moved["fees"] == {"provider": "0.0155", "gasFee": "0.048274", "forwarder": "0.014162"}
    assert moved["tx"] == "0x" + "2" * 64
    assert tools.funds_moved == [moved]


@pytest.mark.parametrize("allocations", [
    "", "Base_Sepolia", "Base_Sepolia:0", "Base_Sepolia:-5", "Base_Sepolia:ten",
    "Base_Sepolia:1, Base_Sepolia:2"])
def test_a_split_must_be_chain_amount_pairs(tmp_path, allocations):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError):
            box(journal, Funds()).call("bring_funds", {"allocations": allocations, "reason": "r"})


def test_nothing_moves_while_the_owner_paused_payments(tmp_path):
    funds = Funds()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="paused"):
            box(journal, funds, can_pay=False).call(
                "bring_funds", {"allocations": "Base_Sepolia:10", "reason": "r"})
    assert funds.brought == []


def test_the_kits_refusal_reaches_the_agent_word_for_word(tmp_path):
    class Refuses(Funds):
        def bring(self, allocations):
            raise UnifiedBalanceError("Insufficient balance on Arbitrum_Sepolia")

    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="nothing was brought: Insufficient balance"):
            box(journal, Refuses()).call("bring_funds", {"allocations": "Arbitrum_Sepolia:50",
                                                         "reason": "r"})


# ---- what it did is told, and does not wake it


def test_after_bringing_funds_the_money_is_read_again_so_it_does_not_wake_the_agent(tmp_path):
    money = {"owner_balance": "165.945618", "available_to_pay": "165.945618"}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        tools = box(journal, Funds(), money=money)
        tools.call("bring_funds", {"allocations": "Base_Sepolia:310.05", "reason": "r"})
    after = {"owner_balance": "475.981456", "available_to_pay": "475.981456"}
    setup = BrainSetup(brain=None, zone=UTC, funds=lambda: after)
    assert money_after(setup, tools, money) == after


def test_the_activity_says_what_reached_the_owners_wallet_and_from_where():
    moved = {"from": [{"chain": "Base_Sepolia", "amount": "300"},
                      {"chain": "Arbitrum_Sepolia", "amount": "10.05"}],
             "total": "310.05", "fees": {}, "arrives_on_arc": "310.035838", "reason": "r",
             "tx": "0x" + "2" * 64}
    session = Session(at=NOW, wakes=(Wake("round", "r"),), finished=True, funds_moved=(moved,))
    [line] = [e for e in activity.session_events(session) if e.get("tx")]
    assert line["text"] == ("Brought 310.035838 USDC to your wallet on Arc from your Unified "
                            "Balance (Base Sepolia 300, Arbitrum Sepolia 10.05).")


def test_a_session_keeps_what_it_moved_across_a_restart(tmp_path):
    moved = {"from": [{"chain": "Base_Sepolia", "amount": "1"}], "total": "1", "fees": {},
             "arrives_on_arc": "0.98", "reason": "r", "tx": "0xabc"}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        journal.record_session(Session(at=NOW, wakes=(Wake("round", "r"),), finished=True,
                                       funds_moved=(moved,)))
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        assert journal.sessions()[-1].funds_moved == (moved,)


def test_the_dashboard_gets_each_chain_or_the_reason_it_could_not_read_them():
    assert unified_view(None) is None
    view = unified_view(Funds())
    assert view["chains"][0] == {"chain": "Base_Sepolia", "available": "320",
                                 "arriving": "0", "agent_authorized": True}

    class Down(Funds):
        def chains(self):
            raise UnifiedBalanceError("the Unified Balance Kit did not run")

    assert unified_view(Down()) == {"error": "the Unified Balance Kit did not run"}

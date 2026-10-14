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
from services.unified_balance.client import ChainFacts
from services.unified_balance.funding import OwnerWallets, WalletFunds

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
OWNER = "0x2471658CD7A7B10d4C92113B0777dA59317e839a"
AGENT = "0xeAc91C9491F0D391c80f17C4267A3329Ad7Ba862"
KEY, SECRET = "fake-circle-key", "fake-entity-secret"
INVOICE = "ACC-PINV-2026-00017"
FUNDING = "0x" + "f1" * 20
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
        def prepare(self, wallet):
            return []

        def spend(self, owner, agent, allocations):
            seen.append((owner, agent))
            return {}

    payer = SimpleNamespace(shop="0x" + "5c" * 20, agent_address=lambda: AGENT,
                            chain=SimpleNamespace(owner_of=lambda shop: OWNER))
    OwnerFunds(Kit(), payer).bring([Allocation("Base_Sepolia", Decimal(1))])
    assert seen == [(OWNER, AGENT)]


def test_a_gasless_agent_has_its_spends_signed_by_its_signer():
    seen = []

    class Kit:
        def spend(self, owner, signer, allocations):
            seen.append(("spend", signer))
            return {}

        def balances(self, owner, signer):
            seen.append(("balances", signer))
            return []

        def prepare(self, wallet):
            seen.append(("prepare", wallet))
            return ["Base_Sepolia"]

    signer = "0x" + "e0" * 20
    payer = SimpleNamespace(shop="0x" + "5c" * 20, agent_address=lambda: AGENT,
                            chain=SimpleNamespace(owner_of=lambda shop: OWNER))
    funds = OwnerFunds(Kit(), payer, signer=signer)
    assert funds.prepare() == ["Base_Sepolia"]
    funds.bring([Allocation("Base_Sepolia", Decimal(1))])
    funds.chains()
    # the signer signs spends; both wallets exist on every chain (the agent deposits there)
    assert seen == [("prepare", signer), ("prepare", AGENT), ("spend", signer),
                    ("balances", signer)]


def test_wallets_not_prepared_at_start_are_prepared_before_the_first_move():
    seen = []

    class Kit:
        def prepare(self, wallet):
            seen.append("prepare")
            if len(seen) <= 2:  # at start and on the first retry
                raise UnifiedBalanceError("API rate limit error")
            return ["Base_Sepolia"]

        def spend(self, owner, signer, allocations):
            seen.append("spend")
            return {}

    payer = SimpleNamespace(shop="0x" + "5c" * 20, agent_address=lambda: AGENT,
                            chain=SimpleNamespace(owner_of=lambda shop: OWNER))
    funds = OwnerFunds(Kit(), payer)
    with pytest.raises(UnifiedBalanceError):
        funds.prepare()  # at start, while every shop restarts
    funds.bring([Allocation("Base_Sepolia", Decimal(1))])
    funds.bring([Allocation("Base_Sepolia", Decimal(1))])
    # a failed retry does not stop the move; the next move tries again, then never more
    assert seen == ["prepare", "prepare", "spend", "prepare", "spend"]
    funds.bring([Allocation("Base_Sepolia", Decimal(1))])
    assert seen[-1] == "spend"


# ---- the agent's tools


class Funds:
    """The owner's Unified Balance as the pilot would have it: 320 on Base Sepolia (agent
    authorized), 50 on Arbitrum Sepolia (not yet)."""

    def __init__(self):
        self.brought, self.deposited = [], []

    def chains(self):
        return [ChainFunds("Base_Sepolia", Decimal(320), Decimal(0), "ready"),
                ChainFunds("Arbitrum_Sepolia", Decimal(50), Decimal(0), "none"),
                ChainFunds("Unichain_Sepolia", Decimal(0), Decimal(0), "none")]

    def estimate(self, allocations):
        return {"fees": FEES}

    def bring(self, allocations):
        self.brought.append(allocations)
        return {"txHash": "0x" + "2" * 64, "fees": FEES}

    def in_wallets(self):
        return [WalletFunds("Base_Sepolia", Decimal(400), FUNDING, Decimal(1000), Decimal(200),
                            Decimal(150), False, True),
                WalletFunds("Ethereum_Sepolia", Decimal(80), None)]

    def deposit(self, chain, amount):
        self.deposited.append((chain, amount))
        return {"chain": chain, "amount": str(amount), "state": "complete",
                "tx": "0x" + "3" * 64, "circle_tx": "c1"}


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
    assert moved["from"] == [{"chain": "Base_Sepolia", "amount": "310.05",
                              "taken_from_balance": "310.05"}]  # the fees in FEES name no chain
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


# ---- the owner's own wallets on other chains


def test_cash_position_shows_what_the_agent_may_deposit_on_each_chain(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        seen = json.loads(box(journal, Funds()).call("cash_position", {}))
    assert seen["usdc_in_owner_wallets_on_other_chains"] == [
        {"chain": "Base_Sepolia", "in_wallet": "400", "you_may_deposit_now": "150"},
        {"chain": "Ethereum_Sepolia", "in_wallet": "80",
         "you_may_deposit_now": "no: the owner sets up this chain in the dashboard"}]


def test_the_agent_deposits_what_it_chose_and_it_is_told(tmp_path):
    funds = Funds()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        tools = box(journal, funds)
        moved = json.loads(tools.call("deposit_to_gateway", {
            "chain": "Base_Sepolia", "amount": "120.5", "reason": "00017 is due in 10 days."}))
    assert funds.deposited == [("Base_Sepolia", Decimal("120.5"))]
    assert moved["state"] == "complete" and moved["kind"] == "deposit"
    session = Session(at=NOW, wakes=(Wake("round", "r"),), finished=True, funds_moved=(moved,))
    [line] = [e for e in activity.session_events(session) if e.get("tx")]
    assert line["text"] == ("Moved 120.5 USDC from your wallet on Base Sepolia into your "
                            "Unified Balance.")


@pytest.mark.parametrize("amount", ["0", "-1", "ten", ""])
def test_a_deposit_needs_an_amount_above_zero(tmp_path, amount):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="above 0"):
            box(journal, Funds()).call("deposit_to_gateway",
                                       {"chain": "Base_Sepolia", "amount": amount, "reason": "r"})


def test_no_deposit_while_payments_are_paused(tmp_path):
    funds = Funds()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        with pytest.raises(ToolError, match="paused"):
            box(journal, funds, can_pay=False).call(
                "deposit_to_gateway", {"chain": "Base_Sepolia", "amount": "10", "reason": "r"})
    assert funds.deposited == []


def test_what_the_agent_may_move_is_the_smallest_of_balance_approval_and_limits():
    w = WalletFunds("Base_Sepolia", Decimal(400), FUNDING, Decimal(1000), Decimal(200),
                    Decimal(150), False, True)
    assert w.can_move == Decimal(150)
    assert WalletFunds("Base_Sepolia", Decimal(400), FUNDING, Decimal(1000), Decimal(200),
                       Decimal(150), True, True).can_move == 0  # paused
    assert WalletFunds("Base_Sepolia", Decimal(400), FUNDING, Decimal(1000), Decimal(200),
                       Decimal(150), False, False).can_move == 0  # another agent
    assert WalletFunds("Base_Sepolia", Decimal(400), None).can_move == 0


def test_a_chain_without_its_factory_is_not_offered():
    facts = lambda: [ChainFacts("Base_Sepolia", "https://node", "0x" + "11" * 20, "BASE-SEPOLIA")]  # noqa: E731
    wallets = OwnerWallets(circle=None, facts=facts, factories={"Base_Sepolia": ""})
    assert wallets.read(OWNER, AGENT) == []
    with pytest.raises(Exception, match="no ShopFunding factory on Base_Sepolia"):
        wallets.deposit(OWNER, AGENT, "Base_Sepolia", Decimal(1))


def test_a_chain_gives_up_its_amount_plus_its_own_gas_and_protocol_fee(tmp_path):
    # the 14-Oct estimate for 11 USDC from Ethereum Sepolia: about 1.10 of gas on top of the 11
    fees = [{"type": "provider", "amount": "0.00055",
             "allocations": [{"chain": "Ethereum_Sepolia", "amount": "0.00055"}]},
            {"type": "gasFee", "amount": "1.09945",
             "allocations": [{"chain": "Ethereum_Sepolia", "amount": "1.09945"}]},
            {"type": "forwarder", "amount": "0.017439"}]

    class Estimates(Funds):
        def estimate(self, allocations):
            return {"fees": fees}

    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        seen = json.loads(box(journal, Estimates()).call(
            "estimate_bring_funds", {"allocations": "Ethereum_Sepolia:11"}))
    assert seen["from"] == [{"chain": "Ethereum_Sepolia", "amount": "11",
                             "taken_from_balance": "12.1"}]
    assert seen["arrives_on_arc"] == "10.982561"

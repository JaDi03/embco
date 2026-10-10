"""The agent sees when the weekly room really resets, the owner's balance and the authorization,
read from the chain, instead of guessing them."""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.tools import Toolbox, ToolError
from agent.wiring import funds_of
from services.payments import ArcRpc, ChainError

SHOP = "0x" + "5c" * 20
OWNER = "0x" + "0a" * 20
USDC_CONTRACT = "0x3600000000000000000000000000000000000000"
STARTED = int(datetime(2026, 10, 7, 2, 25, 38, tzinfo=UTC).timestamp())


def node(answers):
    """An Arc node that answers eth_call by function selector."""
    def handle(request):
        body = json.loads(request.content)
        selector = bytes.fromhex(body["params"][0]["data"][2:10])
        types, values = answers[selector]
        result = "0x" + encode(types, values).hex()
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
    transport = httpx.MockTransport(handle)
    return ArcRpc("https://node.example/rpc", client=httpx.Client(transport=transport))


def sel(signature):
    return keccak(text=signature)[:4]


def contract(week, balance, allowed):
    return node({
        sel("startedAt()"): (["uint256"], [STARTED]),
        sel("currentWeek()"): (["uint256"], [week]),
        sel("owner()"): (["address"], [OWNER]),
        sel("usdc()"): (["address"], [USDC_CONTRACT]),
        sel("balanceOf(address)"): (["uint256"], [balance]),
        sel("allowance(address,address)"): (["uint256"], [allowed]),
        sel("remainingThisWeek()"): (["uint256"], [343_500_000]),
    })


def test_the_week_resets_seven_days_after_the_contract_was_created_not_on_monday():
    rpc = contract(week=0, balance=0, allowed=0)
    assert rpc.week_resets_at(SHOP) == datetime(2026, 10, 14, 2, 25, 38, tzinfo=UTC)
    assert contract(week=1, balance=0, allowed=0).week_resets_at(SHOP) == datetime(
        2026, 10, 21, 2, 25, 38, tzinfo=UTC)


def test_the_owner_balance_and_authorization_are_read_from_usdc():
    assert contract(0, 383_445_034, 181_500_000).owner_funds(SHOP) == (383_445_034, 181_500_000)


def funds(rpc):
    return funds_of(SimpleNamespace(chain=rpc, shop=to_checksum_address(SHOP)))()


def test_the_agent_reads_plain_facts_it_can_act_on():
    seen = funds(contract(0, 383_445_034, 2**256 - 1))
    assert seen == {"weekly_room_resets_at_utc": "2026-10-14 02:25",
                    "owner_balance": "383.445034", "payments_authorized": "yes"}
    assert "authorize again" in funds(contract(0, 1, 181_500_000))["payments_authorized"]
    assert funds(contract(0, 1, 0))["payments_authorized"] == "no"


def test_an_unreachable_node_gives_unknown_instead_of_a_guess(tmp_path):
    class Down:
        def week_resets_at(self, shop):
            raise ChainError("node down")

    assert funds(Down()) is None
    d = Decision(invoice="PINV-1", supplier="S", amount=Decimal(10), due_date=None,
                 action=Action.PAY, reasons=("r",), findings=())
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        policy = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))
        box = Toolbox(decisions=[d], ledger=None, journal=journal, policy=policy,
                      now=datetime(2026, 10, 10, tzinfo=UTC), zone=UTC,
                      funds=lambda: funds(Down()))
        seen = json.loads(box.call("cash_position", {}))
    assert seen["weekly_room_resets_at_utc"] == "unknown"
    assert seen["payments_authorized"] == "unknown"


def schedule(box, pay_on):
    return box.call("schedule_payment", {"invoice": "PINV-1", "pay_on": pay_on,
                                         "reason": "no room this week"})


def big_invoice_box(journal, funds):  # noqa: F811
    d = Decision(invoice="PINV-1", supplier="S", amount=Decimal(420), due_date=None,
                 action=Action.PAY, reasons=("r",), findings=())
    policy = PolicyConfig(max_per_payment=Decimal(420), weekly_budget=Decimal(2000))
    lagos = timezone(timedelta(hours=1))
    return Toolbox(decisions=[d], ledger=None, journal=journal, policy=policy,
                   now=datetime(2026, 10, 10, 1, 12, tzinfo=UTC), zone=lagos,
                   room=lambda: Decimal("343.5"), funds=funds)


def test_a_payment_that_does_not_fit_cannot_be_scheduled_before_the_real_renewal(tmp_path):
    seen = {"weekly_room_resets_at_utc": "2026-10-14 02:25", "owner_balance": "383.45",
            "payments_authorized": "yes"}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        box = big_invoice_box(journal, lambda: seen)
        with pytest.raises(ToolError, match="renews on 2026-10-14"):
            schedule(box, "2026-10-12")  # what Claude assumed: a calendar week
        assert "scheduled for 2026-10-14" in schedule(box, "2026-10-14")


def test_without_the_renewal_date_a_payment_that_does_not_fit_is_not_scheduled(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        box = big_invoice_box(journal, lambda: None)
        with pytest.raises(ToolError, match="cannot be read right now"):
            schedule(box, "2026-10-20")

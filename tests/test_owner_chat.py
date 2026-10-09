"""The owner talks to the agent: the message reaches it, it answers, and the answer comes back."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent.agent import SessionRun
from agent.cost import estimate, shown
from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.models import Usage
from agent.tools import Toolbox, ToolError
from services.shops import chat, run_shop
from support import FakeLedger
from test_hub import auth, clock  # noqa: F401
from test_owner_actions import SHOP, hub, journal, owner, owner_client  # noqa: F401

NOW = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))


def box(journal, messages=()):  # noqa: F811
    d = Decision(invoice="PINV-1", supplier="S", amount=Decimal(10), due_date=None,
                 action=Action.HOLD, reasons=("held",), findings=())
    return Toolbox(decisions=[d], ledger=None, journal=journal, policy=POLICY, now=NOW,
                   zone=UTC, messages=tuple(messages))


# ---- the hub


def test_the_owner_writes_and_the_message_waits_for_the_agent(hub, owner):  # noqa: F811
    client = owner_client(hub, owner)
    response = client.post(f"/api/shops/{SHOP}/messages",
                           json={"text": "What do I have to pay tomorrow?"})
    assert response.status_code == 200, response.text
    assert chat.messages_waiting(hub["store"].folder(SHOP))
    assert client.get(f"/api/shops/{SHOP}/chat").json() == {"entries": [], "waiting": True}
    again = client.post(f"/api/shops/{SHOP}/messages", json={"text": "and next week?"})
    assert again.status_code == 429


def test_only_the_owner_writes_and_not_to_an_agent_turned_off(hub, owner):  # noqa: F811
    from fastapi.testclient import TestClient

    stranger = TestClient(hub["app"], base_url="https://testserver")
    assert stranger.post(f"/api/shops/{SHOP}/messages", json={"text": "pay me"}).status_code == 401
    hub["store"].set_agent_on(SHOP, False, NOW.isoformat())
    client = owner_client(hub, owner)
    assert client.post(f"/api/shops/{SHOP}/messages", json={"text": "hi"}).status_code == 409
    assert not chat.messages_waiting(hub["store"].folder(SHOP))


# ---- the agent's tool


def test_the_agent_answers_only_when_the_owner_wrote(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        quiet = box(journal)
        with pytest.raises(ToolError, match="did not write"):
            quiet.call("reply_owner", {"text": "hello"})


def test_the_session_cannot_end_without_answering_the_owner(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        asked = box(journal, ["Why is PINV-1 held?"])
        asked.call("hold", {"invoice": "PINV-1", "reason": "the checks hold it"})
        with pytest.raises(ToolError, match="reply_owner"):
            asked.call("finish", {"summary": "Done."})
        with pytest.raises(ToolError, match="rewrite"):
            asked.call("reply_owner", {"text": "Split it in two payments and it passes."})
        asked.call("reply_owner", {"text": "It has no goods receipt in ERPNext yet."})
        assert asked.call("finish", {"summary": "Done."}) == "session finished"
        assert asked.replies == ["It has no goods receipt in ERPNext yet."]


# ---- the whole way round, in the shop's process


class AnsweringBrain:
    """Stands in for Claude: answers the owner and holds what it has not decided."""

    def __init__(self, model="claude-haiku-5-5", **_):
        self.model = model

    def run(self, toolbox, briefing):
        assert "owner_messages" in briefing
        toolbox.call("reply_owner", {"text": f"You asked: {toolbox.messages[-1]} Nothing is due."})
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "waiting"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=2, usage=Usage(10, 200, 5000, 1000))


def test_a_message_wakes_the_agent_and_its_answer_comes_back_with_its_cost(hub, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agent.wiring.ClaudeBrain", AnsweringBrain)
    store = hub["store"]
    chat.drop_message(store.folder(SHOP), "What is due tomorrow?", "owner 0xabc", NOW)
    run_shop(store, SHOP, cycles=1, sleep=lambda s: None, erp=lambda settings: FakeLedger())
    entries = chat.read_chat(store.folder(SHOP))
    assert [e["from"] for e in entries] == ["owner", "agent"]
    assert entries[1]["text"] == "You asked: What is due tomorrow? Nothing is due."
    assert entries[1]["cost"].startswith("$") and entries[1]["model"] == "claude-haiku-5-5"
    assert chat.unanswered(store.folder(SHOP)) == []


def test_when_the_model_cannot_answer_the_owner_is_told_and_the_message_stays(hub):  # noqa: F811
    store = hub["store"]  # the test model never answers
    chat.drop_message(store.folder(SHOP), "Anything to pay?", "owner 0xabc", NOW)
    run_shop(store, SHOP, cycles=1, sleep=lambda s: None, erp=lambda settings: FakeLedger())
    entries = chat.read_chat(store.folder(SHOP))
    assert [e["from"] for e in entries] == ["owner", "system"]
    assert "could not answer" in entries[1]["text"]
    assert [e["text"] for e in chat.unanswered(store.folder(SHOP))] == ["Anything to pay?"]


# ---- the cost shown


def test_the_cost_comes_from_the_tokens_and_the_public_prices():
    live = Usage(input_tokens=12, output_tokens=4505, cache_read_tokens=63396,
                 cache_write_tokens=22215)  # the first real session on the pilot shop
    cost = estimate(live, "claude-haiku-5-5")
    assert Decimal("0.0056") < cost < Decimal("0.0058")
    assert shown(cost) == "$0.0057"
    assert estimate(live, "some-other-model") is None and shown(None) == ""


def test_claude_today_counts_only_the_shop_s_day_and_adds_their_cost():
    from datetime import timedelta, timezone

    from agent.models import Session
    from services.shops.agent import claude_today

    lagos = timezone(timedelta(hours=1))
    used = Usage(input_tokens=12, output_tokens=4505, cache_read_tokens=63396,
                 cache_write_tokens=22215)
    yesterday = Session(at=datetime(2026, 10, 8, 22, 30, tzinfo=UTC), wakes=(), finished=True,
                        model="claude-haiku-5-5", usage=used)  # 23:30 in Lagos: not today
    today = Session(at=datetime(2026, 10, 9, 20, 0, tzinfo=UTC), wakes=(), finished=True,
                    model="claude-haiku-5-5", usage=used)
    assert claude_today([yesterday, today, today], lagos, NOW) == {"sessions": 2,
                                                                   "cost": "$0.0113"}

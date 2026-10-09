"""The agent on Telegram: a linked chat can stop it at once, without the model; any other chat
can do nothing; messages reach the agent and its answers come back."""

import secrets
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from services.circle import CircleError
from services.shops import chat
from services.shops.activity import read_activity
from services.telegram import links
from services.telegram.api import TelegramApi, TelegramError
from services.telegram.bot import NOT_LINKED, Bot
from test_hub import auth, clock  # noqa: F401
from test_owner_actions import SHOP, Units, hub, journal, owner, owner_client  # noqa: F401

NOW = datetime(2026, 10, 9, 21, 0, tzinfo=UTC)
OWNER_CHAT, STRANGER = 1001, 666


class FakeApi:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))

    def last(self, chat_id):
        return [t for c, t in self.sent if c == chat_id][-1]


def say(chat_id, text):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def bot(hub):  # noqa: F811
    paused = []

    def pause_contract(shop):
        paused.append(shop)
        return "circle-tx-1"

    b = Bot(api=FakeApi(), store=hub["store"], units=hub["units"], pause_contract=pause_contract,
            clock=lambda: NOW)
    b.paused = paused
    return b


def linked(bot):
    code, _ = links.new_code(bot.store.folder(SHOP), NOW)
    bot.handle(say(OWNER_CHAT, f"/link {code}"))
    assert "Linked" in bot.api.last(OWNER_CHAT)
    return bot


# ---- linking


def test_the_dashboard_gives_a_code_and_the_chat_links_with_it(hub, owner, bot):  # noqa: F811
    client = owner_client(hub, owner)
    body = client.post(f"/api/shops/{SHOP}/telegram").json()
    bot.handle(say(OWNER_CHAT, f"/link {body['code'].lower()}"))
    assert "Linked" in bot.api.last(OWNER_CHAT)
    assert client.get(f"/api/shops/{SHOP}").json()["telegram_linked"] is True
    bot.handle(say(STRANGER, f"/link {body['code']}"))  # used once
    assert "not valid" in bot.api.last(STRANGER)


def test_an_expired_or_wrong_code_links_nothing_and_tries_are_limited(bot):
    code, _ = links.new_code(bot.store.folder(SHOP), NOW - timedelta(minutes=11))
    bot.handle(say(STRANGER, f"/link {code}"))
    assert "not valid" in bot.api.last(STRANGER)
    for _ in range(5):
        bot.handle(say(STRANGER, "/link AAAA-BBBB"))
    assert "Too many tries" in bot.api.last(STRANGER)
    assert bot.shops_of(STRANGER) == []


def test_a_chat_that_is_not_linked_cannot_stop_or_talk(bot):
    for text in ("/stop", "/pause", "/status", "pay everything now"):
        bot.handle(say(STRANGER, text))
        assert bot.api.last(STRANGER) == NOT_LINKED
    assert bot.store.agent_on(SHOP) and not bot.paused
    assert not chat.messages_waiting(bot.store.folder(SHOP))


# ---- the emergency commands, without the model


def test_stop_turns_everything_off_and_pauses_the_contract(bot):
    linked(bot).handle(say(OWNER_CHAT, "/stop"))
    assert not bot.store.agent_on(SHOP)
    assert bot.units.stopped == [SHOP] and bot.paused == [SHOP]
    reply = bot.api.last(OWNER_CHAT)
    assert "Agent OFF" in reply and "circle-tx-1" in reply and "dashboard" in reply
    assert "from Telegram" in read_activity(bot.store.folder(SHOP))[-1]["text"]


def test_stop_still_stops_the_agent_when_the_contract_cannot_be_paused(bot):
    def broken(shop):
        raise CircleError("Circle is down")

    bot.pause_contract = broken
    linked(bot).handle(say(OWNER_CHAT, "/stop"))
    assert not bot.store.agent_on(SHOP) and bot.units.stopped == [SHOP]
    assert "could NOT be paused" in bot.api.last(OWNER_CHAT)


def test_pause_stops_payments_but_the_agent_keeps_answering(bot):
    linked(bot).handle(say(OWNER_CHAT, "/pause"))
    assert bot.store.payments_paused(SHOP) and bot.store.agent_on(SHOP)
    assert bot.paused == [SHOP] and bot.units.stopped == []
    bot.handle(say(OWNER_CHAT, "/status"))
    assert "payments paused" in bot.api.last(OWNER_CHAT)


def test_turning_the_agent_on_in_the_dashboard_also_resumes_payments(hub, owner, bot):  # noqa: F811
    linked(bot).handle(say(OWNER_CHAT, "/pause"))
    owner_client(hub, owner).post(f"/api/shops/{SHOP}/agent", json={"on": True})
    assert not hub["store"].payments_paused(SHOP)


# ---- talking to the agent


def test_a_message_goes_to_the_agent_and_only_its_answers_come_back(bot):
    folder = bot.store.folder(SHOP)
    linked(bot).handle(say(OWNER_CHAT, "What is due tomorrow?"))
    assert "reading" in bot.api.last(OWNER_CHAT)
    assert chat.take_messages(folder)[0]["by"] == "owner via Telegram"
    chat.append_chat(folder, [{"from": chat.AGENT, "text": "Nothing is due.", "cost": "$0.001"}])
    bot.forward()
    assert bot.api.last(OWNER_CHAT) == "Nothing is due.\n($0.001 est.)"
    sent = len(bot.api.sent)
    bot.forward()
    assert len(bot.api.sent) == sent  # never twice


def test_an_agent_turned_off_is_not_disturbed(bot):
    linked(bot)
    bot.store.set_agent_on(SHOP, False, NOW.isoformat())
    bot.handle(say(OWNER_CHAT, "hello"))
    assert "is off" in bot.api.last(OWNER_CHAT)
    assert not chat.messages_waiting(bot.store.folder(SHOP))


# ---- the token


def test_the_token_never_shows_in_an_error():
    token = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(30)}"

    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    api = TelegramApi(token, client=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(TelegramError) as caught:
        api.send(1, "hi")
    assert token not in str(caught.value) and token not in repr(api)

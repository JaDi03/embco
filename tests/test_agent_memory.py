"""The agent keeps what it learns in a few memory files, through Claude's memory tool: owner.md
changes only when the owner wrote, no wallet is remembered, and only a finished session's
memory is kept."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agent.agent import ClaudeBrain, SessionRun
from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.memory.files import MEMORY_TOOL, ShopMemory
from agent.models import Usage
from agent.prompt import briefing
from agent.tools import Toolbox, ToolError
from services.shops import chat, run_shop
from support import FakeLedger
from test_hub import auth, clock  # noqa: F401
from test_owner_actions import SHOP, hub, journal, owner  # noqa: F401

NOW = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))
OWNER = "/memories/owner.md"
WISH = "# What the owner wants\n- Ikeja Fresh Produce: never pay more than 500 USDC at once.\n"


def box(journal, memory, messages=()):  # noqa: F811
    d = Decision(invoice="PINV-1", supplier="S", amount=Decimal(10), due_date=None,
                 action=Action.HOLD, reasons=("held",), findings=())
    return Toolbox(decisions=[d], ledger=None, journal=journal, policy=POLICY, now=NOW, zone=UTC,
                   messages=tuple(messages), memory=memory)


def create(path, text):
    return {"command": "create", "path": path, "file_text": text}


def test_owner_md_changes_only_in_a_session_where_the_owner_wrote(tmp_path):
    memory = ShopMemory(tmp_path)
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        quiet = box(journal, memory.session(owner_wrote=False))
        with pytest.raises(ToolError, match="only in a session where the owner wrote"):
            quiet.call("memory", create(OWNER, WISH))  # an invoice cannot become the owner's wish
        quiet.call("memory", create("/memories/Ikeja Fresh Produce Ltd.md",
                                    "# Ikeja Fresh Produce Ltd\n- Bills the day it delivers.\n"))
        quiet.memory.commit()
        told = box(journal, memory.session(owner_wrote=True), ["Never pay Ikeja more than 500."])
        told.call("memory", create(OWNER, WISH))
        told.memory.commit()
    assert (tmp_path / "memories" / "owner.md").read_text(encoding="utf-8") == WISH
    assert [s["tool"] for s in told.trace] == ["memory"]
    assert told.trace[0]["args"] == {"command": "create", "path": OWNER}  # text: in the file


def test_memory_refuses_wallets_folders_and_sprawl(tmp_path):
    memory = ShopMemory(tmp_path)
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        agent = box(journal, memory.session(owner_wrote=True), ["hi"])
        wallet = "0x4Ed878DEa2293Ea7412BC5fdc0A43887d835f154"
        with pytest.raises(ToolError, match="wallet address"):
            agent.call("memory", create("/memories/shop.md", f"# Shop\n- Ikeja pays to {wallet}\n"))
        for path in ("/memories/suppliers/Ikeja.md", "/memories/../journal.sqlite3",
                     "/memories/notes.txt"):
            with pytest.raises(ToolError, match="flat files"):
                agent.call("memory", create(path, "# x\n"))
        with pytest.raises(ToolError, match="at most 3000 characters"):
            agent.call("memory", create("/memories/shop.md", "# Shop\n" + "x" * 3000))
        agent.call("memory", create("/memories/shop.md", "# Shop\n- Closed on Sundays.\n"))
        with pytest.raises(ToolError, match="missing an input"):
            agent.call("memory", {"command": "create", "path": "/memories/x.md"})
        seen = agent.call("memory", {"command": "view", "path": "/memories/shop.md"})
        assert "Closed on Sundays." in seen
        with pytest.raises(ToolError, match="escape"):  # the SDK's own path check
            agent.call("memory", {"command": "view", "path": "/memories/../j.sqlite3"})


def test_only_a_finished_session_keeps_its_memory(tmp_path):
    memory = ShopMemory(tmp_path)
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        cut = box(journal, memory.session(owner_wrote=True), ["hi"])
        cut.call("memory", create(OWNER, WISH))
        cut.memory.discard()  # the session was cut off: nothing it wrote stays
        assert not (tmp_path / "memories" / "owner.md").exists()
        assert list(tmp_path.glob(".memory-*")) == []
        later = box(journal, memory.session(owner_wrote=False))
        assert later.memory.brief() == {"owner.md": "", "files": []}


def test_the_briefing_shows_owner_md_in_full_and_what_the_other_files_hold(tmp_path):
    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "owner.md").write_text(WISH, encoding="utf-8")
    about = "Lekki Oils: bills before delivery"
    (tmp_path / "memories" / "Lekki Oils.md").write_text(f"# {about}\n- x\n", encoding="utf-8")
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        toolbox = box(journal, ShopMemory(tmp_path).session(owner_wrote=False))
        data = json.loads(briefing(toolbox, [], [], []).split("\n\n", 1)[1])
    assert data["your_memory"] == {
        "owner.md": WISH,
        "files": [{"file": "/memories/Lekki Oils.md", "about": about}]}


class FakeClient:
    """Stands in for the Anthropic client: the model keeps the owner's wish, then finishes."""

    def __init__(self):
        self.tools = []
        self.messages = SimpleNamespace(create=self.create)
        self.turn = 0

    def create(self, **request):
        self.tools.append([t["name"] for t in request["tools"]])
        self.turn += 1
        calls = ([("memory", create(OWNER, WISH))] if self.turn == 1
                 else [("hold", {"invoice": "PINV-1", "reason": "waiting"}),
                       ("reply_owner", {"text": "Noted: never more than 500 to Ikeja."}),
                       ("finish", {"summary": "Noted."})])
        content = [SimpleNamespace(type="tool_use", id=f"t{self.turn}{i}", name=n, input=a)
                   for i, (n, a) in enumerate(calls)]
        return SimpleNamespace(stop_reason="tool_use", content=content,
                               usage=SimpleNamespace(input_tokens=10, output_tokens=10))


def test_the_model_gets_the_memory_tool_only_when_the_shop_has_memory(tmp_path):
    client = FakeClient()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:  # noqa: F811
        toolbox = box(journal, ShopMemory(tmp_path).session(owner_wrote=True),
                      ["Never pay Ikeja more than 500."])
        run = ClaudeBrain(client=client).run(toolbox, "briefing")
        assert run.finished and client.tools[0][-1] == MEMORY_TOOL["name"]
        assert toolbox.replies == ["Noted: never more than 500 to Ikeja."]
        plain = FakeClient()
        ClaudeBrain(client=plain).run(box(journal, None, ["hi"]), "briefing")
        assert "memory" not in plain.tools[0]


class RememberingBrain:
    """Stands in for Claude: keeps what the owner asked in owner.md, and records its briefings."""

    briefings: list[dict] = []

    def __init__(self, model="claude-haiku-5-5", **_):
        self.model = model

    def run(self, toolbox, text):
        data = json.loads(text.split("\n\n", 1)[1])
        RememberingBrain.briefings.append(data)
        if toolbox.messages:
            if not data["your_memory"]["owner.md"]:
                toolbox.call("memory", create(OWNER, WISH))
            toolbox.call("reply_owner", {"text": "Noted."})
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "waiting"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage(10, 200, 5000, 1000))


def test_what_the_owner_said_is_in_the_next_session_s_briefing(hub, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agent.wiring.ClaudeBrain", RememberingBrain)
    RememberingBrain.briefings = []
    store = hub["store"]
    chat.drop_message(store.folder(SHOP), "Never pay Ikeja more than 500 at once.", "owner 0xabc",
                      NOW)

    def nap(seconds):  # the owner writes again before the next check
        chat.drop_message(store.folder(SHOP), "Thanks.", "owner 0xabc", NOW)

    run_shop(store, SHOP, cycles=2, sleep=nap, erp=lambda settings: FakeLedger())
    assert RememberingBrain.briefings[0]["your_memory"]["owner.md"] == ""
    assert RememberingBrain.briefings[-1]["your_memory"]["owner.md"] == WISH
    assert list(store.folder(SHOP).glob(".memory-*")) == []

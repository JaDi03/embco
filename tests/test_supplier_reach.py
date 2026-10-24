"""An email added in the ERP for a supplier the agent is waiting on wakes the agent once."""

import json
from decimal import Decimal

from agent.agent import SessionRun
from agent.guardrails.rules import Action, Decision
from agent.models import Usage
from services.erp import LedgerError
from services.erp.models import Supplier
from services.shops import run_shop
from services.shops.supplier_reach import reach_now, reach_wakes, remember_reach
from support import WALLET_B, FakeLedger
from test_hub import auth, clock  # noqa: F401
from test_owner_actions import SHOP, hub, journal, owner  # noqa: F401


def decision(invoice, supplier, action):
    return Decision(invoice=invoice, supplier=supplier, amount=Decimal(10), due_date=None,
                    action=action, reasons=(), findings=())


def test_only_suppliers_the_checks_hold_or_ask_about_are_looked_up():
    asked = []

    def has_email(supplier):
        asked.append(supplier)
        if supplier == "Down":
            raise LedgerError("ERPNext did not answer")
        return supplier == "Ikeja"

    decisions = [decision("PINV-1", "Ikeja", Action.HOLD), decision("PINV-2", "Ikeja", Action.ASK),
                 decision("PINV-3", "Okogen", Action.PAY), decision("PINV-4", "Klen", Action.ASK),
                 decision("PINV-5", "Paid", Action.HOLD), decision("PINV-6", "Down", Action.HOLD)]
    reach = reach_now(decisions, {"PINV-5"}, has_email)
    assert reach == {"Ikeja": True, "Klen": False}  # Down left out: unknown is not "no email"
    assert asked == ["Down", "Ikeja", "Klen"]
    assert reach_now(None, set(), has_email) == {}  # no check yet since the shop started


def test_a_change_wakes_until_a_finished_session_saw_it(tmp_path):
    assert reach_wakes(tmp_path, {"Ikeja": False}) == []  # first look: remembered only
    assert reach_wakes(tmp_path, {"Ikeja": False}) == []
    wakes = reach_wakes(tmp_path, {"Ikeja": True, "Klen": False})
    assert [(w.kind, w.text) for w in wakes] == [("supplier", "Ikeja now has an email in the ERP.")]
    assert reach_wakes(tmp_path, {"Ikeja": True}) != []  # the session did not finish: again
    remember_reach(tmp_path, {"Ikeja": True})
    assert reach_wakes(tmp_path, {"Ikeja": True, "Klen": False}) == []
    lost = reach_wakes(tmp_path, {"Klen": True, "Ikeja": False})
    assert [w.text for w in lost] == ["Ikeja no longer has an email in the ERP.",
                                      "Klen now has an email in the ERP."]
    assert json.loads((tmp_path / "reach_seen.json").read_text()) == {"Ikeja": True,
                                                                     "Klen": False}


class ReachLedger(FakeLedger):
    """Supplier S has no proven wallet, so the checks hold its invoice; the owner adds its email
    in ERPNext between two checks."""

    def __init__(self):
        super().__init__()
        self.supplier = Supplier(name="S", wallet_address=WALLET_B)  # never paid, never signed
        self.payments = []
        self.email = False

    def has_email(self, supplier):
        return self.email


class RecordingBrain:
    """Stands in for Claude: holds what it has not decided and keeps why it was woken."""

    woke: list[list[str]] = []

    def __init__(self, model="claude-haiku-5-5", **_):
        self.model = model

    def run(self, toolbox, briefing):
        data = json.loads(briefing.split("\n\n", 1)[1])
        RecordingBrain.woke.append(data["why_you_woke"])
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "waiting for the supplier"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage(10, 200, 5000, 1000))


def test_the_shop_wakes_the_agent_when_the_owner_adds_the_email(hub, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agent.wiring.ClaudeBrain", RecordingBrain)
    RecordingBrain.woke = []
    ledger = ReachLedger()
    naps = []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 2:
            ledger.email = True  # the owner adds the email after the second check

    run_shop(hub["store"], SHOP, cycles=4, sleep=nap, erp=lambda settings: ledger)
    supplier_wakes = [[t for t in why if "email" in t] for why in RecordingBrain.woke]
    assert [w for w in supplier_wakes if w] == [["S now has an email in the ERP."]]
    assert json.loads((hub["store"].folder(SHOP) / "reach_seen.json").read_text()) == {"S": True}

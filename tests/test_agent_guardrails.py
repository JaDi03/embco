"""The agent cannot pay what the rules do not allow, whatever the model asks for.

A scripted brain stands in for Claude and tries everything: paying an invoice on HOLD, one on
ASK, one over the week's room, and one the rules allow. Only the last one reaches the plan.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from agent.agent import SessionRun
from agent.guardrails.controls import Finding, Outcome
from agent.guardrails.rules import Action, Decision, PolicyConfig
from agent.memory import SqliteJournal
from agent.models import Autonomy, Choice
from agent.plan import agent_plan
from agent.think import BrainSetup, think
from agent.tools import Toolbox, ToolError

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(300), weekly_budget=Decimal(2000))


def decision(name: str, action: Action, amount: str = "100") -> Decision:
    outcome = {Action.PAY: Outcome.PASS, Action.HOLD: Outcome.HOLD, Action.ASK: Outcome.ASK}
    return Decision(invoice=name, supplier="S", amount=Decimal(amount), due_date=date(2026, 10, 20),
                    action=action, reasons=(f"rules say {action.value}",),
                    findings=(Finding("three_way_match", outcome[action], "detail"),))


@dataclass
class ScriptedBrain:
    """Calls the tools it is given, in order, like a model would; keeps what was refused."""

    calls: list[tuple[str, dict]]
    finish: bool = True
    model: str = "scripted"
    refused: dict[tuple[str, str], str] = field(default_factory=dict)  # (tool, invoice)

    def run(self, toolbox: Toolbox, briefing: str) -> SessionRun:
        for name, args in self.calls:
            try:
                toolbox.call(name, args)
            except ToolError as error:
                self.refused[(name, args.get("invoice", ""))] = str(error)
        if self.finish:
            toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=self.finish, steps=len(self.calls), usage=_no_usage(),
                          error="" if self.finish else "stopped")


def _no_usage():
    from agent.models import Usage
    return Usage()


DECISIONS = [
    decision("HELD", Action.HOLD),
    decision("ASKED", Action.ASK),
    decision("BIG", Action.PAY, "250"),
    decision("OK", Action.PAY, "100"),
]


def run(tmp_path, brain, room=Decimal(300), autonomy=Autonomy.ACT):
    setup = BrainSetup(brain=brain, zone=timezone(timedelta(hours=1)), autonomy=autonomy,
                       room=lambda: room)
    with SqliteJournal(tmp_path / "journal.sqlite3") as journal:
        thought = think(setup, journal=journal, ledger=None, decisions=DECISIONS, changes=(),
                        policy=POLICY, done=set(), now=NOW)
        plan = agent_plan(DECISIONS, thought.agent, NOW.date(), POLICY.weekly_budget, autonomy)
        return thought, plan, journal.sessions()


def tries_everything():
    reason = {"reason": "the model wants it paid"}
    return ScriptedBrain(calls=[
        ("pay_now", {"invoice": "HELD", **reason}),
        ("pay_now", {"invoice": "ASKED", **reason}),
        ("pay_now", {"invoice": "OK", **reason}),
        ("pay_now", {"invoice": "BIG", **reason}),  # 100 + 250 > 300 left this week
        ("schedule_payment", {"invoice": "HELD", "pay_on": "2026-10-10", **reason}),
        ("hold", {"invoice": "HELD", "reason": "the rules hold it"}),
        ("ask_owner", {"invoice": "ASKED", "question": "Approve?", "recommendation": "yes"}),
        ("schedule_payment", {"invoice": "BIG", "pay_on": "2026-10-13", "reason": "next week"}),
    ])


def test_the_model_cannot_pay_what_the_rules_hold_or_ask(tmp_path):
    brain = tries_everything()
    thought, plan, _ = run(tmp_path, brain)

    assert "the checks hold this invoice" in brain.refused[("pay_now", "HELD")]
    assert "the owner must answer first" in brain.refused[("pay_now", "ASKED")]
    assert "do not allow paying it" in brain.refused[("schedule_payment", "HELD")]
    assert [d.invoice for d in plan.pay_now] == ["OK"]
    assert {d.invoice for d in plan.held} == {"HELD"}
    assert {d.invoice for d in plan.asked} == {"ASKED"}


def test_the_model_cannot_pay_past_the_room_left_this_week(tmp_path):
    brain = tries_everything()
    thought, plan, _ = run(tmp_path, brain)

    assert "left under this week's cap" in brain.refused[("pay_now", "BIG")]
    assert thought.agent["BIG"].choice is Choice.SCHEDULE
    assert "BIG" not in {d.invoice for d in plan.pay_now}


def test_a_session_that_does_not_finish_pays_nothing(tmp_path):
    brain = ScriptedBrain(calls=[("pay_now", {"invoice": "OK", "reason": "pay it"})],
                          finish=False)
    thought, plan, sessions = run(tmp_path, brain)

    assert plan.pay_now == ()
    assert thought.agent == {}
    assert sessions[-1].finished is False


def test_observe_mode_pays_nothing(tmp_path):
    thought, plan, _ = run(tmp_path, tries_everything(), autonomy=Autonomy.OBSERVE)

    assert plan.pay_now == ()
    assert any("observe mode" in f.reason for f in plan.deferred)


def test_the_session_cannot_finish_with_an_invoice_undecided(tmp_path):
    brain = ScriptedBrain(calls=[("hold", {"invoice": "HELD", "reason": "held"})], finish=False)
    with SqliteJournal(tmp_path / "journal.sqlite3") as journal:
        box = Toolbox(decisions=DECISIONS, ledger=None, journal=journal, policy=POLICY, now=NOW,
                      zone=UTC)
        brain.run(box, "")
        try:
            box.call("finish", {"summary": "Done."})
        except ToolError as error:
            assert "ASKED" in str(error) and "OK" in str(error)
        else:
            raise AssertionError("finish was accepted with invoices undecided")


def test_the_owner_never_reads_a_way_around_a_limit(tmp_path):
    """The two recommendations Claude Haiku 5.5 wrote in the first real session are refused."""
    with SqliteJournal(tmp_path / "journal.sqlite3") as journal:
        box = Toolbox(decisions=DECISIONS, ledger=None, journal=journal, policy=POLICY, now=NOW,
                      zone=UTC)
        for advice in (
            "Hold; nothing can be paid until the amount is split or approved.",
            "Decide whether you want it settled another way or pushed to a later date.",
        ):
            try:
                box.call("ask_owner", {"invoice": "ASKED", "question": "Pay it?",
                                       "recommendation": advice})
            except ToolError as error:
                assert "rewrite this" in str(error)
            else:
                raise AssertionError(f"accepted: {advice}")
        box.call("ask_owner", {"invoice": "ASKED", "question": "Pay it?",
                               "recommendation": "It is over your limit, so it waits; check the "
                                                 "order with the supplier."})
        assert box.chosen["ASKED"].choice is Choice.ASK

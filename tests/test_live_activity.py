"""The owner watches the agent work: why it started and each tool it calls reach the feed while
the session runs, before its decisions and its answer, and are not written twice."""

from agent.agent import SessionRun
from agent.models import Autonomy, Usage
from agent.reflexes.cycle import run_cycle
from agent.think import BrainSetup
from services.shops import activity
from test_agent_in_cycle import LAGOS
from test_payments import NOW, POLICY, journal, make_payer, matching_ledger  # noqa: F401


class WatchedBrain:
    """Pays every undecided invoice and notes what the feed already shows after each call."""

    model = "scripted"

    def __init__(self, folder) -> None:
        self.folder, self.seen = folder, []

    def run(self, toolbox, briefing):
        toolbox.call("cash_position", {})
        self.seen.append([e["kind"] for e in activity.read_activity(self.folder)])
        for invoice in toolbox.undecided():
            toolbox.call("pay_now", {"invoice": invoice, "reason": "the checks pass"})
        self.seen.append([e["kind"] for e in activity.read_activity(self.folder)])
        toolbox.call("finish", {"summary": "Paid one bill."})
        return SessionRun(finished=True, steps=3, usage=Usage())


def cycle(journal, payer, setup):  # noqa: F811
    return run_cycle(payer.ledger, journal, POLICY, "TEST Shop", at=NOW, payments=payer,
                     brain=setup)


def test_each_step_is_in_the_feed_while_the_session_runs_and_only_once(journal, tmp_path):  # noqa: F811
    payer, _, circle = make_payer(matching_ledger())
    brain = WatchedBrain(tmp_path)
    setup = BrainSetup(brain=brain, zone=LAGOS, autonomy=Autonomy.ACT,
                       live=activity.LiveActivity(tmp_path))
    report = cycle(journal, payer, setup)

    assert brain.seen == [["reflex", "tool"], ["reflex", "tool", "tool"]]  # before it finished
    activity.append_activity(tmp_path, activity.agent_events(report, streamed=True))
    kinds = [e["kind"] for e in activity.read_activity(tmp_path)]
    assert kinds.count("reflex") == 1
    assert kinds.count("tool") == 3  # cash_position, pay_now, finish
    assert kinds[-1] == "agent"  # the decisions and the summary come after the steps
    assert len(circle.sent) == 1


class Broken:
    def started(self, at, wakes):
        raise OSError("disk full")

    def step(self, at, step):
        raise OSError("disk full")


def test_a_feed_that_cannot_be_written_never_stops_the_session(journal, tmp_path):  # noqa: F811
    payer, _, circle = make_payer(matching_ledger())
    setup = BrainSetup(brain=WatchedBrain(tmp_path), zone=LAGOS, autonomy=Autonomy.ACT,
                       live=Broken())
    report = cycle(journal, payer, setup)
    assert report.thought.session.finished
    assert len(circle.sent) == 1
    assert [s["tool"] for s in report.thought.session.trace] == ["cash_position", "pay_now",
                                                                  "finish"]

"""The agent's tools are a request the Claude API accepts: on 2026-10-14 a 21st strict tool made
every session fail with 'Too many strict tools'."""

import re

import pytest

from agent.tools import toolbox
from agent.tools.definitions import MAX_STRICT_TOOLS, TOOLS


def test_no_more_strict_tools_than_the_api_accepts():
    assert sum(1 for t in TOOLS if t["strict"]) <= MAX_STRICT_TOOLS


def test_every_multi_input_tool_is_strict():
    assert all(t["strict"] == (len(t["input_schema"]["properties"]) > 1) for t in TOOLS)


# What the model reads about a tool states the limits the toolbox enforces, so it plans within
# them instead of finding them by a refused call; a limit changed in one place fails here.
LIMITS = [
    ("set_alarm", toolbox.MAX_ALARMS), ("set_alarm", toolbox.MAX_ALARM_DAYS),
    ("note", toolbox.MAX_NOTES), ("note", toolbox.MAX_NOTE_CHARS),
    ("pay_now", toolbox.MAX_PAYMENTS),
    ("deposit_to_gateway", toolbox.MAX_FUND_MOVES), ("bring_funds", toolbox.MAX_FUND_MOVES),
    ("park_in_earn", toolbox.MAX_FUND_MOVES), ("withdraw_from_earn", toolbox.MAX_FUND_MOVES),
]


def _description(name: str) -> str:
    return next(t["description"] for t in TOOLS if t["name"] == name)


@pytest.mark.parametrize(("tool", "limit"), LIMITS)
def test_each_tool_states_the_limit_the_toolbox_enforces(tool, limit):
    assert re.search(rf"\b{limit}\b", _description(tool))


def test_hold_leaves_what_only_the_owner_knows_to_ask_owner():
    text = _description("hold")
    assert "ask_owner" in text and "not sure" not in text

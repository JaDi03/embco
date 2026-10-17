"""The agent's tools are a request the Claude API accepts: on 2026-10-14 a 21st strict tool made
every session fail with 'Too many strict tools'."""

from agent.tools.definitions import MAX_STRICT_TOOLS, TOOLS


def test_no_more_strict_tools_than_the_api_accepts():
    assert sum(1 for t in TOOLS if t["strict"]) <= MAX_STRICT_TOOLS


def test_every_multi_input_tool_is_strict():
    assert all(t["strict"] == (len(t["input_schema"]["properties"]) > 1) for t in TOOLS)

"""No test ever reaches the real model: the brain the settings would build is replaced by one
that cannot answer. Tests that need the agent to decide pass their own brain."""

import pytest

from agent.agent import SessionRun
from agent.models import Usage


class NoModel:
    def __init__(self, model: str = "no-model", **_: object) -> None:
        self.model = model

    def run(self, toolbox, briefing):
        return SessionRun(finished=False, steps=0, usage=Usage(), error="no model in tests")


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    monkeypatch.setattr("agent.wiring.ClaudeBrain", NoModel)

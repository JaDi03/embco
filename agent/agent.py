"""One session of the agent: Claude calls tools until it has decided on every open invoice.

The loop is ours, not the SDK's runner, so the caps are exact: a fixed number of model turns and
of tokens per session. A session that hits a cap, fails or is refused ends unfinished, and an
unfinished session changes nothing: no new payment, the previous decisions stay.

The default model is Claude Haiku 5.5 at medium effort. The goal and the tools are the same in
every request, so they are served from the prompt cache.
"""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from agent.models import Usage
from agent.prompt import SYSTEM
from agent.tools import TOOLS, Toolbox, ToolError

log = logging.getLogger("agent")

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT = "medium"
MAX_TURNS = 25
MAX_SESSION_TOKENS = 400_000
MAX_OUTPUT_TOKENS = 8000  # per response: room for adaptive thinking and several tool calls
NUDGE = "Call finish once every open invoice has your decision."


class BrainError(Exception):
    """The model could not be reached or used. The session ends unfinished."""


@dataclass(frozen=True)
class SessionRun:
    finished: bool
    steps: int  # tool calls made
    usage: Usage
    error: str = ""


class Brain(Protocol):
    model: str

    def run(self, toolbox: Toolbox, briefing: str) -> SessionRun: ...


class ClaudeBrain:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        effort: str = DEFAULT_EFFORT,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
        timeout: float = 120.0,
        max_turns: int = MAX_TURNS,
        max_tokens: int = MAX_SESSION_TOKENS,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=2)

    def run(self, toolbox: Toolbox, briefing: str) -> SessionRun:
        messages: list[dict[str, Any]] = [{"role": "user", "content": briefing}]
        usage, steps = Usage(), 0

        def ended(error: str = "") -> SessionRun:
            return SessionRun(finished=not error, steps=steps, usage=usage, error=error)

        for _ in range(self.max_turns):
            try:
                response = self._ask(messages)
            except BrainError as error:
                return ended(str(error))
            usage = usage + _usage(response)
            if response.stop_reason == "refusal":
                return ended("the model declined to continue")
            if response.stop_reason == "max_tokens":
                return ended("the model's answer was cut off")
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                messages.append({"role": "user", "content": NUDGE})
                continue
            results = []
            for call in calls:
                steps += 1
                try:
                    text, failed = toolbox.call(call.name, call.input), False
                except ToolError as error:
                    text, failed = str(error), True
                log.info("tool %s%s", call.name, " refused" if failed else "")
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": text, "is_error": failed})
            if toolbox.summary is not None:
                return ended()
            messages.append({"role": "user", "content": results})
            if usage.total > self.max_tokens:
                return ended(f"the session used more than {self.max_tokens} tokens")
        return ended(f"no finish after {self.max_turns} turns")

    def _ask(self, messages: list[dict[str, Any]]) -> Any:
        try:
            return self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},  # the conversation so far, turn by turn
            )
        except anthropic.AuthenticationError:
            raise BrainError("the Anthropic credentials were rejected") from None
        except anthropic.RateLimitError:
            raise BrainError("rate limited by the Anthropic API") from None
        except anthropic.APIStatusError as error:
            raise BrainError(f"Anthropic API error {error.status_code}") from None
        except anthropic.APIConnectionError:
            raise BrainError("could not reach the Anthropic API") from None
        except TypeError as error:
            if "authentication" not in str(error):
                raise
            raise BrainError("no Anthropic credentials (set ANTHROPIC_API_KEY)") from None


def _usage(response: Any) -> Usage:
    u = response.usage
    return Usage(input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                 cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                 cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)

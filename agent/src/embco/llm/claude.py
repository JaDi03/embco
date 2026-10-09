"""Explainer backed by Claude through the official Anthropic SDK.

Credentials: the key passed in (read from the settings), else the SDK's own resolution
(ANTHROPIC_API_KEY in the environment or an `ant auth login` profile).

The default is Claude Haiku 5.5, the lowest-cost model: explaining a decision is a short, simple
task, so it runs at low effort. Haiku has no server-side refusal fallback, so it gets a plain
request; Haiku 4.5 also takes no effort setting. Opus and Sonnet 5.x get low effort and the
server-side refusal fallback.
"""

from typing import Any

import anthropic

from embco.decision import Decision
from embco.llm.base import ExplainerError, ExplanationText
from embco.llm.prompt import SYSTEM, decision_message

DEFAULT_MODEL = "claude-haiku-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_PLAIN_MODELS = ("claude-haiku-4",)  # no effort setting, no fallback
_NO_FALLBACK_MODELS = ("claude-haiku-5",)  # effort, but no server-side refusal fallback


class ClaudeExplainer:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=2
        )

    def explain(self, decision: Decision, change_note: str) -> ExplanationText:
        request: dict[str, Any] = {
            "model": self.model,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": decision_message(decision, change_note)}],
            "output_format": ExplanationText,
        }
        try:
            if self.model.startswith(_PLAIN_MODELS):
                response = self._client.messages.parse(max_tokens=1024, **request)
            elif self.model.startswith(_NO_FALLBACK_MODELS):
                response = self._client.messages.parse(
                    max_tokens=4000,  # room for adaptive thinking before the short answer
                    output_config={"effort": "low"},
                    **request,
                )
            else:
                response = self._client.beta.messages.parse(
                    max_tokens=8000,  # room for adaptive thinking before the short answer
                    output_config={"effort": "low"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                    **request,
                )
        except anthropic.AuthenticationError:
            raise ExplainerError("the Anthropic credentials were rejected") from None
        except anthropic.RateLimitError:
            raise ExplainerError("rate limited by the Anthropic API; will try next cycle") from None
        except anthropic.APIStatusError as error:
            raise ExplainerError(f"Anthropic API error {error.status_code}") from None
        except anthropic.APIConnectionError:
            raise ExplainerError("could not reach the Anthropic API") from None
        except TypeError as error:
            if "authentication" not in str(error):
                raise
            raise ExplainerError("no Anthropic credentials (set ANTHROPIC_API_KEY)") from None
        if response.stop_reason == "refusal":
            raise ExplainerError("the model declined to explain this decision")
        if response.parsed_output is None:
            raise ExplainerError(f"no explanation returned (stop reason {response.stop_reason})")
        return response.parsed_output

"""Run Circle's Unified Balance Kit (kit/, Node) and read its answers.

Each call runs `node kit/cli.mjs <command>` with the input as JSON on stdin. Circle's key and
entity secret go in the child's environment, never in its arguments, and never come back in an
error: a message that carries either is cut before it leaves this module.
"""

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

KIT = Path(__file__).parent / "kit"
TIMEOUT = 300  # seconds: a spend waits for Gateway's attestation and the mint on Arc
USDC_PLACES = Decimal("0.000001")


class UnifiedBalanceError(Exception):
    pass


@dataclass(frozen=True)
class ChainFunds:
    """What the owner holds on one chain, and whether the agent may spend it there."""

    chain: str
    confirmed: Decimal
    pending: Decimal
    delegate: str  # "ready" once the owner authorized the agent there; "pending" or "none"

    @property
    def agent_can_spend(self) -> bool:
        return self.delegate == "ready"


@dataclass(frozen=True)
class Allocation:
    chain: str
    amount: Decimal


def usdc(amount: Decimal) -> str:
    return str(amount.quantize(USDC_PLACES))


class UnifiedBalance:
    def __init__(self, api_key: str, entity_secret: str, *, node: str = "node",
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self._secrets = (api_key, entity_secret)
        self._node = node
        self._run = run

    def __repr__(self) -> str:
        return "UnifiedBalance()"

    def _call(self, command: str, payload: dict[str, Any]) -> Any:
        api_key, entity_secret = self._secrets
        env = {**os.environ, "CIRCLE_API_KEY": api_key, "CIRCLE_ENTITY_SECRET": entity_secret}
        try:
            done = self._run([self._node, str(KIT / "cli.mjs"), command], input=json.dumps(payload),
                             capture_output=True, text=True, env=env, timeout=TIMEOUT, cwd=KIT)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UnifiedBalanceError(f"the Unified Balance Kit did not run: {error}") from None
        try:
            answer = json.loads(done.stdout.strip().splitlines()[-1], parse_float=Decimal)
        except (IndexError, ValueError):
            raise UnifiedBalanceError(self._clean(
                f"the Unified Balance Kit gave no answer (exit {done.returncode}): "
                f"{done.stderr.strip()[-300:]}")) from None
        if "error" in answer:
            raise UnifiedBalanceError(self._clean(str(answer["error"])))
        return answer["ok"]

    def _clean(self, message: str) -> str:
        for secret in self._secrets:
            if secret:
                message = message.replace(secret, "[hidden]")
        return message

    def balances(self, owner: str, agent: str) -> list[ChainFunds]:
        rows = self._call("balances", {"owner": owner, "agent": agent})["chains"]
        try:
            return [ChainFunds(r["chain"], Decimal(r["confirmed"]), Decimal(r["pending"]),
                               r["delegate"]) for r in rows]
        except (KeyError, InvalidOperation):
            raise UnifiedBalanceError(
                "the Unified Balance Kit answered in an unknown form") from None

    def prepare(self, agent: str) -> list[str]:
        """Give the agent's Circle wallet the same address on every source chain."""
        return self._call("prepare", {"agent": agent})["chains"]

    def estimate(self, owner: str, agent: str, allocations: Sequence[Allocation]) -> Any:
        return self._call("estimate", _spend(owner, agent, allocations))

    def spend(self, owner: str, agent: str, allocations: Sequence[Allocation]) -> Any:
        return self._call("spend", _spend(owner, agent, allocations))


def _spend(owner: str, agent: str, allocations: Sequence[Allocation]) -> dict[str, Any]:
    total = sum((a.amount for a in allocations), Decimal(0))
    return {"owner": owner, "agent": agent, "amount": usdc(total),
            "allocations": [{"chain": a.chain, "amount": usdc(a.amount)} for a in allocations]}

"""The shop's journal anchored on Arc: the Merkle root of every entry, written by the agent into
the JournalAnchor registry (contracts/src/JournalAnchor.sol), and checked against a copy of the
journal by anyone.

Reading needs only the Arc node. Writing goes through the agent's Circle wallet: simulated first
on the node as the agent, then sent with an idempotency key made from the shop, the size and the
root, so a retry never anchors twice.
"""

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from agent.memory.merkle import root as merkle_root
from services.circle import CircleError
from services.payments import ArcRpc, ChainError, Payer

# JournalAnchor on Arc Testnet (contracts/script/DeployJournalAnchor.s.sol).
JOURNAL_ANCHOR = "0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582"

FOLLOW_SECONDS = 60
POLL_SECONDS = 2
_ANCHOR = "(bytes32,uint64,uint64)"


@dataclass(frozen=True)
class Anchor:
    root: str  # hex, no 0x
    size: int  # how many journal entries the root covers
    anchored_at: datetime | None


def journal_root(entry_hashes: Sequence[str]) -> bytes:
    """The Merkle root of the journal: one leaf per entry, its chain hash."""
    return merkle_root([bytes.fromhex(h) for h in entry_hashes])


def mismatches(entry_hashes: Sequence[str], anchors: Sequence[Anchor]) -> list[str]:
    """Every anchor the journal does not match. Empty means the copy is the one anchored."""
    problems = []
    for i, a in enumerate(anchors):
        if a.size > len(entry_hashes):
            problems.append(f"anchor {i} covers {a.size} entries; the journal has only "
                            f"{len(entry_hashes)} (entries were removed)")
        elif journal_root(entry_hashes[:a.size]).hex() != a.root:
            problems.append(f"anchor {i}: the first {a.size} entries do not give the root "
                            "written on Arc (an entry was changed, removed or reordered)")
    return problems


class JournalAnchors:
    def __init__(self, chain: ArcRpc, shop: str, *, address: str = JOURNAL_ANCHOR,
                 payer: Payer | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not address:
            raise ChainError("the JournalAnchor registry is not deployed yet")
        self.chain = chain
        self.shop = to_checksum_address(shop)
        self.address = to_checksum_address(address)
        self._payer = payer
        self._sleep = sleep

    def _view(self, sig: str, types: Sequence[str], args: Sequence[Any]) -> bytes:
        return self.chain.call(self.address, keccak(text=sig)[:4] + encode(list(types), list(args)))

    def count(self) -> int:
        (n,) = decode(["uint256"], self._view("count(address)", ["address"], [self.shop]))
        return n

    def latest(self) -> Anchor:
        (raw,) = decode([_ANCHOR], self._view("latest(address)", ["address"], [self.shop]))
        return _anchor(raw)

    def all(self) -> list[Anchor]:
        return [_anchor(decode([_ANCHOR], self._view("anchorAt(address,uint256)",
                                                     ["address", "uint256"], [self.shop, i]))[0])
                for i in range(self.count())]

    def write(self, root: bytes, size: int) -> dict[str, Any]:
        """Anchor `root` for the first `size` entries, from the agent's wallet."""
        if self._payer is None:
            raise ChainError("anchoring needs the agent's wallet")
        call = (keccak(text="anchor(address,bytes32,uint64)")[:4]
                + encode(["address", "bytes32", "uint64"], [self.shop, root, size]))
        self.chain.call(self.address, call, sender=self._payer.agent_address())  # reverts say why
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"embco-anchor:{self.shop}:{size}:{root.hex()}"))
        sent = self._payer.circle.execute_contract(self._payer.wallet_id, self.address, call,
                                                   idempotency_key=key)
        waited = 0
        while not sent.finished and waited < FOLLOW_SECONDS:
            self._sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            sent = self._payer.circle.get_transaction(sent.id)
        if sent.finished and sent.state != "COMPLETE":
            raise CircleError(f"journal anchor {sent.state.lower()}: "
                              f"{sent.error_reason or 'no reason given'}")
        return {"root": root.hex(), "size": size, "state": sent.state.lower(),
                "tx": sent.tx_hash or "", "circle_tx": sent.id}


def _anchor(raw: tuple[bytes, int, int]) -> Anchor:
    root, size, at = raw
    return Anchor(root=root.hex(), size=size,
                  anchored_at=datetime.fromtimestamp(at, UTC) if at else None)

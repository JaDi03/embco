"""Discover confirmed creation events and advance durable verification jobs one step at a time."""

import json
import logging
import re
import time
from typing import Any

from eth_utils import to_checksum_address

from services.verification.client import Api, LogRangeError, VerificationError, exact
from services.verification.config import Factory
from services.verification.store import Store

log = logging.getLogger("embco.verification")
BLOCKS = 99_999
CONFIRMATIONS = 12
INDEX_LAG_BLOCKS = 1000
RANGES_PER_FACTORY = 8
HEX_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")


def payload(spec: Factory, bundle: dict, tx: str) -> dict:
    identifier = f"src/{spec.contract}.sol:{spec.contract}"
    source = bundle["stdJsonInput"]
    if identifier.split(":")[0] not in source["sources"]:
        raise VerificationError("sourcify: child source missing from factory compilation")
    return {"stdJsonInput": source, "compilerVersion": bundle["compilation"]["compilerVersion"],
            "contractIdentifier": identifier, "creationTransactionHash": tx}


def creation(spec: Factory, event: dict, start: int, end: int) -> tuple | None:
    if event.get("removed"):
        return None
    topics = event["topics"]
    if (event["address"].lower() != spec.address.lower()
            or len(topics) < 3 or topics[0].lower() != spec.topic.lower()):
        raise VerificationError("rpc: unexpected factory event")
    block, tx = int(event["blockNumber"], 16), event["transactionHash"]
    if (not start <= block <= end or not HEX_HASH.fullmatch(tx)
            or not HEX_HASH.fullmatch(topics[2]) or topics[2][2:26] != "0" * 24):
        raise VerificationError("rpc: invalid creation event")
    address = to_checksum_address("0x" + topics[2][-40:])
    if int(address, 16) == 0:
        raise VerificationError("rpc: zero child address")
    return spec.chain, address.lower(), spec.key, tx.lower(), block


class Worker:
    def __init__(self, store: Store, api: Api, specs: list[Factory]) -> None:
        self.store, self.api = store, api
        self.specs = {spec.key: spec for spec in specs}

    def discover(self, spec: Factory, now: int) -> None:
        state = self.store.factory(spec)
        if state is not None and state["retry_at"] > now:
            return
        if state is None:
            bundle = self.api.bundle(spec.chain, spec.address)
            # Validate the child and input before persisting a compilation for this factory.
            payload(spec, bundle, "0x" + "0" * 64)
            self.store.bootstrap(spec, bundle)
            state = self.store.factory(spec)
        chain = int(self.api.rpc(spec.chain, spec.rpc, "eth_chainId", []), 16)
        if chain != spec.chain:
            raise VerificationError("rpc: wrong chain ID")
        head = int(self.api.rpc(spec.chain, spec.rpc, "eth_blockNumber", []), 16) - CONFIRMATIONS
        start = state["next_block"]
        span = BLOCKS
        for _ in range(RANGES_PER_FACTORY):
            if start > head:
                break
            while True:
                end = min(head, start + span - 1)
                if start < head - INDEX_LAG_BLOCKS:
                    end = min(end, head - INDEX_LAG_BLOCKS - 1)
                try:
                    events = self.logs(spec, start, end, head)
                    break
                except LogRangeError:
                    if span == 1:
                        raise
                    span = max(1, span // 2)
            if not isinstance(events, list):
                raise VerificationError("rpc: logs must be a list")
            jobs = [row for event in events if (row := creation(spec, event, start, end))]
            self.store.discovered(spec, end + 1, jobs)
            start = end + 1

    def logs(self, spec: Factory, start: int, end: int, head: int) -> list:
        # Old ranges can outlive a public node's retained history. An indexed fallback
        # is deliberately excluded near the head, where indexing delays can hide new events.
        if end < head - INDEX_LAG_BLOCKS:
            try:
                return self.api.historical_logs(spec.explorer, spec.address, spec.topic, start, end)
            except LogRangeError:
                raise
            except VerificationError:
                pass  # a slow/unavailable index does not prevent using an archive-capable node
        return self.api.rpc(spec.chain, spec.rpc, "eth_getLogs", [{
            "address": spec.address, "topics": [spec.topic],
            "fromBlock": hex(start), "toBlock": hex(end),
        }])

    def advance(self, job: Any, spec: Factory, now: int) -> None:
        source_ok, explorer_ok = bool(job["sourcify"]), bool(job["explorer"])
        ticket, failures, error = job["ticket"], job["failures"], ""
        retry = now + 60
        try:
            if not source_ok:
                if ticket:
                    result = self.api.poll(ticket)
                    if result.get("isJobCompleted") is True:
                        ticket = ""
                        source_ok = exact(result.get("contract") or {}, spec.chain, job["address"])
                        if not source_ok:
                            code = (result.get("error") or {}).get("customCode", "not_exact")
                            # Codes only: compiler errors may contain arbitrarily large source text.
                            code = re.sub(r"[^a-zA-Z0-9_-]", "", str(code))[:60]
                            raise VerificationError(f"sourcify: {code}")
                    elif result.get("isJobCompleted") is not False:
                        raise VerificationError("sourcify: unexpected job status")
                else:
                    source_ok = self.api.lookup(spec.chain, job["address"])
                    if not source_ok:
                        bundle = json.loads(self.store.factory(spec)["bundle"])
                        ticket = self.api.submit(spec.chain, job["address"],
                                                 payload(spec, bundle, job["tx"]))
            if source_ok and not explorer_ok:
                explorer_ok = self.nudge_explorer(spec, job["address"])
            failures = 0
        except (VerificationError, KeyError, ValueError, TypeError) as exc:
            failures += 1
            error = (str(exc) if isinstance(exc, VerificationError)
                     else "unexpected verification data")
            retry = now + min(86_400, 60 * 2 ** min(failures, 11))
            log.warning("verification %s %s: %s", spec.chain, job["address"], error)
        self.store.save_job(spec.chain, job["address"], sourcify=source_ok, explorer=explorer_ok,
                            ticket=ticket, retry_at=retry, failures=failures, error=error)
        if source_ok:
            log.info("verified %s %s (%s)", spec.chain, job["address"], spec.contract)

    def nudge_explorer(self, spec: Factory, address: str) -> bool:
        # Sourcify is the verification record. The explorer is asked once to import it;
        # its failures never keep a job pending.
        try:
            if self.api.explorer_verified(spec.explorer, address):
                return True
            self.api.import_sourcify(spec.explorer, address)
            return True
        except (VerificationError, KeyError, ValueError, TypeError):
            log.info("explorer import skipped %s %s", spec.chain, address)
            return False

    def run(self, *, discover_only: bool = False, max_jobs: int = 10) -> dict[str, int]:
        now = int(time.time())
        for spec in self.specs.values():
            try:
                self.discover(spec, now)
            except (VerificationError, KeyError, ValueError, TypeError) as exc:
                error = (str(exc) if isinstance(exc, VerificationError)
                         else "unexpected discovery data")
                self.store.defer_factory(spec, now, error)
                log.warning("discover %s %s: %s", spec.chain, spec.address, error)
        if not discover_only:
            for job in self.store.due(now, max_jobs, tuple(self.specs)):
                spec = self.specs.get(job["factory"])
                if spec is not None:
                    self.advance(job, spec, now)
        return self.store.summary()

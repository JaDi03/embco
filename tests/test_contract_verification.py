"""Discovery, crash recovery and verifier failures never need an owner or a signing wallet."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from services.verification.__main__ import main
from services.verification.client import SOURCIFY, Api, VerificationError, retry_after
from services.verification.config import factories
from services.verification.store import Store
from services.verification.worker import Worker, creation, payload

NOW = 1_800_000_000
SHOP = "0xac636c6d228ca1cebb62d09e6cc611d4551f7a06"
TX = "0x61fa8d33df1d2c12ffd3a54c7f27fba83ced65b1381e5ecd19ce740d8054e3c7"
TICKET = "72d12273-0723-448e-a9f6-f7957128efa5"
SPEC = factories({"ARC_TESTNET_RPC_URL": "https://rpc.example.test/secret-canary"})[0]


def compilation(spec=SPEC):
    return {"chainId": str(spec.chain), "address": spec.address,
            "runtimeMatch": "exact_match", "creationMatch": "exact_match",
            "deployment": {"blockNumber": "66943142"},
            "compilation": {"compilerVersion": "0.8.37+commit.f401782d"},
            "stdJsonInput": {"language": "Solidity",
                             "sources": {f"src/{spec.contract}.sol": {"content": "source"}},
                             "settings": {"evmVersion": "osaka",
                                          "optimizer": {"enabled": True, "runs": 200}}}}


def event(spec=SPEC, block=66943142):
    return {"address": spec.address, "topics": [spec.topic, "0x" + "0" * 64,
                                               "0x" + "0" * 24 + SHOP[2:]],
            "transactionHash": TX, "blockNumber": hex(block), "removed": False}


def matched(**overrides):
    return {"chainId": str(SPEC.chain), "address": SHOP,
            "runtimeMatch": "exact_match", "creationMatch": "exact_match", **overrides}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW)
    db = Store(tmp_path / "verification.sqlite3")
    yield db
    db.close()


def queued(store):
    store.bootstrap(SPEC, compilation())
    store.discovered(SPEC, 66943143, [creation(SPEC, event(), 66943142, 66943142)])
    return store.due(NOW, 10, (SPEC.key,))[0]


def api_for(store, handler):
    return Api(httpx.Client(transport=httpx.MockTransport(handler)), store)


def test_discovery_survives_restart_and_needs_no_browser_or_erp(store):
    starts = []

    def handler(request):
        if request.method == "GET":
            assert request.url.params["fields"] == (
                "stdJsonInput,compilation.compilerVersion,deployment.blockNumber")
            return httpx.Response(200, json=compilation())
        body = json.loads(request.content)
        method = body["method"]
        if method == "eth_chainId":
            result = hex(SPEC.chain)
        elif method == "eth_blockNumber":
            result = hex(66943155)
        else:
            assert method == "eth_getLogs"
            starts.append(body["params"][0]["fromBlock"])
            result = [event()]
        return httpx.Response(200, json={"result": result})

    api = api_for(store, handler)
    Worker(store, api, [SPEC]).run(discover_only=True)
    assert store.summary() == {"discovered": 1, "sourcify": 0, "explorer": 0, "pending": 1}
    assert store.factory(SPEC)["next_block"] == 66943144
    # Use a new connection like a restarted timer process.
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    restored = Store(Path(path))
    try:
        Worker(restored, api_for(restored, handler), [SPEC]).run(discover_only=True)
        assert restored.summary()["discovered"] == 1
        assert len(starts) == 1
    finally:
        restored.close()


def test_failed_or_malformed_log_range_does_not_advance_cursor(store):
    store.bootstrap(SPEC, compilation())

    def handler(request):
        method = json.loads(request.content)["method"]
        result = {"eth_chainId": hex(SPEC.chain), "eth_blockNumber": hex(66943200),
                  "eth_getLogs": [{**event(), "transactionHash": "bad"}]}[method]
        return httpx.Response(200, json={"result": result})

    Worker(store, api_for(store, handler), [SPEC]).run(discover_only=True)
    assert store.factory(SPEC)["next_block"] == 66943142
    assert store.summary()["discovered"] == 0
    assert store.factory(SPEC)["retry_at"] > NOW


def test_wrong_network_is_not_scanned(store):
    store.bootstrap(SPEC, compilation())
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["method"])
        return httpx.Response(200, json={"result": "0x1"})

    Worker(store, api_for(store, handler), [SPEC]).run(discover_only=True)
    assert calls == ["eth_chainId"]
    assert store.factory(SPEC)["error"] == "rpc: wrong chain ID"


def test_node_log_limits_reduce_the_range_without_skipping_blocks(store):
    store.bootstrap(SPEC, compilation())
    ranges = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={})
        body = json.loads(request.content)
        if body["method"] == "eth_chainId":
            return httpx.Response(200, json={"result": hex(SPEC.chain)})
        if body["method"] == "eth_blockNumber":
            return httpx.Response(200, json={"result": hex(66943142 + 100_000)})
        query = body["params"][0]
        start, end = int(query["fromBlock"], 16), int(query["toBlock"], 16)
        ranges.append((start, end))
        if end - start > 9999:
            return httpx.Response(200, json={"error": {"code": -32005, "message": "limit"}})
        return httpx.Response(200, json={"result": []})

    Worker(store, api_for(store, handler), [SPEC]).run(discover_only=True)
    successful = [(s, e) for s, e in ranges if e - s <= 9999]
    assert successful[0][0] == 66943142
    assert all(b[0] == a[1] + 1 for a, b in zip(successful, successful[1:], strict=False))
    assert store.factory(SPEC)["next_block"] == successful[-1][1] + 1


def test_base_http_413_log_limits_are_reduced(store):
    api = api_for(store, lambda request: httpx.Response(413, json={
        "error": {"code": -32614, "message": "eth_getLogs is limited to a 200 range"}}))
    from services.verification.client import LogRangeError
    with pytest.raises(LogRangeError):
        api.rpc(84532, "https://rpc.example.test", "eth_getLogs", [])


def test_indexed_history_and_live_rpc_are_separate(store):
    def handler(request):
        if request.method == "GET":
            assert request.url.path.endswith("/logs")
            return httpx.Response(200, json={"next_page_params": None, "items": [{
                "topics": event()["topics"], "block_number": 66943142, "transaction_hash": TX}]})
        return httpx.Response(200, json={"result": []})

    worker = Worker(store, api_for(store, handler), [SPEC])
    assert worker.logs(SPEC, 66943142, 66943143, 68000000) == [event()]
    assert worker.logs(SPEC, 66943142, 66943143, 66943155) == []


def test_indexed_history_pagination_is_not_silently_truncated(store):
    api = api_for(store, lambda request: httpx.Response(
        200, json={"items": [], "next_page_params": {"block_number": 66943142}}))
    with pytest.raises(VerificationError, match="exceeds 20 pages"):
        api.historical_logs(SPEC.explorer, SPEC.address, SPEC.topic, 1, 100000)


def test_duplicate_events_do_not_duplicate_jobs_and_removed_events_are_ignored(store):
    queued(store)
    store.discovered(SPEC, 66943144, [creation(SPEC, event(), 66943142, 66943144)])
    assert store.summary()["discovered"] == 1
    assert creation(SPEC, {**event(), "removed": True}, 0, 99999999) is None


@pytest.mark.parametrize("index", range(4))
def test_payload_uses_the_original_factory_compilation_for_each_contract_kind(index):
    spec = factories({"ARC_TESTNET_RPC_URL": "https://rpc.example.test"})[index]
    bundle = compilation(spec)
    body = payload(spec, bundle, TX)
    assert body["stdJsonInput"] == bundle["stdJsonInput"]
    assert body["contractIdentifier"] == f"src/{spec.contract}.sol:{spec.contract}"
    assert body["creationTransactionHash"] == TX
    assert body["compilerVersion"] == "0.8.37+commit.f401782d"


def test_submit_ticket_is_saved_and_polled_after_restart(store):
    job = queued(store)
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"customCode": "not_found"})
        assert json.loads(request.content) == payload(SPEC, compilation(), TX)
        return httpx.Response(202, json={"verificationId": TICKET})

    Worker(store, api_for(store, handler), [SPEC]).advance(job, SPEC, NOW)
    saved = store.due(NOW + 60, 1, (SPEC.key,))[0]
    assert saved["ticket"] == TICKET
    assert not saved["sourcify"]
    assert len(requests) == 2

    def poll(request):
        assert str(request.url) == f"{SOURCIFY}/v2/verify/{TICKET}"
        return httpx.Response(200, json={"isJobCompleted": False})

    Worker(store, api_for(store, poll), [SPEC]).advance(saved, SPEC, NOW + 60)
    assert store.due(NOW + 120, 1, (SPEC.key,))[0]["ticket"] == TICKET


@pytest.mark.parametrize("result", [
    {"isJobCompleted": True, "contract": matched(runtimeMatch="match")},
    {"isJobCompleted": True, "contract": matched(address="0x" + "1" * 40)},
    {"isJobCompleted": True, "contract": matched(chainId="1")},
    {"isJobCompleted": True, "error": {"customCode": "no_match", "message": "secret-canary"}},
])
def test_http_200_partial_or_wrong_matches_are_not_marked_verified(store, result):
    job = dict(queued(store))
    job["ticket"] = TICKET
    api = api_for(store, lambda request: httpx.Response(200, json=result))
    Worker(store, api, [SPEC]).advance(job, SPEC, NOW)
    saved = store.due(NOW + 1000, 1, (SPEC.key,))[0]
    assert saved["sourcify"] == 0
    assert saved["explorer"] == 0
    assert saved["ticket"] == ""
    assert "secret-canary" not in saved["error"]


def test_sourcify_match_completes_the_job_and_the_explorer_is_asked_once(store):
    job = queued(store)

    def handler(request):
        if request.url.host == "sourcify.dev":
            return httpx.Response(200, json=matched())
        if request.method == "GET":
            return httpx.Response(200, json={"hash": SHOP, "is_verified": False})
        assert request.url.params["action"] == "verify_via_sourcify"
        assert request.content == f"addressHash={SHOP}".encode()
        return httpx.Response(200, json={"status": "1"})

    Worker(store, api_for(store, handler), [SPEC]).advance(job, SPEC, NOW)
    assert store.summary() == {"discovered": 1, "sourcify": 1, "explorer": 1, "pending": 0}
    assert store.due(NOW + 999999, 10, (SPEC.key,)) == []


def test_explorer_failure_never_keeps_a_sourcify_match_pending(store):
    job = queued(store)

    def handler(request):
        if request.url.host == "sourcify.dev":
            return httpx.Response(200, json=matched())
        return httpx.Response(502, json={})

    Worker(store, api_for(store, handler), [SPEC]).advance(job, SPEC, NOW)
    saved = store.db.execute("SELECT * FROM jobs").fetchone()
    assert saved["sourcify"] == 1 and saved["explorer"] == 0
    assert saved["failures"] == 0 and saved["error"] == ""
    assert store.summary()["pending"] == 0
    assert store.due(NOW + 999999, 10, (SPEC.key,)) == []


def test_rate_limit_survives_restart_and_does_not_send_more_requests(store):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"}, json={})

    api = api_for(store, handler)
    with pytest.raises(VerificationError):
        api.lookup(SPEC.chain, SHOP)
    with pytest.raises(VerificationError, match="cooling down"):
        Api(api.http, store).lookup(SPEC.chain, SHOP)
    assert len(requests) == 1
    assert store.cooldown("sourcify") == NOW + 3600


def test_rpc_errors_never_log_node_credentials(store):
    def handler(request):
        raise httpx.ConnectError("secret-canary", request=request)

    with pytest.raises(VerificationError) as error:
        api_for(store, handler).rpc(SPEC.chain, SPEC.rpc, "eth_chainId", [])
    assert "secret-canary" not in str(error.value)


def test_inactive_factories_cannot_starve_active_jobs(store):
    queued(store)
    other = replace(SPEC, address="0x" + "1" * 40)
    store.bootstrap(other, compilation())
    store.discovered(other, 66943144, [(other.chain, "0x" + "2" * 40, other.key, TX, 1)])
    assert store.due(NOW, 1, (SPEC.key,))[0]["address"] == SHOP


def test_status_needs_no_erp_circle_or_network(tmp_path, capsys):
    assert main(["--state", str(tmp_path / "state.sqlite3"), "--status"]) == 0
    assert json.loads(capsys.readouterr().out)["discovered"] == 0


def test_retry_after_supports_http_dates():
    assert retry_after("Fri, 15 Jan 2027 08:01:00 GMT", 1_800_000_000) >= 0
    assert retry_after("120", NOW) == 120
    assert retry_after("bad", NOW) == 300

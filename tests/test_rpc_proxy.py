"""The site's /rpc passes page and wallet calls to the Arc node, never a node's own methods, and
holds each client to a budget."""

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.hub.rpc_proxy import MAX_BATCH, MAX_BODY, CallBudget, rpc_routes

NODE = "https://node.example/key-in-the-path"


def proxy(budget=None):
    sent = []

    def node(request):
        sent.append((str(request.url), json.loads(request.content)))
        body = json.loads(request.content)
        one = lambda c: {"jsonrpc": "2.0", "id": c["id"], "result": "0x1"}  # noqa: E731
        return httpx.Response(200, json=[one(c) for c in body] if isinstance(body, list)
                              else one(body))

    app = FastAPI()
    app.include_router(rpc_routes(NODE, client=httpx.AsyncClient(
        transport=httpx.MockTransport(node)), budget=budget))
    return TestClient(app), sent


def call(method, call_id=1):
    return {"jsonrpc": "2.0", "id": call_id, "method": method, "params": []}


@pytest.mark.parametrize("method", ["eth_call", "eth_getLogs", "eth_sendRawTransaction",
                                    "eth_estimateGas", "eth_feeHistory", "net_version",
                                    "web3_clientVersion"])
def test_what_a_page_and_a_wallet_send_reaches_the_node(method):
    client, sent = proxy()
    answer = client.post("/rpc", json=call(method))
    assert answer.status_code == 200 and answer.json()["result"] == "0x1"
    assert sent == [(NODE, call(method))]


@pytest.mark.parametrize("method", ["debug_traceTransaction", "trace_block", "txpool_content",
                                    "admin_peers", "personal_listAccounts", "", None])
def test_a_nodes_own_methods_never_reach_it(method):
    client, sent = proxy()
    answer = client.post("/rpc", json=call(method, 7))
    assert answer.json() == {"jsonrpc": "2.0", "id": 7,
                             "error": {"code": -32601, "message": "method not available here"}}
    assert sent == []


def test_a_batch_passes_whole_or_not_at_all():
    client, sent = proxy()
    batch = [call("eth_blockNumber", i) for i in range(MAX_BATCH)]
    assert len(client.post("/rpc", json=batch).json()) == MAX_BATCH
    mixed = [call("eth_call", 1), call("debug_traceCall", 2)]
    assert [a["id"] for a in client.post("/rpc", json=mixed).json()] == [2]
    assert client.post("/rpc", json=[*batch, call("eth_call", 99)]).status_code == 413
    assert client.post("/rpc", json=[]).status_code == 400
    assert len(sent) == 1


def test_a_large_or_broken_body_never_reaches_the_node():
    client, sent = proxy()
    big = call("eth_call") | {"params": ["0x" + "00" * MAX_BODY]}
    assert client.post("/rpc", json=big).status_code == 413
    assert client.post("/rpc", content=b"{not json").status_code == 400
    assert sent == []


def test_each_client_gets_a_budget_per_minute():
    now = [0.0]
    client, sent = proxy(CallBudget(per_minute=3, clock=lambda: now[0]))
    assert client.post("/rpc", json=[call("eth_call", i) for i in range(3)]).status_code == 200
    refused = client.post("/rpc", json=call("eth_call"))
    assert refused.status_code == 429 and refused.json()["error"]["code"] == -32005
    now[0] = 61.0  # a minute later
    assert client.post("/rpc", json=call("eth_call")).status_code == 200
    assert len(sent) == 2


def test_the_budget_is_per_client_address():
    budget = CallBudget(per_minute=1, clock=lambda: 0.0)
    assert budget.take("203.0.113.1", 1) and not budget.take("203.0.113.1", 1)
    assert budget.take("203.0.113.2", 1)


def test_a_node_that_does_not_answer_is_a_clear_error_without_its_address():
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    app = FastAPI()
    app.include_router(rpc_routes(NODE, client=httpx.AsyncClient(
        transport=httpx.MockTransport(down))))
    answer = TestClient(app).post("/rpc", json=call("eth_call"))
    assert answer.status_code == 502 and "ConnectError" in answer.text
    assert "node.example" not in answer.text and "key-in-the-path" not in answer.text

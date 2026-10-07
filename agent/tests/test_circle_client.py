import base64
import json
import secrets

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from embco.circle import CircleClient, CircleError

# Keys are generated at run time; nothing secret is written in the repo.
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_PEM = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()
OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


class FakeCircle:
    def __init__(self, routes=None):
        self.requests: list[httpx.Request] = []
        self.routes = {
            ("GET", "/v1/w3s/config/entity/publicKey"): (200, {"data": {"publicKey": PUBLIC_PEM}}),
            **(routes or {}),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.routes[(request.method, request.url.path)]
        return httpx.Response(status, json=body)

    def bodies(self, path):
        return [json.loads(r.content) for r in self.requests if r.url.path == path]


def make(fake, api_key="TEST_API_KEY:id:secret", entity_secret=None):
    entity_secret = entity_secret or secrets.token_hex(32)
    client = CircleClient(api_key, entity_secret,
                          client=httpx.Client(transport=httpx.MockTransport(fake)))
    return client, entity_secret


def decrypt(ciphertext: str) -> bytes:
    return PRIVATE_KEY.decrypt(base64.b64decode(ciphertext), OAEP)


def test_ciphertext_decrypts_to_the_entity_secret_and_is_new_each_time():
    fake = FakeCircle()
    client, entity_secret = make(fake)
    first, second = client.entity_secret_ciphertext(), client.entity_secret_ciphertext()
    assert first != second
    assert decrypt(first) == decrypt(second) == bytes.fromhex(entity_secret)
    assert len(fake.requests) == 1  # the public key is fetched once


def test_create_wallet_set_and_eoa_wallet_on_arc_testnet():
    fake = FakeCircle({
        ("POST", "/v1/w3s/developer/walletSets"): (201, {"data": {"walletSet": {"id": "ws-1"}}}),
        ("POST", "/v1/w3s/developer/wallets"): (201, {"data": {"wallets": [{
            "id": "w-1", "address": "0xabc", "blockchain": "ARC-TESTNET",
            "walletSetId": "ws-1", "accountType": "EOA",
        }]}}),
    })
    client, entity_secret = make(fake)
    wallet = client.create_eoa_wallet(client.create_wallet_set("embco TEST Shop"), "agent")
    assert (wallet.id, wallet.address, wallet.wallet_set_id) == ("w-1", "0xabc", "ws-1")
    [body] = fake.bodies("/v1/w3s/developer/wallets")
    assert body["accountType"] == "EOA"
    assert body["blockchains"] == ["ARC-TESTNET"]
    assert body["walletSetId"] == "ws-1"
    assert decrypt(body["entitySecretCiphertext"]) == bytes.fromhex(entity_secret)
    keys = [b["idempotencyKey"] for p in ("/v1/w3s/developer/walletSets",
                                          "/v1/w3s/developer/wallets") for b in fake.bodies(p)]
    assert len(set(keys)) == 2


def test_errors_carry_circle_code_but_never_the_credentials():
    api_key = "TEST_API_KEY:id:" + secrets.token_hex(16)
    fake = FakeCircle({("POST", "/v1/w3s/developer/walletSets"):
                       (401, {"code": 401, "message": "Malformed authorization."})})
    client, entity_secret = make(fake, api_key=api_key)
    with pytest.raises(CircleError) as error:
        client.create_wallet_set("x")
    text = str(error.value)
    assert "401" in text and "Malformed authorization." in text
    assert api_key not in text and entity_secret not in text
    assert api_key not in repr(client) and entity_secret not in repr(client)


@pytest.mark.parametrize("value", ["not-hex", "ab" * 31])
def test_entity_secret_must_be_32_bytes_of_hex(value):
    with pytest.raises(CircleError, match="64 hex"):
        CircleClient("k", value)


def test_reads_are_retried_when_the_connection_drops_but_writes_are_not():
    drops = {"GET": 2, "POST": 1}

    def flaky(request: httpx.Request) -> httpx.Response:
        if drops[request.method] > 0:
            drops[request.method] -= 1
            raise httpx.RemoteProtocolError("Server disconnected", request=request)
        return FakeCircle()(request)

    client, _ = make(flaky)
    assert client.entity_secret_ciphertext()  # the public key GET survived two drops
    with pytest.raises(CircleError, match="RemoteProtocolError"):
        client.create_wallet_set("x")

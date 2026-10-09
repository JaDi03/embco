"""Circle developer-controlled wallets over REST.

Every mutating request carries a fresh entity secret ciphertext (RSA-OAEP with SHA-256 over
Circle's entity public key, as in Circle's sample code) and an idempotency key. Error messages
carry Circle's error code and message, never the API key, the entity secret or a request body.
"""

import base64
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

BASE_URL = "https://api.circle.com"
ARC_TESTNET = "ARC-TESTNET"
TERMINAL_STATES = frozenset({"COMPLETE", "FAILED", "DENIED", "CANCELLED"})
READ_RETRIES = 2


class CircleError(Exception):
    pass


@dataclass(frozen=True)
class CircleWallet:
    id: str
    address: str
    blockchain: str
    wallet_set_id: str


@dataclass(frozen=True)
class CircleTransaction:
    id: str
    state: str
    tx_hash: str | None = None
    error_reason: str | None = None

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES


class CircleClient:
    def __init__(
        self,
        api_key: str,
        entity_secret: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        timeout: int = 30,
    ) -> None:
        try:
            self._entity_secret = bytes.fromhex(entity_secret)
        except ValueError:
            raise CircleError("the entity secret must be 64 hex characters") from None
        if len(self._entity_secret) != 32:
            raise CircleError("the entity secret must be 64 hex characters")
        self._client = client or httpx.Client(timeout=timeout)
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self._public_key: rsa.RSAPublicKey | None = None

    def __repr__(self) -> str:
        return f"CircleClient({self._base!r})"

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        # Reads are retried when the connection drops (Circle sometimes closes it). Writes are
        # not: each carries a single-use ciphertext, and the next cycle resends with the same
        # idempotency key.
        tries = 1 + (READ_RETRIES if method == "GET" else 0)
        for attempt in range(tries):
            try:
                response = self._client.request(
                    method, f"{self._base}{path}", headers=self._headers, json=body
                )
                break
            except httpx.TransportError as exc:
                if attempt + 1 == tries:
                    raise CircleError(f"Circle request failed: {type(exc).__name__}") from exc
            except httpx.HTTPError as exc:
                raise CircleError(f"Circle request failed: {type(exc).__name__}") from exc
        if response.status_code not in (200, 201):
            raise CircleError(f"Circle returned HTTP {response.status_code} for {path}"
                              f"{_error_detail(response)}")
        try:
            return response.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise CircleError(f"Circle returned an unexpected body for {path}") from None

    def entity_secret_ciphertext(self) -> str:
        """A new ciphertext on every call: Circle rejects a reused one."""
        if self._public_key is None:
            pem = self._request("GET", "/v1/w3s/config/entity/publicKey")["publicKey"]
            key = serialization.load_pem_public_key(pem.encode())
            if not isinstance(key, rsa.RSAPublicKey):
                raise CircleError("Circle's entity public key is not an RSA key")
            self._public_key = key
        encrypted = self._public_key.encrypt(
            self._entity_secret,
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        return base64.b64encode(encrypted).decode()

    def _signed_body(self, idempotency_key: str | None, **fields: Any) -> dict[str, Any]:
        return {
            "idempotencyKey": idempotency_key or str(uuid.uuid4()),
            "entitySecretCiphertext": self.entity_secret_ciphertext(),
            **fields,
        }

    def create_wallet_set(self, name: str, *, idempotency_key: str | None = None) -> str:
        data = self._request("POST", "/v1/w3s/developer/walletSets",
                             self._signed_body(idempotency_key, name=name))
        return data["walletSet"]["id"]

    def create_eoa_wallet(
        self,
        wallet_set_id: str,
        name: str,
        *,
        blockchain: str = ARC_TESTNET,
        idempotency_key: str | None = None,
    ) -> CircleWallet:
        body = self._signed_body(
            idempotency_key,
            walletSetId=wallet_set_id,
            blockchains=[blockchain],
            accountType="EOA",
            count=1,
            metadata=[{"name": name}],
        )
        wallets = self._request("POST", "/v1/w3s/developer/wallets", body)["wallets"]
        if len(wallets) != 1:
            raise CircleError(f"Circle created {len(wallets)} wallets, expected 1")
        w = wallets[0]
        return CircleWallet(id=w["id"], address=w["address"], blockchain=w["blockchain"],
                            wallet_set_id=w["walletSetId"])

    def get_wallet(self, wallet_id: str) -> CircleWallet:
        w = self._request("GET", f"/v1/w3s/wallets/{wallet_id}")["wallet"]
        return CircleWallet(id=w["id"], address=w["address"], blockchain=w["blockchain"],
                            wallet_set_id=w["walletSetId"])

    def execute_contract(
        self,
        wallet_id: str,
        contract: str,
        call_data: bytes,
        *,
        idempotency_key: str,
        ref_id: str | None = None,
        fee_level: str = "MEDIUM",
    ) -> CircleTransaction:
        """Circle signs with the wallet and sends the call. The same key returns the same
        transaction, so a retry after a crash never sends a second one."""
        fields: dict[str, Any] = {
            "walletId": wallet_id,
            "contractAddress": contract,
            "callData": "0x" + call_data.hex(),
            "feeLevel": fee_level,
        }
        if ref_id:
            fields["refId"] = ref_id
        data = self._request("POST", "/v1/w3s/developer/transactions/contractExecution",
                             self._signed_body(idempotency_key, **fields))
        return CircleTransaction(id=data["id"], state=data["state"])

    def get_transaction(self, transaction_id: str) -> CircleTransaction:
        tx = self._request("GET", f"/v1/w3s/transactions/{transaction_id}")["transaction"]
        return CircleTransaction(id=tx["id"], state=tx["state"], tx_hash=tx.get("txHash"),
                                 error_reason=tx.get("errorReason"))


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    code, message = body.get("code"), body.get("message")
    return f" (code {code}: {message})" if code or message else ""

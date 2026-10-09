"""EIP-712 message a supplier signs to prove it controls a wallet, and its verification.

Typed data shows the supplier readable fields in the wallet, and the domain binds the signature
to embco on one chain, so it cannot be replayed elsewhere.
"""

from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from agent.guardrails.controls.confirmations import WalletChallenge

ARC_TESTNET_CHAIN_ID = 5042002

_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "WalletOwnership": [
        {"name": "statement", "type": "string"},
        {"name": "payer", "type": "string"},
        {"name": "supplier", "type": "string"},
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "string"},
        {"name": "expiresAt", "type": "string"},
    ],
}


def typed_data(challenge: WalletChallenge, chain_id: int = ARC_TESTNET_CHAIN_ID) -> dict[str, Any]:
    """The exact message the supplier signs with eth_signTypedData_v4."""
    return {
        "types": _TYPES,
        "primaryType": "WalletOwnership",
        "domain": {"name": "embco", "version": "1", "chainId": chain_id},
        "message": {
            "statement": f"{challenge.supplier} asks {challenge.payer} to pay it to this wallet.",
            "payer": challenge.payer,
            "supplier": challenge.supplier,
            "wallet": challenge.wallet,
            "nonce": challenge.nonce,
            "expiresAt": challenge.expires_at.isoformat(),
        },
    }


def recover_signer(
    challenge: WalletChallenge, signature: str, chain_id: int = ARC_TESTNET_CHAIN_ID
) -> str | None:
    """The address that signed the challenge, or None if the signature is malformed.

    Covers ordinary wallets (EOA). Smart contract wallets need an on-chain EIP-1271 check.
    """
    signable = encode_typed_data(full_message=typed_data(challenge, chain_id))
    try:
        return Account.recover_message(signable, signature=signature)
    except Exception:  # noqa: BLE001 (any malformed signature means "not proven")
        return None

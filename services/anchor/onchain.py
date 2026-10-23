"""One decision of the agent as a single value anyone can paste into DecisionVerifier.verify on
Arc's explorer, with no wallet: the decision's MANIFEST entry, its audit path and which anchor it
is under. The explorer then shows Arc's answer, not embco's.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from eth_abi import encode
from eth_utils import to_checksum_address

from agent.memory.sqlite import entry_text_of
from services.anchor.audit import AuditError
from services.anchor.client import Anchor

# DecisionVerifier on Arc Testnet (contracts/script/DeployDecisionVerifier.s.sol): not deployed yet.
DECISION_VERIFIER = ""
ARC_EXPLORER = "https://explorer.testnet.arc.io"
PROOF_TYPE = "(address,uint256,uint256,bytes32[],bytes)"  # DecisionVerifier.Proof


def arc_proof(bundle: Mapping[str, Any], anchors: Sequence[Anchor]) -> str:
    """The value to paste, from a decision proof (make_decision_bundle, or the dashboard's
    download) and the anchors read from Arc, in the contract's order."""
    manifests = [p for p in bundle.get("proofs", []) if p["entry"]["kind"] == "MANIFEST"]
    if len(manifests) != 1:
        raise AuditError("this decision has no MANIFEST: it was written before decisions listed "
                         "their entries, so Arc cannot check it on its own")
    p = manifests[0]
    position = next((i for i, a in enumerate(anchors)
                     if a.size == p["size"] and a.root == p["root"]), None)
    if position is None:
        raise AuditError("the anchor this proof uses is not on Arc for this shop")
    text = entry_text_of(p["previous_hash"], p["entry"]).encode()
    value = (to_checksum_address(bundle["shop"]), position, int(p["index"]),
             [bytes.fromhex(h) for h in p["proof"]], text)
    return "0x" + encode([PROOF_TYPE], [value]).hex()

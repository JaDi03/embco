// One decision checked by Arc itself: the decision's MANIFEST, its audit path and its anchor as
// the one value DecisionVerifier.verify takes, and Arc's answer to it. The same value is what a
// person pastes in the explorer's Read form to ask Arc without this page.
// Same encoding as services/anchor/onchain.py (`embco arc-proof`).

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { DECISION_VERIFIER_ABI } from "./abi.js";
import { ARC_TESTNET, DECISION_VERIFIER_ADDRESS } from "./config.js";
import { chainText } from "./journal.js";

const PROOF_TYPE = "tuple(address,uint256,uint256,bytes32[],bytes)";  // DecisionVerifier.Proof

/** The page of the contract in the explorer, open on its Read form. */
export const VERIFIER_PAGE = `${ARC_TESTNET.explorer}/address/${DECISION_VERIFIER_ADDRESS}?tab=read_contract`;

/** The value to paste, from a decision proof and the anchors read from Arc (in the contract's
 * order). Null for a decision written before the MANIFEST: Arc cannot check it on its own. */
export function arcValue(bundle, anchors) {
  const manifests = bundle.proofs.filter((p) => p.entry?.kind === "MANIFEST");
  if (manifests.length !== 1) return null;
  const [p] = manifests;
  const position = anchors.findIndex((a) => a.size === p.size && a.root === p.root);
  if (position < 0) return null;
  const text = ethers.toUtf8Bytes(chainText(p.previous_hash, p.entry));
  return ethers.AbiCoder.defaultAbiCoder().encode([PROOF_TYPE],
    [[ethers.getAddress(bundle.shop), position, p.index, p.proof.map((h) => `0x${h}`), text]]);
}

/** Arc's answer to the value, read through `runner` (a provider): a plain object. */
export async function askArc(value, runner) {
  const verifier = new ethers.Contract(DECISION_VERIFIER_ADDRESS, DECISION_VERIFIER_ABI, runner);
  const r = await verifier.verify(value);
  return {
    verified: r.verified, answer: r.answer, shop: r.shop, owner: r.owner, invoice: r.invoice,
    decision: r.decision, reason: r.reason, amount: r.amount, supplier: r.supplier, payment: r.payment,
    paymentTx: r.paymentTx, decidedAt: r.decidedAt, records: Number(r.records),
    anchoredAt: r.anchoredAt ? new Date(Number(r.anchoredAt) * 1000).toISOString() : null,
  };
}

/** Whether what the page shows from the records is what Arc returned for them. A payment that
 * finished in a later cycle has its transaction after the MANIFEST, so Arc's may be empty. */
export function sameAsArc(story, arc) {
  const tx = (story.payment?.tx ?? "").toLowerCase();
  return arc.verified && arc.invoice === story.invoice && arc.decision === story.choice
    && arc.reason === story.reason && (!arc.paymentTx || arc.paymentTx.toLowerCase() === tx);
}

// Wallets the agent is waiting to pay, published by the agent on this site (no supplier names).
// The owner checks who a wallet belongs to in ERPNext, then approves it here with one signature.
// The file is only a suggestion: the contract still pays nobody the owner has not approved.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { APPROVALS_PATH } from "./config.js";
import { parseUsdc } from "./units.js";

const FORMAT = 1;
const MAX_INVOICE_LENGTH = 140;

/**
 * Validate the agent's file for this shop and group it by wallet:
 * [{ wallet, invoices: [{ invoice, amount }] }], amounts as exact 6-decimal integers.
 * Anything malformed, or for another shop, is dropped.
 */
export function parseApprovals(body, shop) {
  if (!body || body.format !== FORMAT || !Array.isArray(body.pending)) return [];
  if (String(body.shop ?? "").toLowerCase() !== String(shop).toLowerCase()) return [];
  const byWallet = new Map();
  for (const item of body.pending) {
    if (!item || !ethers.isAddress(item.wallet)) continue;
    const invoice = String(item.invoice ?? "");
    if (!invoice || invoice.length > MAX_INVOICE_LENGTH) continue;
    let amount;
    try {
      amount = parseUsdc(item.amount);
    } catch {
      continue;
    }
    const wallet = ethers.getAddress(item.wallet);
    if (!byWallet.has(wallet)) byWallet.set(wallet, { wallet, invoices: [] });
    byWallet.get(wallet).invoices.push({ invoice, amount });
  }
  return [...byWallet.values()];
}

/** The pending wallets the contract does not approve yet; an absent file means none. */
export async function pendingApprovals(shop, isApproved, fetchImpl = fetch) {
  const response = await fetchImpl(APPROVALS_PATH, { cache: "no-store" });
  if (response.status === 404) return [];
  if (!response.ok) throw new Error("Could not load the wallets waiting for approval.");
  const pending = parseApprovals(await response.json(), shop);
  const approved = await Promise.all(pending.map((p) => isApproved(p.wallet)));
  return pending.filter((_, i) => !approved[i]);
}

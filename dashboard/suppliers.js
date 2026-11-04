// Suppliers: the owner sees each supplier and sends it a notice; a supplier signs in with the
// wallet it is paid to, sees what each shop owes it and confirms that wallet with a signature.
// Notices never carry a link: the supplier types this site's address itself.

import { HUB_PATH } from "./config.js";
import { call, forEthers } from "./hub.js";
import { formatUsdc, parseUsdc } from "./units.js";

const shopPath = (shop) => `${HUB_PATH}/shops/${shop.toLowerCase()}`;
const supplierPath = `${HUB_PATH}/supplier`;

export const SUPPLIER_PAGE = "app.embco.xyz/supplier";

// ---- the owner

export const listSuppliers = (shop, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/suppliers`, { fetchImpl });

/**
 * Who an on-chain payment went to and for which invoice, from what the agent recorded: found by
 * its transaction, else only the supplier by its wallet. Null when nobody on file matches.
 */
export function paymentName(payment, list) {
  const tx = String(payment.tx ?? "").toLowerCase();
  const recorded = (list?.payments ?? []).find((p) => String(p.tx_hash).toLowerCase() === tx);
  if (recorded) return { supplier: recorded.supplier, invoice: recorded.invoice };
  const payee = String(payment.payee ?? "").toLowerCase();
  const row = (list?.suppliers ?? []).find((s) => s.wallet && s.wallet.toLowerCase() === payee);
  return row ? { supplier: row.supplier, invoice: null } : null;
}

const same = (a, b) => Boolean(a && b) && a.toLowerCase() === b.toLowerCase();

/**
 * One row per supplier, with whether the contract approves its wallet on file, and the approved
 * wallets no supplier uses now: `was` names the supplier a payment once went to, if any. Those are
 * the risk: the contract still lets the agent pay them.
 */
export function supplierBook(list, approved) {
  const rows = (list?.suppliers ?? []).map((row) => ({
    ...row, approved: approved.some((wallet) => same(wallet, row.wallet)),
  }));
  const loose = approved
    .filter((wallet) => !rows.some((row) => same(row.wallet, wallet)))
    .map((wallet) => ({
      wallet, was: (list?.payments ?? []).find((p) => same(p.wallet, wallet))?.supplier ?? null,
    }));
  return { rows, loose };
}

/** Approved wallets a payment went to for the supplier whose wallet on file is `wallet`, but not `wallet`. */
export function oldWallets(list, approved, wallet) {
  const owner = (list?.suppliers ?? []).find((row) => same(row.wallet, wallet))?.supplier;
  if (!owner) return [];
  const before = (list?.payments ?? []).filter((p) => p.supplier === owner && !same(p.wallet, wallet));
  return approved.filter((a) => before.some((p) => same(p.wallet, a)));
}

export const emailNotice = (shop, supplier, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/suppliers/${encodeURIComponent(supplier)}/email`, { method: "POST", fetchImpl });

/** The notice the owner can send by WhatsApp: the same words as the email, no link. */
export function noticeText({ company, supplier, wallet, signatureNeeded, site = SUPPLIER_PAGE }) {
  const ask = signatureNeeded
    ? `${company} asks you to confirm the wallet you are paid to${wallet ? `: ${wallet}` : ""}.`
    : `You can follow your invoices and payments from ${company}.`;
  return [
    `Hello ${supplier},`,
    ask,
    `Type ${site} in your browser yourself and sign in with that wallet. Do not use links in messages that ask you to sign.`,
    "Signing in or confirming your wallet moves no money. Nobody will ever ask for your private key or recovery phrase.",
  ].join("\n\n");
}

export const whatsappUrl = (text) => `https://wa.me/?text=${encodeURIComponent(text)}`;

// ---- the supplier

/** Sign-In with Ethereum (EIP-4361): the hub writes the message, the wallet signs it as is. */
export async function signInSupplier(signer, fetchImpl = fetch) {
  const wallet = await signer.getAddress();
  const request = await call(`${supplierPath}/sign-in-request`, { method: "POST", body: { wallet }, fetchImpl });
  const signature = await signer.signMessage(request.message);
  return call(`${supplierPath}/sign-in`, {
    method: "POST",
    body: { nonce: request.nonce, signature },
    fetchImpl,
  });
}

/** What the signed-in wallet is owed, or null when the supplier has to sign in first. */
export async function myPage(fetchImpl = fetch) {
  try {
    return await call(`${supplierPath}/me`, { fetchImpl });
  } catch (error) {
    if (error.status === 401) return null;
    throw error;
  }
}

export const signOutSupplier = (fetchImpl = fetch) =>
  call(`${supplierPath}/sign-out`, { method: "POST", fetchImpl });

/** Sign the shop's wallet challenge exactly as the agent wrote it, then hand it to the hub. */
export async function confirmWallet(signer, entry, fetchImpl = fetch) {
  const { domain, types, value } = forEthers(entry.challenge.typed_data);
  const signature = await signer.signTypedData(domain, types, value);
  return call(`${supplierPath}/signature`, {
    method: "POST",
    body: { shop: entry.shop, supplier: entry.supplier, signature },
    fetchImpl,
  });
}

const STATUS = {
  SCHEDULED: { label: "Will be paid", kind: "pay" },
  PAYMENT_SENT: { label: "Payment on its way", kind: "pay" },
  SIGNATURE_NEEDED: { label: "Confirm your wallet", kind: "ask" },
  UNDER_REVIEW: { label: "Under review by the shop", kind: "hold" },
};

export function invoiceStatus(code) {
  return STATUS[code] ?? { label: "Under review by the shop", kind: "hold" };
}

/** The supplier's last signature, in words, or null when there is nothing to say. */
export function signatureNote(entry) {
  const last = entry.last_signature;
  if (entry.challenge) {
    return last?.result === "REJECTED"
      ? { text: `Your last signature was not accepted: ${last.reason}. Sign again.`, kind: "error" }
      : null;
  }
  if (last?.result === "ACCEPTED") return { text: "Your wallet is confirmed.", kind: "success" };
  return null;
}

/** "1250.5" -> "1,250.5"; anything that is not a plain amount is shown as written. */
export function formatAmount(text) {
  try {
    return formatUsdc(parseUsdc(text));
  } catch {
    return String(text ?? "");
  }
}

/** "0x213793c4..." -> ["0x", "2137", "93c4", ...]: easier to read and compare than 42 characters. */
export function addressGroups(address) {
  const text = String(address ?? "");
  const body = text.startsWith("0x") ? text.slice(2) : text;
  return [text.startsWith("0x") ? "0x" : "", ...(body.match(/.{1,4}/g) ?? [])].filter(Boolean);
}

/**
 * Compare the wallet on file with an address the supplier types, character by character.
 * Upper and lower case mean the same in an address. `chars` marks each character of the wallet
 * on file: ok, wrong, or not typed yet.
 */
export function compareAddress(expected, typed) {
  const want = String(expected ?? "").toLowerCase();
  let got = String(typed ?? "").trim().toLowerCase();
  if (got && !got.startsWith("0x")) got = `0x${got}`;  // many apps copy the address without 0x
  const chars = [...want].map((c, i) => ({
    c: String(expected)[i],
    state: i >= got.length ? "missing" : got[i] === c ? "ok" : "wrong",
  }));
  let state = "mismatch";
  if (!got) state = "empty";
  else if (got === want) state = "match";
  else if (want.startsWith(got)) state = "partial";
  return { state, chars, extra: Math.max(0, got.length - want.length) };
}

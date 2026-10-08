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

export async function signInSupplier(signer, fetchImpl = fetch) {
  const wallet = await signer.getAddress();
  const typed = await call(`${supplierPath}/sign-in-request`, { method: "POST", body: { wallet }, fetchImpl });
  const { domain, types, value } = forEthers(typed);
  const signature = await signer.signTypedData(domain, types, value);
  return call(`${supplierPath}/sign-in`, {
    method: "POST",
    body: { nonce: typed.message.nonce, signature },
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

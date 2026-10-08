// The hub API: the owner signs in with a signature (not a transaction), connects the shop's
// ERPNext, and reads what the shop's own agent decided. ERP keys are sent once and never shown
// again; the session lives in a cookie this page cannot read.

import { HUB_PATH } from "./config.js";

export class HubError extends Error {
  constructor(status, message, details = {}) {
    super(message);
    this.name = "HubError";
    this.status = status;
    this.details = details;
  }
}

const shopPath = (shop) => `${HUB_PATH}/shops/${shop.toLowerCase()}`;

async function call(path, { method = "GET", body, fetchImpl = fetch } = {}) {
  const response = await fetchImpl(path, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    const { error, ...details } = data;
    throw new HubError(response.status, error || `The embco service answered ${response.status}.`, details);
  }
  return data;
}

/** ethers wants the types without the domain type, and the domain on its own. */
export function forEthers(typed) {
  const { EIP712Domain: _domain, ...types } = typed.types;
  return { domain: typed.domain, types, value: typed.message };
}

export async function signIn(signer, shop, fetchImpl = fetch) {
  const typed = await call(`${shopPath(shop)}/sign-in-request`, { method: "POST", fetchImpl });
  const { domain, types, value } = forEthers(typed);
  const signature = await signer.signTypedData(domain, types, value);
  return call(`${shopPath(shop)}/sign-in`, {
    method: "POST",
    body: { nonce: typed.message.nonce, signature },
    fetchImpl,
  });
}

/** The shop as the hub knows it, or null when the owner has to sign in first. */
export async function status(shop, fetchImpl = fetch) {
  try {
    return await call(shopPath(shop), { fetchImpl });
  } catch (error) {
    if (error instanceof HubError && error.status === 401) return null;
    throw error;
  }
}

export const connectErp = (shop, form, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/erp`, { method: "POST", body: form, fetchImpl });

export const disconnectErp = (shop, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/erp`, { method: "DELETE", fetchImpl });

export const signOut = (shop, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/sign-out`, { method: "POST", fetchImpl });

/** The connect form as the hub expects it; the optional JSON of extra fields is checked here. */
export function connectRequest(fields) {
  const text = (name) => String(fields[name] ?? "").trim();
  const request = {
    erp_url: text("erpUrl"),
    company: text("company"),
    api_key: text("apiKey"),
    api_secret: text("apiSecret"),
    wallet_bank: text("walletBank") || null,
    payment_extra: {},
  };
  for (const [name, label] of [["erp_url", "ERPNext address"], ["company", "company"], ["api_key", "API key"], ["api_secret", "API secret"]]) {
    if (!request[name]) throw new Error(`Fill in the ${label}.`);
  }
  if (!request.erp_url.startsWith("https://")) throw new Error("The ERPNext address must start with https://");
  const extra = text("paymentExtra");
  if (extra) {
    let parsed;
    try {
      parsed = JSON.parse(extra);
    } catch {
      parsed = null;
    }
    const valid = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      && Object.values(parsed).every((v) => typeof v === "string");
    if (!valid) throw new Error('Extra payment fields must look like {"field": "value"}.');
    request.payment_extra = parsed;
  }
  return request;
}

const ACTION_ORDER = { ASK: 0, HOLD: 1, PAY: 2 };

/** What the agent decided last time, ready to show: questions first, then holds, then payments. */
export function lastRunView(lastRun) {
  if (!lastRun) return { state: "waiting", text: "The agent has not finished its first check yet." };
  if (!lastRun.ok) return { state: "error", text: `The last check failed: ${lastRun.error}`, at: lastRun.at };
  const decisions = [...(lastRun.decisions ?? [])].sort(
    (a, b) => (ACTION_ORDER[a.action] ?? 9) - (ACTION_ORDER[b.action] ?? 9) || a.invoice.localeCompare(b.invoice),
  );
  const c = lastRun.counts ?? {};
  const text = decisions.length
    ? `${decisions.length} unpaid invoices: ${c.pay ?? 0} to pay, ${c.ask ?? 0} need you, ${c.held ?? 0} held, ${c.deferred ?? 0} waiting for budget.`
    : "No unpaid invoices.";
  return { state: "ok", text, at: lastRun.at, decisions };
}

/** The shop's agent wallet is not the agent of the contract yet. */
export function needsSetAgent(agentWallet, contractAgent) {
  return Boolean(agentWallet) && String(agentWallet).toLowerCase() !== String(contractAgent ?? "").toLowerCase();
}

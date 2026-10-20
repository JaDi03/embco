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

export async function call(path, { method = "GET", body, fetchImpl = fetch } = {}) {
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

/** Sign-In with Ethereum (EIP-4361): the hub writes the message, the wallet signs it as is. One
 * signature covers every shop of the wallet, and creating a new one. */
export async function signIn(signer, fetchImpl = fetch) {
  const address = await signer.getAddress();
  const request = await call(`${HUB_PATH}/owners/sign-in-request`, { method: "POST", body: { address }, fetchImpl });
  const signature = await signer.signMessage(request.message);
  return call(`${HUB_PATH}/owners/sign-in`, {
    method: "POST",
    body: { nonce: request.nonce, signature },
    fetchImpl,
  });
}

/** The agent wallets of the owner's next shop ({agent, signer}): its contract is created with
 * them. Signs in first when the owner has not yet. */
export async function newAgentWallets(signer, fetchImpl = fetch) {
  const ask = () => call(`${HUB_PATH}/owners/agent-wallets`, { method: "POST", fetchImpl });
  try {
    return await ask();
  } catch (error) {
    if (!(error instanceof HubError && error.status === 401)) throw error;
  }
  await signIn(signer, fetchImpl);
  return ask();
}

/** The contract's agent and the wallet that signs the owner's Unified Balance spends; null when
 * the owner has to sign in first. */
export async function agentWallets(shop, fetchImpl = fetch) {
  try {
    return await call(`${shopPath(shop)}/agent`, { fetchImpl });
  } catch (error) {
    if (error instanceof HubError && error.status === 401) return null;
    throw error;
  }
}

/** Who the owner authorizes in Gateway: the agent's signer, or for an agent wallet that is not
 * ours, the agent itself. */
export function delegateOf(wallets, contractAgent) {
  if (!wallets) return null;
  return wallets.ours ? wallets.signer : contractAgent;
}

/** The shop as the hub knows it, or null when the owner has to sign in first. */
/** Where the owner's USDC was after each check (points, oldest first) and every move in and out
 * of Earn (earn_moves); empty when signed out. */
export async function balances(shop, fetchImpl = fetch) {
  try {
    const body = await call(`${shopPath(shop)}/balances`, { fetchImpl });
    return { points: body.points ?? [], earn_moves: body.earn_moves ?? [] };
  } catch (error) {
    if (error instanceof HubError && error.status === 401) return { points: [], earn_moves: [] };
    throw error;
  }
}

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

/** The owner's answer to the agent's question, tied to the exact question shown. */
export const answer = (shop, { invoice, verdict, fingerprint, note = "" }, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/answers`, { method: "POST", body: { invoice, verdict, fingerprint, note }, fetchImpl });

/** Ask the shop's agent to check now instead of at its next scheduled check. */
export const checkNow = (shop, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/check`, { method: "POST", fetchImpl });

/** Turn the whole agent on or off: its process, and with it Claude and every payment. */
export const setAgent = (shop, on, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/agent`, { method: "POST", body: { on }, fetchImpl });

/**
 * What the Agent card says, from the agent's process (null when the service cannot tell, e.g.
 * not signed in) and the contract's pause. The agent is on only when both let it work.
 */
export function agentSwitchView(serviceOn, paused) {
  if (serviceOn === false && paused) return { on: false, status: "Off", note: "" };
  if (serviceOn === false) {
    return { on: false, status: "Off", note: "Payments are still allowed in the contract." };
  }
  if (paused) {
    return { on: false, status: "Off", note: "Payments are paused in the contract." };
  }
  return { on: true, status: "On", note: serviceOn === null ? "Sign in to the embco service to turn it off fully." : "" };
}

/**
 * Draft mode leaves each payment in ERPNext as a draft marked TESTNET. Turning it off needs
 * `confirm`: the service refuses it otherwise, since test USDC would then close real invoices.
 */
export const setDraftMode = (shop, draft, confirm = false, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/draft-mode`, { method: "POST", body: { draft, confirm }, fetchImpl });

/** What the page says about draft mode; nothing until the shop's ERP is connected. */
export function draftModeView(shop) {
  if (!shop?.connected) return null;
  if (shop.draft_mode !== false) {
    return {
      draft: true,
      badge: "Draft mode",
      badgeClass: "tag-mode draft",
      note: "Each payment stays in ERPNext as a draft marked TESTNET. It closes no invoice.",
      action: "Turn draft mode off",
    };
  }
  return {
    draft: false,
    badge: "Submitting to ERPNext",
    badgeClass: "tag-mode submitted",
    note: "Each payment is submitted in ERPNext and closes its invoice, even when it was paid in test USDC.",
    action: "Turn draft mode on",
  };
}

/** A one-time code to send to the Telegram bot as /link <code>. */
export const telegramCode = (shop, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/telegram`, { method: "POST", fetchImpl });

export const telegramUnlink = (shop, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/telegram`, { method: "DELETE", fetchImpl });

/** What to tell the owner to send, and to whom. */
export function linkInstruction({ code, bot }) {
  const to = bot ? `@${bot}` : "your embco bot";
  return `In Telegram, send this to ${to} within 10 minutes: /link ${code}`;
}

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

/** How the agent chooses between idle USDC on other chains and Arc Earn when an invoice is due. */
export const setLiquidityStrategy = (shop, strategy, fetchImpl = fetch) =>
  call(`${shopPath(shop)}/liquidity-strategy`, { method: "POST", body: { strategy }, fetchImpl });

// The owner's Unified Balance (Circle Gateway): USDC kept on other chains, which the agent can
// bring to the owner's wallet on Arc when an invoice needs it. Here the owner deposits into it and
// authorizes the agent on a chain, with their own browser wallet, through Circle's Unified
// Balance Kit. The kit is loaded only when the owner does one of these.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { FUNDING_FACTORY_ABI, GATEWAY_WALLET_ABI, USDC_ABI } from "./abi.js";
import { FUNDING_FACTORIES, GATEWAY_WALLET_TESTNET } from "./config.js";
import { AUTHORIZE_ALL, formatUsdc, parseUsdc } from "./units.js";

const KIT = "./vendor/unified-balance-kit-1.8.0/index.js";
let loaded = null;

/** The kit's six-decimal text as exact units; zero, or anything not an amount, is 0n. */
function units(text) {
  try {
    return parseUsdc(text);
  } catch {
    return 0n;
  }
}

function kit() {
  loaded ??= import(KIT);
  return loaded;
}

/** What the page shows, from the agent's last check: each chain, what is there, and whether the
 * agent may spend it. The agent reads it with Circle's kit, so only chains it can sign on. */
export function unifiedView(lastRun) {
  const view = lastRun?.unified_balance;
  if (!view) return { state: "off", text: "Your agent shows this once it is connected and payments are on.", rows: [] };
  if (view.error) return { state: "error", text: `Could not read it: ${view.error}`, rows: [] };
  const rows = (view.chains ?? []).map((c) => ({
    chain: c.chain,
    name: c.chain.replaceAll("_", " "),
    available: units(c.available),
    arriving: units(c.arriving),
    authorized: Boolean(c.agent_authorized),
  }));
  const total = rows.reduce((sum, r) => sum + r.available, 0n);
  const usable = rows.filter((r) => r.authorized).reduce((sum, r) => sum + r.available, 0n);
  const text = total
    ? `${formatUsdc(total)} USDC on other chains; the agent may bring ${formatUsdc(usable)} of it to your wallet on Arc.`
    : "No USDC on other chains yet. Deposit on a chain, then authorize the agent there.";
  return { state: "ok", text, rows };
}

/** The connected wallet on the chain, through the kit's viem adapter. */
async function adapterOn(provider, chain) {
  const { createViemAdapterFromProvider, resolveChainIdentifier } = await kit();
  const definition = resolveChainIdentifier(chain);
  const adapter = await createViemAdapterFromProvider({ provider, capabilities: { supportedChains: [definition] } });
  await adapter.ensureChain(definition);
  return adapter;
}

/** Move USDC from the owner's wallet on the chain into their Unified Balance. */
export async function deposit(provider, chain, amount) {
  const { UnifiedBalanceKit } = await kit();
  const adapter = await adapterOn(provider, chain);
  return new UnifiedBalanceKit().deposit({ from: { adapter, chain }, amount, token: "USDC" });
}

/** The chains where the owner can let the agent move funds: those with a funding factory. */
export function fundingChains(factories = FUNDING_FACTORIES) {
  return Object.entries(factories).filter(([, address]) => address).map(([chain]) => chain);
}

/** The owner's wallets on other chains, from the agent's last check. */
export function walletsView(lastRun) {
  const wallets = lastRun?.unified_balance?.wallets;
  if (!Array.isArray(wallets)) return [];
  return wallets.map((w) => ({
    chain: w.chain,
    name: w.chain.replaceAll("_", " "),
    inWallet: units(w.in_wallet),
    setUp: Boolean(w.funding),
    agentMayMove: units(w.agent_may_move),
  }));
}

const BATCH_POLL_MS = 2000;
const BATCH_WAIT_MS = 180000;

/** The calls that let the agent move funds on a chain, in order: create the owner's ShopFunding
 * (skipped when it exists), approve it, and authorize the agent in Gateway (skipped when it is). */
export function setupCalls({ factory, funding, create, usdc, gateway, agent, maxPerMove, weeklyCap, delegate }) {
  const calls = [];
  if (create) {
    const data = new ethers.Interface(FUNDING_FACTORY_ABI).encodeFunctionData("create", [agent, maxPerMove, weeklyCap]);
    calls.push({ to: factory, data, value: "0x0" });
  }
  calls.push({ to: usdc, data: new ethers.Interface(USDC_ABI).encodeFunctionData("approve", [funding, AUTHORIZE_ALL]), value: "0x0" });
  if (delegate) {
    calls.push({ to: gateway, data: new ethers.Interface(GATEWAY_WALLET_ABI).encodeFunctionData("addDelegate", [usdc, agent]), value: "0x0" });
  }
  return calls;
}

/** Wait for a batch sent with wallet_sendCalls (EIP-5792): 200 is confirmed, 400 and up failed. */
async function waitForCalls(provider, id) {
  for (let waited = 0; waited < BATCH_WAIT_MS; waited += BATCH_POLL_MS) {
    const status = await provider.request({ method: "wallet_getCallsStatus", params: [id] });
    if (status.status === 200) return status;
    if (status.status >= 400) throw new Error(`The wallet could not complete it (status ${status.status}).`);
    await new Promise((resolve) => setTimeout(resolve, BATCH_POLL_MS));
  }
  throw new Error("Still waiting for the chain; check your wallet's activity, then look again.");
}

/** Let the agent move the owner's USDC on this chain into the owner's Unified Balance, within
 * limits: create the owner's ShopFunding there, approve it, and authorize the agent in Gateway.
 * One confirmation when the wallet batches calls (EIP-5792, MetaMask's smart account through
 * EIP-7702); otherwise one per step. */
export async function setUpChain(provider, chain, { agent, maxPerMove, weeklyCap }, factories = FUNDING_FACTORIES) {
  const { UnifiedBalanceKit, resolveChainIdentifier } = await kit();
  const definition = resolveChainIdentifier(chain);
  const adapter = await adapterOn(provider, chain);  // the wallet is now on that chain
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const factory = new ethers.Contract(factories[chain], FUNDING_FACTORY_ABI, browser);
  const existing = (await factory.fundingsOf(owner)).at(-1);
  const funding = existing ?? (await factory.predict(owner, agent, maxPerMove, weeklyCap));
  const delegate = await new UnifiedBalanceKit().getDelegateStatus({ from: { adapter, chain }, delegateAddress: agent });
  const calls = setupCalls({
    factory: factories[chain], funding, create: !existing, usdc: definition.usdcAddress,
    gateway: GATEWAY_WALLET_TESTNET, agent, maxPerMove, weeklyCap, delegate: delegate === "none",
  });
  const chainId = ethers.toQuantity(definition.chainId);
  const capabilities = await provider.request({ method: "wallet_getCapabilities", params: [owner, [chainId]] })
    .catch(() => ({}));
  const atomic = capabilities?.[chainId]?.atomic?.status;
  if (atomic === "supported" || atomic === "ready") {
    const sent = await provider.request({
      method: "wallet_sendCalls",
      params: [{ version: "2.0.0", from: owner, chainId, atomicRequired: true, calls }],
    });
    await waitForCalls(provider, sent.id ?? sent.batchId);
  } else {
    for (const call of calls) await (await signer.sendTransaction({ to: call.to, data: call.data })).wait();
  }
  return funding;
}

/** Let the agent's wallet spend the owner's Unified Balance on this chain (only to the owner). */
export async function authorizeAgent(provider, chain, agent) {
  const { UnifiedBalanceKit } = await kit();
  const adapter = await adapterOn(provider, chain);
  return new UnifiedBalanceKit().addDelegate({ from: { adapter, chain }, delegateAddress: agent });
}

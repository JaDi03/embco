// The owner's Unified Balance (Circle Gateway): USDC kept on other chains, which the agent can
// bring to the owner's wallet on Arc when an invoice needs it. Here the owner deposits into it and
// authorizes the agent on a chain, with their own browser wallet, through Circle's Unified
// Balance Kit. The kit is loaded only when the owner does one of these.

import { formatUsdc, parseUsdc } from "./units.js";

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

/** Let the agent's wallet spend the owner's Unified Balance on this chain (only to the owner). */
export async function authorizeAgent(provider, chain, agent) {
  const { UnifiedBalanceKit } = await kit();
  const adapter = await adapterOn(provider, chain);
  return new UnifiedBalanceKit().addDelegate({ from: { adapter, chain }, delegateAddress: agent });
}

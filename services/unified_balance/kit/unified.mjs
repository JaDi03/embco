// The shop owner's Unified Balance (Circle Gateway), read and spent with Circle's Unified
// Balance Kit. The agent's Circle wallet signs spends as the owner's delegate, which the owner
// authorizes once per chain from the dashboard. A spend only ever mints to the owner's own
// wallet on Arc: the shop contract pays suppliers from there, within its limits.

import {
  UnifiedBalanceChain,
  UnifiedBalanceKit,
  getErrorMessage,
  resolveChainIdentifier,
} from "@circle-fin/unified-balance-kit";
import { createCircleWalletsAdapter, toCircleBlockchain } from "@circle-fin/adapter-circle-wallets";
import { initiateDeveloperControlledWalletsClient } from "@circle-fin/developer-controlled-wallets";

export const DESTINATION = "Arc_Testnet";

/** Chains the owner can hold a Unified Balance on and the agent's Circle wallet can sign on:
 * the kit's EVM testnets that Circle wallets support, Arc aside (it is where the money goes). */
export function sourceChains() {
  return Object.values(UnifiedBalanceChain).filter((name) => {
    if (name === DESTINATION) return false;
    const chain = resolveChainIdentifier(name);
    if (!chain.isTestnet || chain.type !== "evm") return false;
    try {
      toCircleBlockchain(name);
      return true;
    } catch {
      return false;
    }
  });
}

/** A spend from the owner's balance, signed by the agent, minted to the owner on Arc. */
export function spendParams({ adapter, owner, agent, amount, allocations }) {
  return {
    amount,
    token: "USDC",
    from: [{ adapter, address: agent, sourceAccount: owner, allocations }],
    to: { chain: DESTINATION, recipientAddress: owner, useForwarder: true },
  };
}

function circle(env) {
  return { apiKey: env.CIRCLE_API_KEY, entitySecret: env.CIRCLE_ENTITY_SECRET };
}

/** What the owner holds on each chain, and whether the agent may spend it there yet. */
async function balances({ owner, agent }, env) {
  const kit = new UnifiedBalanceKit();
  const adapter = createCircleWalletsAdapter(circle(env));
  const chains = sourceChains();
  const read = await kit.getBalances({
    sources: { address: owner, chains },
    networkType: "testnet",
    includePending: true,
  });
  const held = new Map();
  for (const depositor of read.breakdown ?? []) {
    for (const entry of depositor.breakdown ?? []) held.set(entry.chain, entry);
  }
  const rows = [];
  for (const chain of chains) {
    const entry = held.get(chain) ?? {};
    const delegate = await kit.getDelegateStatus({
      from: { adapter, chain, address: owner },
      delegateAddress: agent,
    });
    rows.push({
      chain,
      confirmed: entry.confirmedBalance ?? "0",
      pending: entry.pendingBalance ?? "0",
      delegate,
    });
  }
  return { chains: rows };
}

/** The agent's Circle wallet on every source chain, at the same address as on Arc: the
 * Circle wallets adapter signs with the wallet it finds for that address and chain. */
async function prepare({ agent }, env) {
  const client = initiateDeveloperControlledWalletsClient(circle(env));
  const ready = [];
  for (const chain of sourceChains()) {
    await client.deriveWalletByAddress({
      sourceBlockchain: toCircleBlockchain(DESTINATION),
      walletAddress: agent,
      targetBlockchain: toCircleBlockchain(chain),
    });
    ready.push(chain);
  }
  return { chains: ready };
}

async function estimate(input, env) {
  const kit = new UnifiedBalanceKit();
  const adapter = createCircleWalletsAdapter(circle(env));
  return kit.estimateSpend(spendParams({ adapter, ...input }));
}

async function spend(input, env) {
  const kit = new UnifiedBalanceKit();
  const adapter = createCircleWalletsAdapter(circle(env));
  return kit.spend(spendParams({ adapter, ...input }));
}

export const COMMANDS = { balances, prepare, estimate, spend, chains: async () => ({ chains: sourceChains() }) };

/** Run one command; the answer is plain data, or the kit's own error message. */
export async function run(command, input, env) {
  const handler = COMMANDS[command];
  if (!handler) return { error: `unknown command ${command}` };
  try {
    return { ok: await handler(input, env) };
  } catch (error) {
    return { error: getErrorMessage(error) };
  }
}

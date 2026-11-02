// What embco agents have paid for real businesses on Arc, read only from public chain data: the
// shops the factory created and the Paid events of each shop. Pure functions here; stats-app.js
// fetches and renders.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { FACTORY_ABI, SHOP_ABI } from "./abi.js";

const factoryIface = new ethers.Interface(FACTORY_ABI);
const shopIface = new ethers.Interface(SHOP_ABI);
const DAY = 24 * 3600 * 1000;

function parse(iface, log) {
  try {
    return iface.parseLog({ topics: (log.topics ?? []).filter(Boolean), data: log.data ?? "0x" });
  } catch {
    return null;
  }
}

/** The factory's logs as shops, oldest first: [{ shop, owner, agent, createdAt }]. */
export function shopsFromFactory(logs) {
  const shops = [];
  for (const log of logs) {
    const parsed = parse(factoryIface, log);
    if (parsed?.name !== "ShopCreated") continue;
    shops.push({
      shop: parsed.args.shop.toLowerCase(),
      owner: parsed.args.owner.toLowerCase(),
      agent: parsed.args.agent.toLowerCase(),
      createdAt: log.block_timestamp ?? null,
      block: log.block_number,
    });
  }
  return shops.sort((a, b) => a.block - b.block);
}

/** One shop's Paid events, newest first: [{ amount (6-decimal bigint), byAgent, at, tx }].
 * A payment is the agent's when the owner did not sign it: the contract lets only those two pay. */
export function paymentsFromShop(logs, owner) {
  const payments = [];
  for (const log of logs) {
    const parsed = parse(shopIface, log);
    if (parsed?.name !== "Paid") continue;
    payments.push({
      amount: parsed.args.amount,
      byAgent: parsed.args.by.toLowerCase() !== owner,
      at: log.block_timestamp ?? null,
      tx: log.transaction_hash,
      block: log.block_number,
      index: log.index,
    });
  }
  return payments.sort((a, b) => b.block - a.block || b.index - a.index);
}

/**
 * The page's numbers, for the real businesses only. `histories` maps a shop to its raw logs;
 * shops not in `real` are left out. Payments are the agent's, newest first, with their shop.
 */
export function summarize(shops, histories, real) {
  const isReal = new Set(real.map((a) => a.toLowerCase()));
  const payments = shops.filter((s) => isReal.has(s.shop))
    .flatMap((s) => paymentsFromShop(histories.get(s.shop) ?? [], s.owner)
      .filter((p) => p.byAgent).map((p) => ({ ...p, shop: s.shop })))
    .sort((a, b) => b.block - a.block || b.index - a.index);
  return {
    shops: shops.filter((s) => isReal.has(s.shop)).length,
    payments,
    usdc: payments.reduce((sum, p) => sum + p.amount, 0n),
  };
}

/** USDC paid per UTC day, from the first payment to `today`, days without payments included:
 * [{ day: "2026-10-14", usdc (bigint), count }], oldest first. */
export function dailyTotals(payments, today = new Date()) {
  const dated = payments.filter((p) => p.at);
  if (!dated.length) return [];
  const byDay = new Map();
  for (const p of dated) {
    const day = p.at.slice(0, 10);
    const sum = byDay.get(day) ?? { usdc: 0n, count: 0 };
    byDay.set(day, { usdc: sum.usdc + p.amount, count: sum.count + 1 });
  }
  const first = Date.parse(`${[...byDay.keys()].sort()[0]}T00:00:00Z`);
  const last = Math.max(Date.parse(`${today.toISOString().slice(0, 10)}T00:00:00Z`), first);
  const days = [];
  for (let t = first; t <= last; t += DAY) {
    const day = new Date(t).toISOString().slice(0, 10);
    days.push({ day, ...(byDay.get(day) ?? { usdc: 0n, count: 0 }) });
  }
  return days;
}

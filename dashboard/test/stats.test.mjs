import assert from "node:assert/strict";
import { test } from "node:test";

import { ethers } from "../vendor/ethers-6.17.0.min.js";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const { FACTORY_ABI, SHOP_ABI } = await import("../abi.js");
const { dailyTotals, paymentsFromShop, shopsFromFactory, summarize } = await import("../stats.js");

const factoryIface = new ethers.Interface(FACTORY_ABI);
const shopIface = new ethers.Interface(SHOP_ABI);
const OWNER_A = "0x1111111111111111111111111111111111111111";
const OWNER_B = "0x5555555555555555555555555555555555555555";
const AGENT = "0x2222222222222222222222222222222222222222";
const SHOP_A = "0x00000000000000000000000000000000000000aa";
const SHOP_B = "0x00000000000000000000000000000000000000bb";
const SUPPLIER = "0x3333333333333333333333333333333333333333";

/** A log shaped like the explorer's: topics padded with null, with its block time. */
function log(iface, name, args, block, index = 0) {
  const { topics, data } = iface.encodeEventLog(iface.getEvent(name), args);
  return {
    topics: [...topics, null, null, null].slice(0, 4), data, block_number: block, index,
    transaction_hash: `0xtx${block}`, block_timestamp: `2026-10-${String(block).padStart(2, "0")}T12:00:00.000000Z`,
  };
}

const created = (owner, shop, block) => log(factoryIface, "ShopCreated", [owner, shop, AGENT, 100_000_000n, 500_000_000n], block);
const paid = (ref, amount, by, block) => log(shopIface, "Paid", [ethers.id(ref), SUPPLIER, amount, by], block);

test("the factory's logs become shops, oldest first", () => {
  const shops = shopsFromFactory([created(OWNER_B, SHOP_B, 9), created(OWNER_A, SHOP_A, 3)]);
  assert.deepEqual(shops.map((s) => s.shop), [SHOP_A, SHOP_B]);
  assert.equal(shops[0].owner, OWNER_A);
  assert.equal(shops[0].createdAt, "2026-10-03T12:00:00.000000Z");
});

test("a payment the owner signed is not the agent's", () => {
  const payments = paymentsFromShop([
    paid("ACC-PINV-1", 12_500_000n, AGENT, 5),
    paid("ACC-PINV-2", 7_000_000n, OWNER_A, 6),
  ], OWNER_A);
  assert.deepEqual(payments.map((p) => [p.amount, p.byAgent]), [[7_000_000n, false], [12_500_000n, true]]);
});

test("only the agent's payments for real businesses are counted", () => {
  const shops = shopsFromFactory([created(OWNER_A, SHOP_A, 1), created(OWNER_B, SHOP_B, 2)]);
  const histories = new Map([
    [SHOP_A, [paid("A-1", 100_250_000n, AGENT, 4), paid("A-2", 50_000_000n, OWNER_A, 5), paid("A-3", 1_000_000n, AGENT, 8)]],
    [SHOP_B, [paid("B-1", 900_000_000n, AGENT, 6)]],
  ]);
  const stats = summarize(shops, histories, [SHOP_A.toUpperCase().replace("0X", "0x")]);
  assert.equal(stats.shops, 1);
  assert.equal(stats.usdc, 101_250_000n);
  assert.deepEqual(stats.payments.map((p) => [p.amount, p.shop]), [[1_000_000n, SHOP_A], [100_250_000n, SHOP_A]]);
});

test("no real shop means nothing counted", () => {
  const shops = shopsFromFactory([created(OWNER_A, SHOP_A, 1)]);
  const stats = summarize(shops, new Map([[SHOP_A, [paid("A-1", 5_000_000n, AGENT, 2)]]]), []);
  assert.deepEqual(stats, { shops: 0, payments: [], usdc: 0n });
});

test("days are summed in UTC from the first payment to today, empty days included", () => {
  const payments = [
    { amount: 30_160_000n, at: "2026-10-23T23:04:00.000000Z" },
    { amount: 1_097_320_000n, at: "2026-10-23T22:48:00.000000Z" },
    { amount: 1_800_000n, at: "2026-10-25T20:55:00.000000Z" },
  ];
  const days = dailyTotals(payments, new Date("2026-10-26T09:00:00Z"));
  assert.deepEqual(days, [
    { day: "2026-10-23", usdc: 1_127_480_000n, count: 2 },
    { day: "2026-10-24", usdc: 0n, count: 0 },
    { day: "2026-10-25", usdc: 1_800_000n, count: 1 },
    { day: "2026-10-26", usdc: 0n, count: 0 },
  ]);
  assert.deepEqual(dailyTotals([]), []);
});

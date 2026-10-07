import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const { parseApprovals, pendingApprovals } = await import("../approvals.js");

const SHOP = "0x628ed6e6940F14043876a1255Da5506939A46AbC";
const A = "0x213793c4a7bddB9D5d37B2C0d964BED707eE0379";
const B = "0x4444444444444444444444444444444444444444";
const body = (pending, shop = SHOP) => ({ format: 1, shop, updated_at: "2026-10-07T06:00:00+00:00", pending });

test("pending invoices are grouped by wallet with exact amounts", () => {
  const parsed = parseApprovals(
    body([
      { wallet: A.toLowerCase(), invoice: "ACC-PINV-2026-00030", amount: "2.5" },
      { wallet: A, invoice: "ACC-PINV-2026-00031", amount: "0.000001" },
      { wallet: B, invoice: "ACC-PINV-2026-00032", amount: "10" },
    ]),
    SHOP.toLowerCase(),
  );
  assert.deepEqual(parsed, [
    { wallet: A, invoices: [{ invoice: "ACC-PINV-2026-00030", amount: 2_500_000n }, { invoice: "ACC-PINV-2026-00031", amount: 1n }] },
    { wallet: B, invoices: [{ invoice: "ACC-PINV-2026-00032", amount: 10_000_000n }] },
  ]);
});

test("a file for another shop, another format or malformed entries shows nothing", () => {
  const entry = { wallet: A, invoice: "X", amount: "1" };
  assert.deepEqual(parseApprovals(body([entry], B), SHOP), []);
  assert.deepEqual(parseApprovals({ ...body([entry]), format: 2 }, SHOP), []);
  assert.deepEqual(parseApprovals(null, SHOP), []);
  const bad = [
    { wallet: "0x123", invoice: "X", amount: "1" },
    { wallet: A, invoice: "", amount: "1" },
    { wallet: A, invoice: "X", amount: "1e3" },
    { wallet: A, invoice: "X", amount: "-1" },
    { wallet: A, invoice: "X".repeat(141), amount: "1" },
  ];
  assert.deepEqual(parseApprovals(body(bad), SHOP), []);
});

test("wallets the contract already approves are left out, and no file means none", async () => {
  const file = body([{ wallet: A, invoice: "X", amount: "1" }, { wallet: B, invoice: "Y", amount: "1" }]);
  const fetchFile = async () => ({ ok: true, status: 200, json: async () => file });
  const pending = await pendingApprovals(SHOP, async (w) => w === A, fetchFile);
  assert.deepEqual(pending.map((p) => p.wallet), [B]);
  const missing = async () => ({ ok: false, status: 404 });
  assert.deepEqual(await pendingApprovals(SHOP, async () => false, missing), []);
});

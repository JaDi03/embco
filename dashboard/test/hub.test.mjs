import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const hub = await import("../hub.js");

const SHOP = "0x628ed6e6940F14043876a1255Da5506939A46AbC";
const TYPED = {
  types: {
    EIP712Domain: [{ name: "name", type: "string" }],
    SignIn: [{ name: "statement", type: "string" }, { name: "nonce", type: "string" }],
  },
  primaryType: "SignIn",
  domain: { name: "embco", version: "1", chainId: 5042002 },
  message: { statement: "Sign in", nonce: "n-1" },
};

function fakeFetch(routes) {
  const calls = [];
  const impl = async (path, options) => {
    calls.push({ path, ...options, body: options.body ? JSON.parse(options.body) : undefined });
    const [status, body] = routes[`${options.method} ${path}`] ?? [404, { error: "not found" }];
    return { ok: status < 400, status, json: async () => body };
  };
  return { impl, calls };
}

test("ethers gets the types without the domain type", () => {
  const { domain, types, value } = hub.forEthers(TYPED);
  assert.deepEqual(Object.keys(types), ["SignIn"]);
  assert.equal(domain.chainId, 5042002);
  assert.equal(value.nonce, "n-1");
});

test("signing in sends back the nonce with the owner's signature, on this site only", async () => {
  const shop = SHOP.toLowerCase();
  const { impl, calls } = fakeFetch({
    [`POST /api/shops/${shop}/sign-in-request`]: [200, TYPED],
    [`POST /api/shops/${shop}/sign-in`]: [200, { owner: "0xOwner" }],
  });
  const signer = { signTypedData: async (domain, types, value) => `sig:${value.nonce}:${Object.keys(types)}` };
  assert.deepEqual(await hub.signIn(signer, SHOP, impl), { owner: "0xOwner" });
  assert.deepEqual(calls[1].body, { nonce: "n-1", signature: "sig:n-1:SignIn" });
  assert.ok(calls.every((c) => c.credentials === "same-origin"));
});

test("without a session the status is null, other errors are thrown with their details", async () => {
  const shop = SHOP.toLowerCase();
  const signedOut = fakeFetch({ [`GET /api/shops/${shop}`]: [401, { error: "sign in" }] });
  assert.equal(await hub.status(SHOP, signedOut.impl), null);
  const probe = { ok: false, checks: [{ name: "keys", ok: false, detail: "HTTP 401", required: true }] };
  const refused = fakeFetch({ [`POST /api/shops/${shop}/erp`]: [422, { error: "the ERP keys cannot do what the agent needs", probe }] });
  await assert.rejects(hub.connectErp(SHOP, {}, refused.impl), (error) => {
    assert.equal(error.status, 422);
    assert.deepEqual(error.details.probe, probe);
    return true;
  });
});

test("the connect form is checked before anything is sent", () => {
  const fields = { erpUrl: " https://shop.frappe.cloud ", company: "Shop", apiKey: "k", apiSecret: "s", walletBank: "USDC on Arc" };
  assert.deepEqual(hub.connectRequest(fields), {
    erp_url: "https://shop.frappe.cloud", company: "Shop", api_key: "k", api_secret: "s",
    wallet_bank: "USDC on Arc", payment_extra: {},
  });
  assert.equal(hub.connectRequest({ ...fields, walletBank: "" }).wallet_bank, null);
  assert.deepEqual(hub.connectRequest({ ...fields, paymentExtra: '{"payment_form": "03"}' }).payment_extra, { payment_form: "03" });
  assert.throws(() => hub.connectRequest({ ...fields, apiSecret: "" }), /API secret/);
  assert.throws(() => hub.connectRequest({ ...fields, erpUrl: "http://shop" }), /https/);
  for (const bad of ["03", "[1]", '{"a": 3}', "{oops"]) {
    assert.throws(() => hub.connectRequest({ ...fields, paymentExtra: bad }), /Extra payment fields/);
  }
});

test("the agent's decisions show questions first, then holds, then payments", () => {
  const view = hub.lastRunView({
    ok: true, at: "2026-10-08T06:26:49+00:00",
    counts: { pay: 1, ask: 1, held: 1, deferred: 0 },
    decisions: [
      { invoice: "B", action: "PAY", reasons: [] },
      { invoice: "C", action: "HOLD", reasons: ["no wallet"] },
      { invoice: "A", action: "ASK", reasons: ["new wallet"] },
    ],
  });
  assert.deepEqual(view.decisions.map((d) => d.action), ["ASK", "HOLD", "PAY"]);
  assert.match(view.text, /3 unpaid invoices: 1 to pay, 1 need you, 1 held/);
  assert.equal(hub.lastRunView(null).state, "waiting");
  const failed = hub.lastRunView({ ok: false, at: "x", error: "ERPNext returned HTTP 401" });
  assert.equal(failed.state, "error");
  assert.match(failed.text, /401/);
});

test("the owner is asked to set the agent only when the contract has another one", () => {
  const wallet = "0x2309eD4A43e3b1AB4222F6938538a6C88BD27DD8";
  assert.equal(hub.needsSetAgent(wallet, wallet.toLowerCase()), false);
  assert.equal(hub.needsSetAgent(wallet, "0x0000000000000000000000000000000000000000"), true);
  assert.equal(hub.needsSetAgent(null, "0x0000000000000000000000000000000000000000"), false);
});

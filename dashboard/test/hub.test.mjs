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

test("signing in signs the hub's EIP-4361 message as is and sends back its nonce, on this site only", async () => {
  const OWNER = "0x2137" + "0".repeat(32) + "0379";
  const message = "app.embco.xyz wants you to sign in with your Ethereum account:\n" + OWNER;
  const { impl, calls } = fakeFetch({
    "POST /api/owners/sign-in-request": [200, { message, nonce: "n1n1n1n1" }],
    "POST /api/owners/sign-in": [200, { owner: OWNER }],
  });
  const signer = { getAddress: async () => OWNER, signMessage: async (text) => `sig:${text.length}` };
  assert.deepEqual(await hub.signIn(signer, impl), { owner: OWNER });
  assert.deepEqual(calls[0].body, { address: OWNER });
  assert.deepEqual(calls[1].body, { nonce: "n1n1n1n1", signature: `sig:${message.length}` });
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

test("the agent is on only when its process runs and the contract allows payments", () => {
  assert.deepEqual(hub.agentSwitchView(true, false), { on: true, status: "On", note: "" });
  assert.equal(hub.agentSwitchView(false, true).on, false);
  assert.match(hub.agentSwitchView(false, false).note, /still allowed in the contract/);
  assert.match(hub.agentSwitchView(true, true).note, /paused in the contract/);
  assert.match(hub.agentSwitchView(null, false).note, /Sign in/);
});

test("turning the agent off or on is one call with the owner's choice", async () => {
  const { impl, calls } = fakeFetch({
    [`POST /api/shops/${SHOP.toLowerCase()}/agent`]: [200, { agent_on: false }],
  });
  assert.deepEqual(await hub.setAgent(SHOP, false, impl), { agent_on: false });
  assert.deepEqual(calls[0].body, { on: false });
  assert.equal(calls[0].credentials, "same-origin");
});

test("draft mode is shown once the ERP is connected, and on unless the owner turned it off", () => {
  assert.equal(hub.draftModeView({ connected: false }), null);
  assert.equal(hub.draftModeView(null), null);
  const draft = hub.draftModeView({ connected: true, draft_mode: true });
  assert.equal(draft.draft, true);
  assert.match(draft.badge, /Draft mode/);
  assert.match(draft.note, /TESTNET/);
  assert.equal(hub.draftModeView({ connected: true }).draft, true);  // an older service: drafts
  const submitted = hub.draftModeView({ connected: true, draft_mode: false });
  assert.equal(submitted.draft, false);
  assert.match(submitted.badgeClass, /submitted/);
  assert.match(submitted.note, /closes its invoice/);
});

test("leaving draft mode sends the owner's confirmation", async () => {
  const { impl, calls } = fakeFetch({
    [`POST /api/shops/${SHOP.toLowerCase()}/draft-mode`]: [200, { draft_mode: false }],
  });
  assert.deepEqual(await hub.setDraftMode(SHOP, false, true, impl), { draft_mode: false });
  assert.deepEqual(calls[0].body, { draft: false, confirm: true });
  assert.equal(calls[0].credentials, "same-origin");
});

test("linking Telegram tells the owner exactly what to send, and to which bot", () => {
  assert.equal(hub.linkInstruction({ code: "ABCD-EFGH", bot: "embco_agent_bot" }),
    "In Telegram, send this to @embco_agent_bot within 10 minutes: /link ABCD-EFGH");
  assert.match(hub.linkInstruction({ code: "ABCD-EFGH", bot: null }), /your embco bot/);
});

test("a new shop's agent wallets sign the owner in once when needed, and only then", async () => {
  const OWNER = "0x2137" + "0".repeat(32) + "0379";
  const wallets = { agent: "0x" + "5c".repeat(20), signer: "0x" + "e0".repeat(20) };
  let signedIn = false;
  const signatures = [];
  const answer = (status, body) => ({ ok: status < 400, status, json: async () => body });
  const impl = async (path) => {
    if (path === "/api/owners/sign-in-request") return answer(200, { message: "m", nonce: "n" });
    if (path === "/api/owners/sign-in") {
      signedIn = true;
      return answer(200, { owner: OWNER });
    }
    if (path === "/api/owners/agent-wallets") return signedIn ? answer(200, wallets) : answer(401, { error: "sign in" });
    return answer(404, {});
  };
  const signer = { getAddress: async () => OWNER, signMessage: async (text) => signatures.push(text) };
  assert.deepEqual(await hub.newAgentWallets(signer, impl), wallets);
  assert.deepEqual(await hub.newAgentWallets(signer, impl), wallets);
  assert.equal(signatures.length, 1);
});

test("the owner authorizes the agent's signer in Gateway, or an outside agent itself", () => {
  const agent = "0x" + "5c".repeat(20);
  const signer = "0x" + "e0".repeat(20);
  assert.equal(hub.delegateOf({ ours: true, signer }, agent), signer);
  assert.equal(hub.delegateOf({ ours: false, signer: null }, agent), agent);
  assert.equal(hub.delegateOf(null, agent), null);
});

test("the agent's balance can be taken back only while the agent is off", () => {
  const body = { wallet: "0x" + "5c".repeat(20), balance: "19.5435", returnable: "19.4435", gasless: false };
  const off = hub.agentBalanceView(body, false);
  assert.equal(off.text, "19.5435 USDC");
  assert.equal(off.canReturn, true);
  assert.equal(off.amount, "19.4435");
  assert.match(off.note, /gas of that transfer/);
  const on = hub.agentBalanceView(body, true);
  assert.equal(on.canReturn, false);
  assert.match(on.note, /Turn the agent off/);
  assert.equal(hub.agentBalanceView({ ...body, returnable: "0" }, false).canReturn, false);
  assert.equal(hub.agentBalanceView(null, false).canReturn, false);
  assert.doesNotMatch(hub.agentBalanceView({ ...body, gasless: true }, false).note, /gas/);
});

test("taking the agent's balance back posts to the shop's endpoint with no address in it", async () => {
  const { impl, calls } = fakeFetch({
    [`POST /api/shops/${SHOP.toLowerCase()}/agent-balance/return`]: [200, { amount: "19.4435", transaction: "tx-1" }],
  });
  assert.deepEqual(await hub.returnAgentBalance(SHOP, impl), { amount: "19.4435", transaction: "tx-1" });
  assert.equal(calls[0].body, undefined);  // the service sends only to the signed-in owner
  assert.equal(calls[0].credentials, "same-origin");
});

test("setting the liquidity strategy posts the owner's choice to the shop's endpoint", async () => {
  const { impl, calls } = fakeFetch({
    [`POST /api/shops/${SHOP.toLowerCase()}/liquidity-strategy`]: [200, { liquidity_strategy: "instant_zero_fee" }],
  });
  assert.deepEqual(await hub.setLiquidityStrategy(SHOP, "instant_zero_fee", impl), { liquidity_strategy: "instant_zero_fee" });
  assert.deepEqual(calls[0].body, { strategy: "instant_zero_fee" });
  assert.equal(calls[0].credentials, "same-origin");
});

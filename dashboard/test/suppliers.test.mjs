import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const suppliers = await import("../suppliers.js");

const SHOP = "0x628ed6e6940F14043876a1255Da5506939A46AbC";
const WALLET = "0x2137" + "0".repeat(32) + "0379";
const TYPED = {
  types: {
    EIP712Domain: [{ name: "name", type: "string" }],
    SupplierSignIn: [{ name: "wallet", type: "address" }, { name: "nonce", type: "string" }],
  },
  primaryType: "SupplierSignIn",
  domain: { name: "embco", version: "1", chainId: 5042002 },
  message: { wallet: WALLET, nonce: "n-1" },
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

const fakeSigner = {
  getAddress: async () => WALLET,
  signTypedData: async (domain, types, value) => `sig:${Object.keys(types).join()}:${value.nonce ?? value.statement}`,
};

test("the notice tells the supplier what to type and never carries a link", () => {
  const text = suppliers.noticeText({
    company: "embcocompany", supplier: "Central Provisions Ltd", wallet: WALLET, signatureNeeded: true,
  });
  assert.match(text, /app\.embco\.xyz\/supplier/);
  assert.match(text, new RegExp(WALLET));
  assert.match(text, /private key/);
  assert.doesNotMatch(text, /https?:\/\//);
});

test("a notice without a pending signature only invites the supplier to follow its invoices", () => {
  const text = suppliers.noticeText({ company: "Shop", supplier: "S", wallet: null, signatureNeeded: false });
  assert.match(text, /follow your invoices/);
  assert.doesNotMatch(text, /confirm the wallet/);
});

test("the WhatsApp share carries the whole notice, encoded", () => {
  const url = suppliers.whatsappUrl("Hello S,\n\nType app.embco.xyz/supplier");
  assert.ok(url.startsWith("https://wa.me/?text="));
  assert.equal(decodeURIComponent(url.split("=")[1]), "Hello S,\n\nType app.embco.xyz/supplier");
});

test("a supplier signs in with its own wallet and the nonce comes back with the signature", async () => {
  const { impl, calls } = fakeFetch({
    "POST /api/supplier/sign-in-request": [200, TYPED],
    "POST /api/supplier/sign-in": [200, { wallet: WALLET }],
  });
  await suppliers.signInSupplier(fakeSigner, impl);
  assert.deepEqual(calls[0].body, { wallet: WALLET });
  assert.deepEqual(calls[1].body, { nonce: "n-1", signature: "sig:SupplierSignIn:n-1" });
  assert.ok(calls.every((c) => c.path.startsWith("/api/") && c.credentials === "same-origin"));
});

test("the page asks to sign in when the hub has no session", async () => {
  const { impl } = fakeFetch({ "GET /api/supplier/me": [401, { error: "sign in" }] });
  assert.equal(await suppliers.myPage(impl), null);
});

test("confirming a wallet signs the agent's own message and names the shop and supplier", async () => {
  const { impl, calls } = fakeFetch({ "POST /api/supplier/signature": [200, { received: true }] });
  const entry = { shop: SHOP, supplier: "Central Provisions Ltd", challenge: { typed_data: TYPED } };
  await suppliers.confirmWallet(fakeSigner, entry, impl);
  assert.deepEqual(calls[0].body, { shop: SHOP, supplier: "Central Provisions Ltd", signature: "sig:SupplierSignIn:n-1" });
});

test("the owner's notice is sent for the supplier named in the path, encoded", async () => {
  const shop = SHOP.toLowerCase();
  const { impl, calls } = fakeFetch({
    [`POST /api/shops/${shop}/suppliers/Central%20Provisions%20Ltd/email`]: [200, { sent: true }],
  });
  await suppliers.emailNotice(SHOP, "Central Provisions Ltd", impl);
  assert.equal(calls.length, 1);
});

test("invoice states read as plain words, and an unknown one as under review", () => {
  assert.equal(suppliers.invoiceStatus("SCHEDULED").label, "Will be paid");
  assert.equal(suppliers.invoiceStatus("SIGNATURE_NEEDED").kind, "ask");
  assert.equal(suppliers.invoiceStatus("SOMETHING_NEW").label, "Under review by the shop");
});

test("the last signature is explained only when it matters", () => {
  const rejected = { result: "REJECTED", reason: "the wallet in the ERP changed" };
  assert.equal(suppliers.signatureNote({ challenge: {}, last_signature: rejected }).kind, "error");
  assert.equal(suppliers.signatureNote({ challenge: null, last_signature: { result: "ACCEPTED" } }).kind, "success");
  assert.equal(suppliers.signatureNote({ challenge: {}, last_signature: null }), null);
});

test("amounts are shown exactly, and odd ones as written", () => {
  assert.equal(suppliers.formatAmount("1250.5"), "1,250.5");
  assert.equal(suppliers.formatAmount("0"), "0");
});

test("a wallet is shown in groups of four after 0x", () => {
  assert.deepEqual(suppliers.addressGroups("0x213793c4a7bd"), ["0x", "2137", "93c4", "a7bd"]);
});

test("the wallet check ignores case and marks the exact characters that differ", () => {
  const onFile = "0x213793c4A7bd";
  assert.equal(suppliers.compareAddress(onFile, "").state, "empty");
  assert.equal(suppliers.compareAddress(onFile, " 0X213793C4a7BD ".replace("0X", "0x")).state, "match");
  assert.equal(suppliers.compareAddress(onFile, "0x2137").state, "partial");
  assert.equal(suppliers.compareAddress(onFile, "213793c4a7bd").state, "match");
  const wrong = suppliers.compareAddress(onFile, "0x213793c4a7be");
  assert.equal(wrong.state, "mismatch");
  assert.deepEqual(wrong.chars.filter((c) => c.state === "wrong").map((c) => c.c), ["d"]);
  assert.equal(suppliers.compareAddress(onFile, "0x213793c4a7bd00").extra, 2);
});

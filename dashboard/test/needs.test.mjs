import assert from "node:assert/strict";
import { test } from "node:test";

const needs = await import("../needs.js");

const WALLET = "0x213793c4a7bddB9D5d37B2C0d964BED707eE0379";
const FP = "f".repeat(64);
const USDC = (n) => BigInt(n) * 1_000_000n;

function decision(action, steps, extra = {}) {
  return {
    invoice: "ACC-PINV-2026-00005", supplier: "Central Provisions Ltd", amount: "335.0", action,
    fingerprint: FP, reasons: [], payment: null,
    findings: steps.map(([step, outcome = "ASK"]) => ({ step, outcome, control: step, reason: `${step} reason` })),
    ...extra,
  };
}

const state = (task) => Object.fromEntries(task.steps.map((s) => [s.key, s.state]));

test("the real case: wallet proven, approved, over the limit: raise the limit first, decide after", () => {
  const task = needs.taskFor(decision("ASK", [["first_payment"], ["over_limit"]]),
    { wallet: WALLET, approved: true, maxPerPayment: USDC(300), weeklyCap: USDC(2000) });
  assert.deepEqual(state(task), { wallet: "done", approved: "done", limit: "todo" });
  assert.equal(task.steps.find((s) => s.key === "limit").action.amount, USDC(335));
  assert.equal(task.decide.state, "locked");
  assert.equal(task.section, "needs");
});

test("once the contract allows it but the agent has not looked again, the owner can make it look", () => {
  const task = needs.taskFor(decision("ASK", [["first_payment"], ["over_limit"]]),
    { wallet: WALLET, approved: true, maxPerPayment: USDC(400), weeklyCap: USDC(2000) });
  const limit = task.steps.find((s) => s.key === "limit");
  assert.equal(limit.state, "waiting");
  assert.equal(limit.action.type, "check");
  assert.equal(task.decide.state, "locked");
});

test("with every step done, the owner's decision opens, tied to the question shown", () => {
  const task = needs.taskFor(decision("ASK", [["first_payment"]]),
    { wallet: WALLET, approved: true, maxPerPayment: USDC(400), weeklyCap: USDC(2000) });
  assert.equal(task.decide.state, "open");
  assert.equal(task.decide.fingerprint, FP);
  assert.match(task.decide.text, /first payment/);
});

test("a wallet not approved in the contract gets its approve button", () => {
  const task = needs.taskFor(decision("ASK", [["first_payment"]]),
    { wallet: WALLET, approved: false, maxPerPayment: USDC(400), weeklyCap: USDC(2000) });
  const step = task.steps.find((s) => s.key === "approved");
  assert.deepEqual(step.action, { type: "approve_wallet", wallet: WALLET });
  assert.equal(task.decide.state, "locked");
});

test("an answer already sent is shown as sent, and a refused one says why", () => {
  const facts = { wallet: WALLET, approved: true, maxPerPayment: USDC(400), weeklyCap: USDC(2000) };
  assert.equal(needs.taskFor(decision("ASK", [["first_payment"]]), facts, { waiting: true }).decide.state, "sent");
  const refused = needs.taskFor(decision("ASK", [["first_payment"]]), facts,
    { answer: { result: "REJECTED", reason: "the question changed" } });
  assert.equal(refused.decide.refused, "the question changed");
});

test("only the limit asking needs no decision: raising it is enough", () => {
  const task = needs.taskFor(decision("ASK", [["over_limit"]]),
    { wallet: WALLET, approved: true, maxPerPayment: USDC(300), weeklyCap: USDC(2000) });
  assert.equal(task.decide, null);
});

test("no wallet in ERPNext: fix it there, nothing to approve yet", () => {
  const task = needs.taskFor(decision("HOLD", [["wallet_missing", "HOLD"], ["order_receipt", "HOLD"]], { amount: "125.0" }),
    { wallet: null, maxPerPayment: USDC(300), weeklyCap: USDC(2000) });
  assert.deepEqual(task.steps.map((s) => s.key), ["wallet", "order", "limit"]);
  assert.deepEqual(state(task), { wallet: "todo", limit: "done", order: "todo" });
  assert.equal(task.section, "held");
});

test("waiting only for the supplier's signature goes to its own section with a notice button", () => {
  const task = needs.taskFor(decision("HOLD", [["supplier_signature", "HOLD"]], { amount: "100" }),
    { wallet: WALLET, approved: false, maxPerPayment: USDC(300), weeklyCap: USDC(2000) });
  assert.equal(task.steps[0].action.type, "notice");
  assert.equal(task.section, "held");  // the contract approval is still the owner's to do
  const approved = needs.taskFor(decision("HOLD", [["supplier_signature", "HOLD"]], { amount: "100" }),
    { wallet: WALLET, approved: true, maxPerPayment: USDC(300), weeklyCap: USDC(2000) });
  assert.equal(approved.section, "supplier");
});

test("a paid invoice says where the money is", () => {
  const paid = needs.paymentText({ action: "PAY", payment: { status: "RECORDED", tx_hash: "0xab", erp_entry: "ACC-PAY-1" } });
  assert.equal(paid.state, "paid");
  assert.match(paid.text, /ACC-PAY-1/);
  assert.equal(needs.paymentText({ action: "PAY", payment: null }).state, "next");
  assert.equal(needs.paymentText({ action: "ASK" }), null);
});

test("raised limits never leave the weekly cap below the per-payment limit", () => {
  assert.deepEqual(needs.raisedLimits(USDC(335), USDC(2000)), { maxPerPayment: USDC(335), weeklyCap: USDC(2000) });
  assert.deepEqual(needs.raisedLimits(USDC(3000), USDC(2000)), { maxPerPayment: USDC(3000), weeklyCap: USDC(3000) });
});

test("tasks are grouped in the order the owner should look at them", () => {
  const facts = { wallet: WALLET, approved: true, maxPerPayment: USDC(400), weeklyCap: USDC(2000) };
  const tasks = [
    needs.taskFor(decision("HOLD", [["order_receipt", "HOLD"]]), facts),
    needs.taskFor(decision("ASK", [["first_payment"]]), facts),
    needs.taskFor(decision("PAY", []), facts),
  ];
  assert.deepEqual(needs.groupTasks(tasks).map((g) => g.key), ["needs", "paying", "held"]);
});

test("the limit button waits until nothing is left to fix in ERPNext", () => {
  const facts = { wallet: null, maxPerPayment: USDC(300), weeklyCap: USDC(2000) };
  const held = needs.taskFor(decision("HOLD", [["wallet_missing", "HOLD"], ["over_limit"]], { amount: "686.0" }), facts);
  const limit = held.steps.find((s) => s.key === "limit");
  assert.equal(limit.action, undefined);
  assert.match(limit.text, /^686 USDC .* once the steps above are fixed/);
});

test("the agent's choice decides the tab of an invoice the rules allow", () => {
  const base = { invoice: "PINV-1", supplier: "S", amount: "46", action: "PAY", findings: [], fingerprint: "f", payment: null };
  const tab = (agent) => needs.taskFor({ ...base, agent }, { wallet: "0x" + "a1".repeat(20), approved: true }).section;
  assert.equal(tab({ choice: "SCHEDULE", pay_on: "2026-10-13", reason: "due then" }), "scheduled");
  assert.equal(tab({ choice: "HOLD", reason: "odd" }), "held");
  assert.equal(tab({ choice: "ASK", reason: "?", question: "Ok?" }), "needs");
  assert.equal(tab({ choice: "PAY_NOW", reason: "due" }), "paying");
  assert.equal(needs.paymentText({ ...base, agent: null }).text, "Waiting for the agent to decide.");
  assert.match(needs.paymentText({ ...base, agent: { choice: "SCHEDULE", pay_on: "2026-10-13" } }).text, /2026-10-13/);
});

test("the agent's question and recommendation are shown as it wrote them", () => {
  const view = needs.agentView({ agent: { choice: "ASK", reason: "r", question: "Accept 1.50?", recommendation: "Check first." } });
  assert.deepEqual(view, { said: "Asking you", reason: "Accept 1.50?", recommendation: "Check first." });
  assert.equal(needs.agentView({ agent: null }), null);
});

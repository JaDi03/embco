import { test } from "node:test";
import assert from "node:assert/strict";
import { applyFloor, type AgentDecision, type CampaignState, type SubmissionFacts } from "./floor.ts";
import { checkReceipt, findCopy } from "./formats.ts";

const DAY = 86_400n;
const NOW = 1_000_000n;

const campaign = (over: Partial<CampaignState> = {}): CampaignState => ({
  reward: 50_000n, // 0.05 USDC
  balance: 100_000_000n,
  capPerPeriod: 1_000_000n,
  workerCapPerPeriod: 100_000n,
  periodLength: DAY,
  periodStart: NOW - 10n,
  spentInPeriod: 0n,
  workerPeriodStart: NOW - 10n,
  workerSpentInPeriod: 0n,
  ...over,
});

const clean: SubmissionFacts = { formatErrors: [], copyOf: null };
const pay: AgentDecision = { action: "pay", reasons: ["sharp image, total matches, worker 12/12 on control tasks"] };

// --- floor -----------------------------------------------------------------

test("a confident pay goes through when no rule is broken", () => {
  assert.deepEqual(applyFloor(pay, campaign(), clean, NOW), { action: "pay", violations: [] });
});

test("wait, reject and escalate always pass: the agent may block on its own", () => {
  for (const action of ["wait", "reject", "escalate"] as const) {
    const result = applyFloor({ action, reasons: ["blurry date"] }, campaign({ balance: 0n }), clean, NOW);
    assert.equal(result.action, action);
  }
});

test("a decision without reasons is escalated, whatever it was", () => {
  const result = applyFloor({ action: "pay", reasons: [" "] }, campaign(), clean, NOW);
  assert.equal(result.action, "escalate");
  assert.equal(result.violations[0].rule, "reasons");
});

test("a pay on a receipt that doesn't add up is escalated, with the reason", () => {
  const formatErrors = checkReceipt({ lines: [{ description: "Coffee", amount: "3.50" }], total: "4.00" });
  const result = applyFloor(pay, campaign(), { formatErrors, copyOf: null }, NOW);
  assert.equal(result.action, "escalate");
  assert.match(result.violations[0].detail, /add up to 3.50 but the total says 4.00/);
});

test("a pay on a copied answer is escalated", () => {
  const result = applyFloor(pay, campaign(), { formatErrors: [], copyOf: "17" }, NOW);
  assert.equal(result.action, "escalate");
  assert.equal(result.violations[0].rule, "copy");
});

test("an empty budget turns a pay into a wait", () => {
  const result = applyFloor(pay, campaign({ balance: 49_999n }), clean, NOW);
  assert.equal(result.action, "wait");
  assert.equal(result.violations[0].rule, "budget");
});

test("caps turn a pay into an escalation, like the contract does", () => {
  const overPeriod = applyFloor(pay, campaign({ spentInPeriod: 960_000n }), clean, NOW);
  assert.equal(overPeriod.action, "escalate");
  assert.equal(overPeriod.violations[0].rule, "period-cap");

  const overWorker = applyFloor(pay, campaign({ workerSpentInPeriod: 100_000n }), clean, NOW);
  assert.equal(overWorker.action, "escalate");
  assert.equal(overWorker.violations[0].rule, "worker-cap");
});

test("exactly reaching a cap is allowed", () => {
  const result = applyFloor(pay, campaign({ spentInPeriod: 950_000n, workerSpentInPeriod: 50_000n }), clean, NOW);
  assert.equal(result.action, "pay");
});

test("spend from an expired period no longer counts", () => {
  const old = NOW - DAY;
  const result = applyFloor(
    pay,
    campaign({ periodStart: old, spentInPeriod: 1_000_000n, workerPeriodStart: old, workerSpentInPeriod: 100_000n }),
    clean,
    NOW,
  );
  assert.equal(result.action, "pay");
});

test("budget plus another violation needs the agency, not just funds", () => {
  const result = applyFloor(pay, campaign({ balance: 0n }), { formatErrors: [], copyOf: "3" }, NOW);
  assert.equal(result.action, "escalate");
  assert.deepEqual(
    result.violations.map((v) => v.rule),
    ["copy", "budget"],
  );
});

// --- formats ---------------------------------------------------------------

test("a receipt whose lines add up to the total passes", () => {
  const errors = checkReceipt({
    lines: [
      { description: "Coffee", amount: "3.50" },
      { description: "Bagel", amount: "2" },
    ],
    total: "5.50",
  });
  assert.deepEqual(errors, []);
});

test("receipt amounts are never rounded", () => {
  const errors = checkReceipt({ lines: [{ description: "Gas", amount: "10.005" }], total: "10.01" });
  assert.match(errors.join(), /"10.005" is not a valid amount/);
  assert.match(checkReceipt({ lines: [], total: "0" }).join(), /no lines/);
  assert.match(checkReceipt({ lines: [{ description: " ", amount: "1" }], total: "1" }).join(), /no description/);
});

test("copies are found ignoring case and spacing, never on empty text", () => {
  const others = [{ submissionId: "9", text: "Hello   World" }];
  assert.equal(findCopy(" hello world ", others), "9");
  assert.equal(findCopy("hello there", others), null);
  assert.equal(findCopy("   ", [{ submissionId: "1", text: "" }]), null);
});

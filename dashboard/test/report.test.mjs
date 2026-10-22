import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { checkDecisionProof } from "../proof.js";
import { checklist, decisionStory, headline, plain } from "../report.js";
import { proofFromHash, proofLink } from "../share.js";

// The decision on PINV-1 in session s1, written by services/anchor/decision.py (see proof.test.mjs).
const fixture = JSON.parse(readFileSync(new URL("./decision-fixture.json", import.meta.url), "utf-8"));

test("a decision reads in plain words: what, why, the checks, the limits and the payment", () => {
  const story = decisionStory(fixture.bundle);
  assert.equal(headline(story), "The agent paid 125.5 USDC to S for invoice PINV-1.");
  assert.equal(story.reason, "ok");
  assert.deepEqual(story.checks, [{ control: "Three way match", outcome: "PASS", passed: true, why: "ok" }]);
  assert.ok(story.limits.some((l) => l.name === "Most it may pay at once" && l.value === "5000 USDC"));
  assert.ok(story.limits.some((l) => l.name === "Largest price rise it accepts" && l.value === "15%"));
  assert.deepEqual(story.answers.map((a) => a.verdict), ["APPROVE"]);
  assert.equal(story.payment.status, "RECORDED");
  assert.deepEqual(story.woke, ["s"]);
  assert.equal(plain("payee_wallet"), "Payee wallet");
});

test("a hold or a schedule says so in its sentence", () => {
  const base = { invoice: "PINV-9", supplier: "S", amount: "40.0", payOn: "2026-10-30" };
  assert.equal(headline({ ...base, choice: "HOLD", did: "held" }), "The agent held invoice PINV-9 (40 USDC from S).");
  assert.equal(headline({ ...base, choice: "SCHEDULE", did: "scheduled" }),
    "The agent scheduled invoice PINV-9 (40 USDC to S) to be paid on 2026-10-30.");
});

test("the checklist ticks all three only when the proof checks", async () => {
  const good = checklist(await checkDecisionProof(fixture.bundle, fixture.anchors));
  assert.deepEqual(good.items.map((i) => i.ok), [true, true, true]);
  const changed = structuredClone(fixture.bundle);
  changed.proofs[0].entry.body = changed.proofs[0].entry.body.replace("5000", "9000");
  const bad = checklist(await checkDecisionProof(changed, fixture.anchors));
  assert.deepEqual(bad.items.map((i) => i.ok), [false, true, true]);  // its text changed; its place did not
});

test("a link carries the whole proof after the #, and a cut link says so", async () => {
  const link = await proofLink(fixture.bundle, "https://app.embco.xyz/audit/");
  assert.ok(link.startsWith("https://app.embco.xyz/audit/#proof="));
  const hash = link.slice(link.indexOf("#"));
  assert.deepEqual(await proofFromHash(hash), fixture.bundle);
  assert.equal(await proofFromHash("#other"), null);
  await assert.rejects(() => proofFromHash(hash.slice(0, 200)), /incomplete/);
});

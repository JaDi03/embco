import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { arcValue, sameAsArc } from "../arc-check.js";
import { decisionStory } from "../report.js";

// Written by Python (services/anchor/onchain.py arc_proof) from the synthetic journal of
// tests/test_decision_proof.py; contracts/test/DecisionVerifier.t.sol checks a value made the same way.
const fixture = JSON.parse(readFileSync(new URL("./decision-fixture.json", import.meta.url), "utf-8"));

test("the browser makes the very value Python makes for Arc", () => {
  assert.equal(arcValue(fixture.bundle, fixture.anchors), fixture.arcValue);
});

test("no value without a MANIFEST, or when its anchor is not on Arc", () => {
  const older = structuredClone(fixture.bundle);
  older.proofs = older.proofs.filter((p) => p.entry.kind !== "MANIFEST");
  assert.equal(arcValue(older, fixture.anchors), null);
  assert.equal(arcValue(fixture.bundle, []), null);
});

test("what the page shows must be what Arc returned", () => {
  const story = decisionStory(fixture.bundle);
  const arc = { verified: true, invoice: "PINV-1", decision: "PAY_NOW", reason: "ok", paymentTx: "" };
  assert.equal(sameAsArc(story, arc), true);
  assert.equal(sameAsArc(story, { ...arc, decision: "HOLD" }), false);
  assert.equal(sameAsArc(story, { ...arc, reason: "something else" }), false);
  assert.equal(sameAsArc(story, { ...arc, verified: false }), false);
  assert.equal(sameAsArc(story, { ...arc, paymentTx: "0xabc" }), false);  // the records show no such payment
});

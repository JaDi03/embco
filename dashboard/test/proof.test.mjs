import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { merkleRoot, merkleTree, sessionOf, verifyInclusion } from "../journal.js";
import {
  allDecisionProofs, checkDecisionProof, checkEntryProof, decisionEntries, isDecisionProof, NotAnchored,
  prover, receiptEvidence,
} from "../proof.js";

// Written by services/anchor/decision.py (Python) from the synthetic journal of
// tests/test_decision_proof.py: two anchors, and the proof of the decision on PINV-1 in session s1.
const fixture = JSON.parse(readFileSync(new URL("./decision-fixture.json", import.meta.url), "utf-8"));
const { anchors, entries } = fixture;
const data = { shop: fixture.shop, entries };
const hex = (n) => n.toString(16).padStart(64, "0");

test("every leaf of every small tree has an audit path that gives the root", async () => {
  for (let n = 1; n < 34; n += 1) {
    const leaves = Array.from({ length: n }, (_, i) => hex(i * 97 + n));
    const tree = await merkleTree(leaves);
    const root = await merkleRoot(leaves);
    assert.equal(await tree.root(), root);
    for (let m = 0; m < n; m += 1) {
      const path = await tree.proof(m);
      assert.ok(await verifyInclusion(leaves[m], m, n, path, root), `${n}:${m}`);
      assert.ok(!(await verifyInclusion(hex(1), m, n, path, root)) || leaves[m] === hex(1));
      if (n > 1) assert.ok(!(await verifyInclusion(leaves[m], (m + 1) % n, n, path, root)));
    }
  }
  assert.equal(await verifyInclusion(hex(0), 0, 1, ["not hex"], hex(0)), false);
});

test("the browser gathers the same entries of a decision as Python", () => {
  assert.deepEqual(decisionEntries(entries, "s1", "PINV-1"), fixture.numbers);
  assert.throws(() => decisionEntries(entries, "s9", "PINV-1"), /no decision on PINV-1 in session s9/);
});

test("the browser makes the very proof Python made, and checks it against Arc", async () => {
  const bundle = await prover(data, anchors).decision("s1", "PINV-1");
  assert.deepEqual(bundle, fixture.bundle);
  const result = await checkDecisionProof(fixture.bundle, anchors);
  assert.equal(result.ok, true);
  assert.equal(result.proofs.length, 9);
  assert.ok(isDecisionProof(fixture.bundle));
});

test("a changed entry, a stray entry or a root Arc never wrote fails", async () => {
  const changed = structuredClone(fixture.bundle);
  const agent = changed.proofs.find((p) => p.entry.kind === "AGENT");
  agent.entry.body = agent.entry.body.replace('"ok"', '"changed"');
  const one = await checkDecisionProof(changed, anchors);
  assert.equal(one.ok, false);
  assert.equal(one.proofs.filter((p) => !p.contentMatchesHash).length, 1);

  const other = await prover(data, anchors).decision("s0", "PINV-2");
  const stray = structuredClone(fixture.bundle);
  stray.proofs.push(other.proofs.find((p) => p.entry.kind === "DECISION"));
  assert.match((await checkDecisionProof(stray, anchors)).unlinked, /DECISION entry/);

  const elsewhere = [{ root: "00".repeat(32), size: anchors[1].size, anchoredAt: "x" }];
  assert.equal((await checkDecisionProof(fixture.bundle, elsewhere)).ok, false);
});

test("one entry is proved with the first anchor that covers it", async () => {
  const make = prover(data, anchors);
  const early = await make.entry(2);
  assert.equal(early.size, 4);
  assert.equal((await checkEntryProof(early, anchors)).ok, true);
  assert.equal((await make.entry(9)).size, entries.length);
  await assert.rejects(() => make.entry(99), /entries 1 to 13/);
  await assert.rejects(() => prover(data, anchors.slice(0, 1)).entry(9), NotAnchored);
});

test("a download proves every decision with a session id, and counts the ones not anchored yet", async () => {
  const all = await allDecisionProofs(data, anchors);
  assert.deepEqual(all.proofs.map((p) => [p.session, p.invoice]), [["s0", "PINV-1"], ["s0", "PINV-2"], ["s1", "PINV-1"]]);
  assert.equal(all.pending, 0);
  const early = await allDecisionProofs(data, anchors.slice(0, 1));
  assert.deepEqual([early.proofs.length, early.pending], [0, 3]);
});

test("a receipt points to its decision, or to its session when that has no id", () => {
  const session = (body) => ({ kind: "SESSION", run: null, invoice: null, at: "t", body: JSON.stringify(body), hash: "h" });
  const pay = { tool: "pay_now", args: { invoice: "PINV-7" }, ok: true, tx: "0xAB" };
  assert.deepEqual(receiptEvidence([session({ id: "s5", trace: [pay] })], "0xab"), { session: "s5", invoice: "PINV-7" });
  assert.deepEqual(receiptEvidence([session({ trace: [pay] })], "0xab"), { number: 1 });
  assert.deepEqual(receiptEvidence([session({ id: "s6", funds_moved: [{ tx: "0xcd" }] })], "0xCD"), { number: 1 });
  assert.equal(receiptEvidence([session({ trace: [pay] })], "0xee"), null);
});

test("a payment finds its session by the id its PAYMENT entries carry, with no tx in the steps", () => {
  // as the live journal wrote ACC-PINV-2026-00025: the pay_now step kept no transaction
  const entry = (kind, invoice, body) => ({ kind, run: null, invoice, at: "t", body: JSON.stringify(body), hash: "h" });
  const journal = [
    entry("PAYMENT", "PINV-8", { status: "SUBMITTED", tx_hash: null, session: "s7" }),
    entry("PAYMENT", "PINV-8", { status: "COMPLETE", tx_hash: "0xEF", session: "s7" }),
    entry("SESSION", null, { id: "s7", summary: "Paid.", wakes: [], trace: [{ tool: "pay_now", args: { invoice: "PINV-8" }, ok: true }] }),
    entry("SESSION", null, { id: "s8", summary: "Looked.", wakes: [], trace: [] }),
  ];
  assert.deepEqual(receiptEvidence(journal, "0xef"), { session: "s7", invoice: "PINV-8" });
  assert.equal(sessionOf(journal, "0xef").summary, "Paid.");
});

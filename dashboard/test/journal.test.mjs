import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { canonicalContent, check, merkleRoot, mismatches, parseJournal, report, sessionOf, verifyChain } from "../journal.js";

// Written by agent/memory (Python) from a synthetic journal: accents, emoji, quotes, a backslash,
// line breaks and a control character, where two JSON writers could differ.
const fixture = JSON.parse(readFileSync(new URL("./journal-fixture.json", import.meta.url), "utf-8"));
const entries = fixture.entries;
const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

// The Certificate Transparency reference leaves and roots (RFC 6962 / 9162 test vectors).
const LEAVES = [[], [0x00], [0x10], [0x20, 0x21], [0x30, 0x31], [0x40, 0x41, 0x42, 0x43],
  [0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57],
  [0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x6b, 0x6c, 0x6d, 0x6e, 0x6f]].map(hex);
const ROOTS = [
  "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
  "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
  "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
  "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
  "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
  "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
  "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
  "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
];

test("the Merkle root matches the Certificate Transparency reference vectors", async () => {
  for (const [i, root] of ROOTS.entries()) assert.equal(await merkleRoot(LEAVES.slice(0, i + 1)), root);
});

test("the browser recomputes the same hash chain and root the agent wrote", async () => {
  assert.deepEqual(await verifyChain(entries), { ok: true });
  assert.equal(await merkleRoot(entries.map((e) => e.hash)), fixture.root);
  assert.equal(await merkleRoot(entries.slice(0, 3).map((e) => e.hash)), fixture.root_first_3);
  assert.match(canonicalContent(entries[0]), /^\{"at":"2026-10-21T05:48:00\+00:00","body":/);
});

test("a copy that matches every anchor passes, even after the journal kept growing", async () => {
  const anchors = [{ root: fixture.root_first_3, size: 3, anchoredAt: "x" }, { root: fixture.root, size: 5, anchoredAt: "y" }];
  const result = await check(entries, anchors);
  assert.equal(result.ok, true);
  assert.deepEqual([result.entries, result.anchored], [5, 5]);
  assert.deepEqual(await mismatches(entries, anchors.slice(0, 1)), []);
});

test("an edited entry breaks the chain", async () => {
  const edited = entries.map((e, i) => (i === 1 ? { ...e, body: e.body.replace("quotes", "QUOTES") } : e));
  const result = await check(edited, [{ root: fixture.root, size: 5 }]);
  assert.equal(result.ok, false);
  assert.match(result.problems[0], /^Entry 2 \(ANSWER\)/);
});

test("a rewrite with every hash recomputed still fails against the root on Arc", async () => {
  const { chainHash } = await import("../journal.js");
  let previous = "0".repeat(64);
  const forged = [];
  for (const [i, e] of entries.entries()) {
    const entry = i === 1 ? { ...e, body: e.body.replace("quotes", "QUOTES") } : { ...e };
    entry.hash = await chainHash(previous, entry);
    previous = entry.hash;
    forged.push(entry);
  }
  assert.deepEqual(await verifyChain(forged), { ok: true });
  const result = await check(forged, [{ root: fixture.root, size: 5 }]);
  assert.equal(result.ok, false);
  assert.match(result.problems[0], /do not give the root written on Arc/);
});

test("removed entries are caught", async () => {
  const result = await check(entries.slice(0, 4), [{ root: fixture.root, size: 5 }]);
  assert.match(result.problems[0], /entries were removed/);
});

test("a receipt finds the session that moved it, with what the agent read", () => {
  const session = sessionOf(entries, "0xAB");
  assert.deepEqual(session.woke, ["daily round"]);
  assert.equal(session.summary, "Parked.");
  assert.deepEqual(session.steps[0].saw, { fees: "0" });
  assert.equal(session.steps[1].saw, null);
  assert.equal(sessionOf(entries, "0xcd"), null);
  assert.equal(sessionOf(entries, ""), null);
});

test("the report gives the chain, the root of the whole copy and a row per anchor", async () => {
  const anchors = [{ root: fixture.root_first_3, size: 3, anchoredAt: "a" }, { root: "00".repeat(32), size: 5, anchoredAt: "b" },
    { root: fixture.root, size: 9, anchoredAt: "c" }];
  const r = await report(entries, anchors);
  assert.equal(r.chain.ok, true);
  assert.equal(r.root, fixture.root);
  assert.deepEqual(r.rows.map((row) => row.matches), [true, false, false]);
  assert.equal(r.rows[1].fileRoot, fixture.root);  // different from what Arc has
  assert.equal(r.rows[2].fileRoot, null);          // the anchor covers more entries than the copy has
});

test("a file is read as a journal only when it has a shop and complete entries", () => {
  const good = JSON.stringify({ shop: "0xabc", merkle: { root: "claimed" }, entries });
  assert.equal(parseJournal(good).entries.length, 5);
  assert.throws(() => parseJournal("nope"), /not JSON/);
  assert.throws(() => parseJournal('{"hello": 1}'), /not an Embco journal/);
  assert.throws(() => parseJournal(JSON.stringify({ shop: "0xabc", entries: [{ kind: "RUN" }] })), /not an Embco journal/);
});

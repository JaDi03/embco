import assert from "node:assert/strict";
import { test } from "node:test";

const chart = await import("../treasury-chart.js");

const USDC = (n) => BigInt(Math.round(n * 1_000_000));
// lopezgrocery: the agent parked 6.69 at 07:49:52 UTC on 20 Oct (ShopEarn's Parked event)
const moves = [{ at: "2026-10-20T07:49:52+00:00", kind: "in", vault: "Morpho USDC Vault", usdc: "6.690000", by: "agent", tx: "0xd185" }];
const points = [
  { at: "2026-10-20T15:45:28+00:00", wallet: "1.004041", earn: "6.689999", other_chains: "6971.960511" },
  { at: "2026-10-27T15:45:28+00:00", wallet: "1.004041", earn: "6.701", other_chains: "6971.960511" },
];

test("the result is the value now plus what came out minus what went in", () => {
  const r = chart.earnResult(points, moves, Date.parse("2026-10-27T15:49:52Z"));
  assert.equal(r.putIn, USDC(6.69));
  assert.equal(r.value, USDC(6.701));
  assert.equal(r.gain, USDC(0.011));
  assert.equal(chart.percentText(r.percent), "+0.1644%");
  assert.equal(chart.timeInside(r.hours), "7 days");
});

test("once everything is out, the time inside stops at the last exit", () => {
  const out = [...moves, { at: "2026-10-21T07:49:52+00:00", kind: "out", vault: "Morpho USDC Vault", usdc: "6.70", by: "agent", tx: "0xaa" }];
  const gone = [{ at: "2026-10-21T08:00:00+00:00", earn: "0" }];
  const r = chart.earnResult(gone, out, Date.parse("2026-10-30T00:00:00Z"));
  assert.equal(r.gain, USDC(0.01));
  assert.equal(chart.timeInside(r.hours), "24 h");
});

test("no result before anything went in", () => {
  const r = chart.earnResult(points, [], Date.now());
  assert.equal(r.percent, null);
  assert.equal(chart.percentText(r.percent), "");
});

test("the line needs two readings, and each move sits on it", () => {
  assert.equal(chart.chartModel(points.slice(0, 1), moves), null);
  const model = chart.chartModel(points, moves, Date.parse("2026-10-28T00:00:00Z"));
  assert.ok(model.line.startsWith("M") && model.area.endsWith("Z"));
  assert.equal(model.marks.length, 0);  // the entry came before the first reading
  const inside = chart.chartModel(points, [{ ...moves[0], at: "2026-10-22T00:00:00Z" }], Date.parse("2026-10-28T00:00:00Z"));
  assert.equal(inside.marks.length, 1);
  assert.ok(inside.lo < 6.689999 && inside.hi > 6.701);
});

test("round axis values", () => {
  assert.deepEqual(chart.niceTicks(0, 151.5), [0, 50, 100, 150]);
  assert.deepEqual(chart.niceTicks(6.67, 6.72), [6.68, 6.7, 6.72]);
});

test("unreadable moves are dropped", () => {
  assert.equal(chart.earnMoves([{ at: "never", kind: "in", usdc: "1" }, { at: "2026-10-20T00:00:00Z", kind: "sideways", usdc: "1" }]).length, 0);
});

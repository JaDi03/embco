import assert from "node:assert/strict";
import { test } from "node:test";

const chart = await import("../treasury-chart.js");

// lopezgrocery's reading on 2026-10-20, then 125 brought from Ethereum and parked in Earn
const points = [
  { at: "2026-10-20T14:56:43+00:00", wallet: "1.004041", earn: "6.689999", other_chains: "6971.960511" },
  { at: "2026-10-20T16:00:00+00:00", wallet: "1", earn: "131.689999", other_chains: "6845.9" },
];

test("no chart until there are two readings", () => {
  assert.equal(chart.chartModel([]), null);
  assert.equal(chart.chartModel(points.slice(0, 1)), null);
});

test("round y ticks from zero above the largest total", () => {
  assert.deepEqual(chart.niceTicks(6979.65), [0, 2000, 4000, 6000, 8000]);
  assert.deepEqual(chart.niceTicks(132.69), [0, 50, 100, 150]);
  assert.deepEqual(chart.niceTicks(0), [0, 1]);
});

test("areas stack Earn first, then the wallet on Arc; other chains stay out", () => {
  const model = chart.chartModel(points, Date.parse("2026-10-20T17:00:00Z"));
  assert.deepEqual(model.areas.map((a) => a.key), ["earn", "wallet"]);
  const [earn, wallet] = model.areas.map((a) => a.top);
  assert.deepEqual(earn, [0, 131.689999]);
  assert.ok(Math.abs(wallet[1] - 132.689999) < 1e-6);
  assert.deepEqual(model.ticks.map((t) => t.v), [0, 50, 100, 150]);
  assert.ok(model.areas.every((a) => a.d.startsWith("M") && a.d.endsWith("Z")));
});

test("unreadable points are dropped and the rest sorted by time", () => {
  const read = chart.readPoints([points[1], { at: "never", wallet: "1", earn: "1", other_chains: "1" }, points[0]]);
  assert.deepEqual(read.map((p) => p.earn), [6689999n, 131689999n]);
});

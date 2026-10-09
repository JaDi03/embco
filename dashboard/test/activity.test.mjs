import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const activity = await import("../activity.js");

const ev = (seq, kind, at = "2026-10-09T20:00:00+00:00") => ({ seq, kind, at, text: kind });

test("only events after the last one are asked for, on this site", async () => {
  const calls = [];
  const fetchImpl = async (path, options) => {
    calls.push({ path, options });
    return { ok: true, status: 200, json: async () => ({ events: [], interval_minutes: 15 }) };
  };
  await activity.fetchActivity("0xABC", 41, fetchImpl);
  assert.equal(calls[0].path, "/api/shops/0xabc/activity?after=41");
  assert.equal(calls[0].options.credentials, "same-origin");
});

test("new events join the old ones once, in order, newest kept", () => {
  const merged = activity.merge([ev(1, "check"), ev(2, "done")], [ev(2, "done"), ev(3, "check")], 2);
  assert.deepEqual(merged.map((e) => e.seq), [2, 3]);
});

test("a check with no end yet means the agent is checking now", () => {
  assert.equal(activity.nextCheck([ev(1, "done"), ev(2, "check"), ev(3, "new")], 15).state, "checking");
});

test("after a check, the countdown runs to the next one", () => {
  const done = Date.parse("2026-10-09T20:00:00+00:00");
  const next = activity.nextCheck([ev(1, "check"), ev(2, "done")], 15, done + 60_000);
  assert.deepEqual(next, { state: "waiting", seconds: 14 * 60 });
  assert.equal(activity.countdown(14 * 60 + 5), "14:05");
  assert.equal(activity.nextCheck([ev(1, "done")], 15, done + 20 * 60_000).seconds, 0);
});

test("every kind reads as a short label", () => {
  assert.equal(activity.labelOf("paid"), "PAID");
  assert.equal(activity.labelOf("recorded"), "ERP");
  assert.equal(activity.labelOf("something_new"), "SOMETHING_NEW");
});

test("each line says who did it, and a chip shows one actor's lines", () => {
  assert.equal(activity.actorOf("agent"), "AGENT");
  assert.equal(activity.actorOf("check"), "REFLEX");
  assert.equal(activity.actorOf("you"), "YOU");
  assert.equal(activity.actorOf("signature"), "SUPPLIER");
  assert.equal(activity.actorOf("paid"), "CONTRACT");
  assert.equal(activity.actorOf("recorded"), "ERP");
  assert.equal(activity.actorOf("not_paid"), "GUARD");
  const events = [{ kind: "agent" }, { kind: "paid" }, { kind: "agent" }];
  assert.equal(activity.byActor(events, "AGENT").length, 2);
  assert.equal(activity.byActor(events, null).length, 3);
});

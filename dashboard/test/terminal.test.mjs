import assert from "node:assert/strict";
import { test } from "node:test";

const { linesOf } = await import("../terminal.js");

const AT = "2026-10-19T09:10:26+00:00";

test("a session reads as wake, decisions, the move of money, what it said and its cost", () => {
  const events = [
    { seq: 1, at: AT, kind: "reflex", text: "The agent started working. A due date is close for ACC-PINV-2026-00017." },
    { seq: 2, at: AT, kind: "agent", text: "ACC-PINV-2026-00017: Due 24 Oct; paying on the 20th keeps room this week.", invoice: "ACC-PINV-2026-00017", choice: "SCHEDULE", pay_on: "2026-10-20" },
    { seq: 3, at: AT, kind: "agent", text: "Withdrew 213.3 USDC from Morpho USDC Vault to your wallet on Arc.", tx: "0xabc" },
    { seq: 4, at: AT, kind: "agent", text: "Scheduled one bill for 20 Oct.", cost: "$0.004", steps: 7 },
  ];
  const lines = linesOf(events);
  assert.deepEqual(lines.map((l) => l.tag), ["WAKE", "DECIDE", "MOVED", "SAYS", ""]);
  assert.equal(lines[0].text, "A due date is close for ACC-PINV-2026-00017.");
  assert.match(lines[1].text, /^PAY ON 2026-10-20 · ACC-PINV-2026-00017: /);
  assert.equal(lines[2].tx, "0xabc");
  assert.equal(lines[4].text, "session done · 7 steps");
});

test("the tools the model called come right after what woke it, refusals marked", () => {
  const lines = linesOf([
    { seq: 1, at: AT, kind: "reflex", text: "The agent started working. The daily round." },
    { seq: 2, at: AT, kind: "tool", text: "cash_position() -> done", tool: "cash_position" },
    { seq: 3, at: AT, kind: "tool", text: "pay_now(invoice=X) -> refused: duplicate bill", tool: "pay_now", refused: true },
    { seq: 4, at: AT, kind: "agent", text: "X: a duplicate.", invoice: "X", choice: "HOLD" },
  ]);
  assert.deepEqual(lines.map((l) => [l.tag, l.tone]), [["WAKE", "wake"], ["TOOL", "tool"], ["TOOL", "refused"], ["DECIDE", "bad"]]);
});

test("each step shows when it returned, and what follows it the last step's time", () => {
  const lines = linesOf([
    { seq: 1, at: AT, kind: "reflex", text: "The agent started working. The owner wrote to you (1 message(s))." },
    { seq: 2, at: AT, kind: "tool", text: "earn_position() -> done", done_at: "2026-10-19T09:10:29+00:00" },
    { seq: 3, at: AT, kind: "tool", text: "cash_position() -> done", done_at: "2026-10-19T09:10:34+00:00" },
    { seq: 4, at: AT, kind: "agent", text: "Answered the owner.", cost: "", steps: 2 },
  ]);
  assert.deepEqual(lines.map((l) => l.at), [AT, "2026-10-19T09:10:29+00:00", "2026-10-19T09:10:34+00:00",
    "2026-10-19T09:10:34+00:00", "2026-10-19T09:10:34+00:00"]);
});

test("a payment line keeps its transaction; a failed check is an error line", () => {
  const lines = linesOf([
    { seq: 1, at: AT, kind: "paid", text: "Paid 58 USDC to Adeyemi Foods for ACC-PINV-2026-00018.", tx: "0xdef" },
    { seq: 2, at: AT, kind: "error", text: "ERPNext did not answer." },
  ]);
  assert.deepEqual(lines.map((l) => [l.tag, l.tone, l.tx ?? null]), [["PAID", "ok", "0xdef"], ["ERROR", "bad", null]]);
});

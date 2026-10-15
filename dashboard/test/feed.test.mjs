import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const { chatOf, feedOf, quickAnswer, quietText } = await import("../feed.js");
const { replyTo } = await import("../needs.js");

const TX = "0x" + "10".repeat(32);
const at = (minute) => `2026-10-14T19:${String(minute).padStart(2, "0")}:00+00:00`;

// The shape of the pilot's activity around ACC-PINV-2026-00019: a session that brought funds,
// the payment's contract and ERP lines, and quiet checks around them.
const EVENTS = [
  { seq: 1, at: at(44), kind: "done", text: "Nothing changed in 3 checks.", summary: "Check done: 9 unpaid, 2 need you", checks: 3 },
  { seq: 2, at: at(50), kind: "reflex", text: "Woke the agent. Your alarm: bring funds." },
  { seq: 3, at: at(50), kind: "agent", text: "Brought 37.78 USDC to your wallet on Arc.", tx: "0x" + "4d".repeat(32) },
  { seq: 4, at: at(50), kind: "agent", text: "ACC-PINV-2026-00019 is paid.", cost: "$0.0089", steps: 20 },
  { seq: 5, at: at(51), kind: "sending", text: "Sending 58 USDC to Adeyemi.", invoice: "ACC-PINV-2026-00019" },
  { seq: 6, at: at(51), kind: "paid", text: "Paid 58 USDC to Adeyemi.", tx: TX, invoice: "ACC-PINV-2026-00019" },
  { seq: 7, at: at(51), kind: "recorded", text: "Paid 58 USDC to Adeyemi, recorded in ERPNext as ACC-PAY-2026-00015.", tx: TX, invoice: "ACC-PINV-2026-00019" },
  { seq: 8, at: at(52), kind: "done", text: "Check done.", summary: "Check done: 9 unpaid, 1 need you, 58 USDC paid" },
  { seq: 9, at: at(55), kind: "check", text: "Checking your ERPNext..." },
  { seq: 10, at: at(56), kind: "done", text: "Nothing changed in 13 checks.", summary: "Check done: 9 unpaid, 1 need you", checks: 13 },
];
test("the activity reads in the order it happened, quiet checks run together", () => {
  const types = feedOf(EVENTS).map((i) => i.type);
  assert.deepEqual(types, ["quiet", "money", "work", "money", "quiet"]);
});

test("a payment's contract and ERP lines are one card, with the ERP's words", () => {
  const cards = feedOf(EVENTS).filter((i) => i.type === "money");
  assert.equal(cards.length, 2);  // the funds brought, and the payment
  assert.match(cards[1].event.text, /recorded in ERPNext/);
});

test("quiet checks fold into one line that says how many and what they found", () => {
  const quiet = feedOf(EVENTS).filter((i) => i.type === "quiet");
  assert.equal(quietText(quiet.at(-1).events), "Checked 14 times · 9 unpaid, 1 need you");  // 1 + 13, back to back
  assert.equal(quietText(quiet[1].events.slice(0, 1)), "Checked once · 9 unpaid, 1 need you, 58 USDC paid");
  assert.equal(quietText([{ kind: "check", text: "Checking your ERPNext..." }]), "Checking your ERPNext...");
});

test("a payment still on its way shows that it is being sent", () => {
  const items = feedOf([{ seq: 1, at: at(1), kind: "sending", text: "Sending 58 USDC.", invoice: "ACC-PINV-2026-00020" }]);
  assert.equal(quietText(items[0].events), "Sending 58 USDC.");
});

test("what the owner must see stays a line of its own", () => {
  const notes = feedOf([
    { seq: 1, at: at(1), kind: "not_paid", text: "Did not pay ACC-PINV-2026-00015." },
    { seq: 2, at: at(2), kind: "you", text: "You turned draft mode off." },
  ]).map((i) => i.type);
  assert.deepEqual(notes, ["note", "note"]);
});

test("a quick answer shows the answer and the invoice, not the question again", () => {
  const decision = {
    invoice: "ACC-PINV-2026-00019", action: "PAY",
    agent: { choice: "ASK", question: "Adeyemi's bill (58 USDC): pay it today? (1) add funds (2) wait." },
  };
  const [yes, no] = replyTo(decision).options;
  assert.deepEqual(quickAnswer(yes.text), { invoice: "ACC-PINV-2026-00019", text: "Yes, go ahead and pay it." });
  assert.deepEqual(quickAnswer(no.text), { invoice: "ACC-PINV-2026-00019", text: "No, hold it for now." });
  assert.equal(quickAnswer("About ACC-PINV-2026-00019, why is it on hold?"), null);  // the owner's own words
});

test("the chat shows each payment and move of funds as a receipt, where it happened", () => {
  const chat = [
    { seq: 1, at: at(49), from: "owner", text: "Pay ACC-PINV-2026-00019 today" },
    { seq: 2, at: at(53), from: "agent", text: "Paid it." },
  ];
  const items = chatOf(chat, EVENTS);
  assert.deepEqual(items.map((i) => i.type), ["message", "money", "money", "message"]);
  assert.match(items[2].event.text, /recorded in ERPNext/);  // one receipt per payment
});

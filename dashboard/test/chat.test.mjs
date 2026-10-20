import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const chat = await import("../chat.js");

const SHOP = "0x628ed6e6940F14043876a1255Da5506939A46AbC";

test("a message is trimmed, and an empty or too long one is not sent", () => {
  assert.deepEqual(chat.checkMessage("  What is due tomorrow?  "), { text: "What is due tomorrow?" });
  assert.match(chat.checkMessage("   ").error, /Write something/);
  assert.match(chat.checkMessage("x".repeat(chat.MAX_TEXT + 1)).error, /At most/);
});

test("the owner's words go right, the agent's left, with no label, like a colleague", () => {
  assert.equal(chat.bubbleOf({ from: "owner", text: "hi" }).side, "me");
  const reply = chat.bubbleOf({ from: "agent", text: "Nothing is due.", cost: "$0.0012", model: "claude-haiku-5-5" });
  assert.deepEqual(reply, { side: "agent", text: "Nothing is due.", meta: "" });
  assert.equal(chat.bubbleOf({ from: "system", text: "could not answer" }).meta, "SERVICE");
});

test("what the owner wrote on Telegram says so; the agent's words carry no label", () => {
  assert.equal(chat.bubbleOf({ from: "owner", text: "pay it", by: chat.VIA_TELEGRAM }).meta, "via Telegram");
  assert.equal(chat.bubbleOf({ from: "owner", text: "pay it", by: "owner 0xabc" }).meta, "");
  const reply = chat.bubbleOf({ from: "agent", text: "Paid.", cost: "$0.0014", model: "m", telegram: true });
  assert.equal(reply.meta, "");
  assert.equal(chat.bubbleOf({ from: "system", text: "x", telegram: true }).meta, "SERVICE · sent to Telegram");
});

test("transaction hashes and addresses in a message become links, the rest stays text", () => {
  const tx = "0x" + "fc".repeat(32);
  const wallet = "0x" + "ab".repeat(20);
  assert.deepEqual(chat.linkParts(`Paid, transaction ${tx}, to ${wallet}.`), [
    { text: "Paid, transaction " }, { tx }, { text: ", to " }, { address: wallet }, { text: "." },
  ]);
  assert.deepEqual(chat.linkParts("Nothing is due."), [{ text: "Nothing is due." }]);
  assert.deepEqual(chat.linkParts("0x" + "1".repeat(50)), [{ text: "0x" + "1".repeat(50) }]);  // neither
});

test("sending posts the text to this shop, with the session cookie", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, ...options, body: JSON.parse(options.body) });
    return { ok: true, status: 200, json: async () => ({ received: true }) };
  };
  assert.deepEqual(await chat.sendMessage(SHOP, "hi", fetchImpl), { received: true });
  assert.equal(calls[0].url, `/api/shops/${SHOP.toLowerCase()}/messages`);
  assert.deepEqual(calls[0].body, { text: "hi" });
  assert.equal(calls[0].credentials, "same-origin");
});

test("while the agent works, the owner sees for how long since they wrote", async () => {
  const { workingText } = await import("../chat.js");
  const entries = [{ from: "agent", at: "2026-10-14T19:20:00Z" }, { from: "owner", at: "2026-10-14T19:24:00Z" }];
  const now = Date.parse("2026-10-14T19:25:20Z");
  assert.equal(workingText(true, entries, now), "The agent is working on your message · 1m 20s");
  assert.equal(workingText(true, [{ from: "owner", at: "2026-10-14T19:25:12Z" }], now), "The agent is working on your message · 8s");
  assert.equal(workingText(false, entries, now), "");
});

test("a question with buttons is a card: who and how much, the question, two answers", () => {
  const answers = { yes: "yes, go ahead and pay it.", no: "no, hold it for now." };
  const entry = { from: "agent", text: "t", invoice: "ACC-PINV-2026-00022", buttons: true,
    question: "Northern Grains billed 2 sacks of flour but no delivery is recorded. Did it arrive?" };
  const card = chat.questionCard(entry, { supplier: "Northern Grains & Cereals Co.", amount: "92.0" }, answers);
  assert.equal(card.title, "Northern Grains & Cereals Co. · 92.0 USDC");
  assert.equal(card.text, entry.question);
  assert.equal(card.yes, `About ACC-PINV-2026-00022 (you asked: ${entry.question}): yes, go ahead and pay it.`);
  assert.equal(chat.questionCard({ ...entry, buttons: false }, null, answers), null);
  assert.equal(chat.questionCard({ from: "agent", text: "Paid." }, null, answers), null);
});

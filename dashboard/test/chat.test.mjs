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

test("the owner's words go right, the agent's left with what it cost", () => {
  assert.equal(chat.bubbleOf({ from: "owner", text: "hi" }).side, "me");
  const reply = chat.bubbleOf({ from: "agent", text: "Nothing is due.", cost: "$0.0012", model: "claude-haiku-5-5" });
  assert.deepEqual(reply, { side: "agent", text: "Nothing is due.", meta: "AGENT · Claude · $0.0012 est." });
  assert.equal(chat.bubbleOf({ from: "system", text: "could not answer" }).meta, "SERVICE");
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

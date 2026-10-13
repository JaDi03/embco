// Talking to the agent: the owner writes, the agent (Claude) answers within seconds.
// The page asks the hub only for entries after the last one it has.

import { HUB_PATH } from "./config.js";
import { call } from "./hub.js";

const path = (shop) => `${HUB_PATH}/shops/${shop.toLowerCase()}`;

export const fetchChat = (shop, after = 0, fetchImpl = fetch) =>
  call(`${path(shop)}/chat?after=${Number(after) || 0}`, { fetchImpl });

export const sendMessage = (shop, text, fetchImpl = fetch) =>
  call(`${path(shop)}/messages`, { method: "POST", body: { text }, fetchImpl });

export const MAX_TEXT = 1000;

/** What to send, or why not: trimmed, not empty, not too long. */
export function checkMessage(text) {
  const clean = String(text ?? "").trim();
  if (!clean) return { error: "Write something first." };
  if (clean.length > MAX_TEXT) return { error: `At most ${MAX_TEXT} characters.` };
  return { text: clean };
}

const HEX = /0x[0-9a-fA-F]{64}(?![0-9a-fA-F])|0x[0-9a-fA-F]{40}(?![0-9a-fA-F])/g;

/** A message cut into text, transaction hashes and addresses, so the page can link each one to
 * the explorer. Anything else stays plain text. */
export function linkParts(text) {
  const parts = [];
  let at = 0;
  for (const match of String(text ?? "").matchAll(HEX)) {
    if (match.index > at) parts.push({ text: text.slice(at, match.index) });
    parts.push(match[0].length === 66 ? { tx: match[0] } : { address: match[0] });
    at = match.index + match[0].length;
  }
  if (at < String(text ?? "").length) parts.push({ text: text.slice(at) });
  return parts;
}

export const VIA_TELEGRAM = "owner via Telegram";  // who wrote it, as the bot records it

/** One bubble per entry: the owner on the right, the agent and the service on the left. What
 * the owner wrote on Telegram, and what also went to their Telegram chat, says so. */
export function bubbleOf(entry) {
  if (entry.from === "owner") {
    return { side: "me", text: entry.text, meta: entry.by === VIA_TELEGRAM ? "via Telegram" : "" };
  }
  const telegram = entry.telegram ? "sent to Telegram" : "";
  if (entry.from === "agent") {
    const meta = ["AGENT", entry.model ? "Claude" : "", entry.cost ? `${entry.cost} est.` : "", telegram];
    return { side: "agent", text: entry.text, meta: meta.filter(Boolean).join(" · ") };
  }
  return { side: "system", text: entry.text, meta: ["SERVICE", telegram].filter(Boolean).join(" · ") };
}

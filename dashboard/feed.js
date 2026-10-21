// What the agent and the contract did, as the owner reads it next to the chat: money that moved
// is a one-line card, quiet checks fold into one line, and each time the agent worked is one line
// that opens to its steps. The owner's quick answers read as the answer they gave.

import { timeline } from "./activity.js";
import { QUICK_ANSWERS } from "./needs.js";

/** Lines the owner should see as they are (what stopped, what changed, what they did); the rest
 * of the checks fold. */
const NOTES = new Set(["not_paid", "signature", "limits", "error", "answer", "you"]);

/**
 * The items, oldest first: {type: "money", event}, {type: "note", event}, {type: "work",
 * session} and {type: "quiet", events}. A payment's contract and ERP lines share their
 * transaction and become one card, with the last line's words.
 */
export function feedOf(events) {
  const items = [];
  const money = new Map();  // tx: its card
  const addMoney = (event, at) => {
    const card = money.get(event.tx);
    if (card) {
      card.event = event;
      return;
    }
    const item = { type: "money", at, event };
    money.set(event.tx, item);
    items.push(item);
  };
  // "Sending" says a payment is on its way; once its card is there, it says nothing more
  const sent = new Set(events.filter((e) => e.tx && e.invoice).map((e) => e.invoice));
  for (const it of timeline(events)) {
    if (it.type === "line" && it.event.kind === "sending" && sent.has(it.event.invoice)) continue;
    if (it.type === "session") {
      for (const e of it.items) if (e.tx) addMoney(e, it.at);
      items.push({ type: "work", at: it.at, session: it });
    } else if (it.event.tx) {
      addMoney(it.event, it.event.at);
    } else if (NOTES.has(it.event.kind)) {
      items.push({ type: "note", at: it.event.at, event: it.event });
    } else {
      items.push({ type: "quiet", at: it.event.at, events: [it.event] });
    }
  }
  items.sort((a, b) => new Date(a.at) - new Date(b.at));
  const out = [];
  for (const item of items) {
    const last = out.at(-1);
    if (item.type === "quiet" && last?.type === "quiet") last.events.push(...item.events);
    else out.push(item);
  }
  return out;
}

/** The activity beside the chat: everything but the money cards, which are the chat's receipts,
 * and each session without the moves the chat already shows. */
export function activityOf(events) {
  return feedOf(events).filter((i) => i.type !== "money").map((i) => (i.type === "work"
    ? { ...i, session: { ...i.session, items: i.session.items.filter((e) => !e.tx) } }
    : i));
}

/** The chat as the owner reads it: their messages and the agent's, with the money cards of the
 * activity in between, by time. */
/** Why the agent made this move, in its own words: the reason it gave for a move of funds, or
 * for a payment the reason of its last decision on that invoice. Empty when it gave none. */
export function receiptWhy(event, events) {
  if (event.why) return event.why;
  if (!event.invoice) return "";
  const decided = (events ?? []).filter((e) => e.kind === "agent" && e.choice && e.invoice === event.invoice
    && (e.seq ?? 0) <= (event.seq ?? Infinity));
  const last = decided.at(-1);
  if (!last) return "";
  const text = String(last.text);
  return text.startsWith(`${event.invoice}:`) ? text.slice(event.invoice.length + 1).trim() : text;
}

export function chatOf(entries, events) {
  const money = feedOf(events).filter((i) => i.type === "money");
  const messages = entries.map((entry) => ({ type: "message", at: entry.at, entry }));
  return [...messages, ...money].sort((a, b) => new Date(a.at) - new Date(b.at));
}

/** The owner's quick answer as they meant it: the answer, and the invoice it answers. The text
 * sent to the agent also carries the question, so the agent knows what the yes is for. */
export function quickAnswer(text) {
  const match = /^About (\S+) \(you asked: [\s\S]*\): (.+)$/.exec(String(text ?? ""));
  if (!match || !Object.values(QUICK_ANSWERS).includes(match[2])) return null;
  const answer = match[2];
  return { invoice: match[1], text: answer[0].toUpperCase() + answer.slice(1) };
}

/** What a folded run of checks says: how many, and what the last one found. */
export function quietText(events) {
  const checks = events.reduce((n, e) => n + (e.kind === "done" ? (e.checks ?? 1) : 0), 0);
  if (!checks) return events.length === 1 ? events[0].text : `${events.length} steps of a check`;
  const last = [...events].reverse().find((e) => e.kind === "done");
  const found = String(last.summary ?? "").replace(/^Check done:\s*/, "");
  return [`Checked ${checks === 1 ? "once" : `${checks} times`}`, found].filter(Boolean).join(" · ");
}

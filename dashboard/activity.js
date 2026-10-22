// The agent's activity: what it checks, decides and pays, as it happens.
// The page asks the hub only for events after the last one it has.

import { HUB_PATH } from "./config.js";
import { call } from "./hub.js";

export const fetchActivity = (shop, after = 0, fetchImpl = fetch) =>
  call(`${HUB_PATH}/shops/${shop.toLowerCase()}/activity?after=${Number(after) || 0}`, { fetchImpl });

/** Who did it: the agent (Claude), its reflexes, the owner, a supplier, the contract, the ERP,
 * or a guardrail that stopped something. */
const ACTOR_OF = {
  agent: "AGENT", tool: "AGENT", anchor: "AGENT",
  reflex: "REFLEX", check: "REFLEX", done: "REFLEX", new: "REFLEX", changed: "REFLEX", waiting: "REFLEX",
  you: "YOU", answer: "YOU",
  signature: "SUPPLIER",
  sending: "CONTRACT", paid: "CONTRACT", limits: "CONTRACT",
  recorded: "ERP", closed: "ERP",
  not_paid: "GUARD", guard: "GUARD",
  error: "ERROR",
};
export const actorOf = (kind) => ACTOR_OF[kind] ?? "REFLEX";

/**
 * The feed as the page shows it: each time Claude was woken becomes one session (what woke it,
 * what it decided, what it said and cost); everything else stays a line. A session starts with
 * the "reflex" event that woke the agent and takes the agent's events written at the same time.
 */
export function timeline(events) {
  const out = [];
  for (const e of events) {
    const last = out.at(-1);
    if (e.kind === "reflex") {
      out.push({ type: "session", at: e.at, woke: e.text, tools: [], items: [], said: null, cost: null, failed: null });
    } else if (last?.type === "session" && last.at === e.at && ["agent", "error", "tool"].includes(e.kind)) {
      if (e.kind === "tool") last.tools.push(e);
      else if (e.kind === "error") last.failed = e.text;
      else if (e.cost !== undefined) Object.assign(last, { said: e.text, cost: e.cost || null, steps: e.steps ?? null });
      else last.items.push(e);
    } else {
      out.push({ type: "line", event: e });
    }
  }
  return out;
}

/** Why a session started, as the owner reads it. Older events said "Woke the agent." */
export const whyText = (text) =>
  String(text ?? "").replace(/^(Woke the agent|The agent started working)\.\s*/, "Why: ");

/** The agent's choice on an invoice, as a short label next to its own words. */
export function choiceLabel(e) {
  if (e.choice === "PAY_NOW") return "Pay now";
  if (e.choice === "SCHEDULE") return e.pay_on ? `Pay on ${e.pay_on}` : "Pay later";
  if (e.choice === "HOLD") return "Hold";
  if (e.choice === "ASK") return "Asks you";
  return "";
}

/** "Today", "Yesterday" or the date, in the viewer's time. */
export function dayOf(at, now = new Date()) {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  const key = (x) => x.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key(d) === key(now)) return "Today";
  if (key(d) === key(yesterday)) return "Yesterday";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "2026-10-09T20:19:45+00:00" -> "20:19:45" in the viewer's own time. */
export function timeOf(at, locale) {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(locale, { hour12: false });
}

/** Keep the newest events, no duplicates, in order. An event that joins quiet checks into one
 * line names the events it replaces: those are dropped. */
export function merge(current, incoming, max = 300) {
  const seen = new Set(current.map((e) => e.seq));
  const replaced = new Set(incoming.flatMap((e) => e.replaces ?? []));
  const all = [...current, ...incoming.filter((e) => !seen.has(e.seq))]
    .filter((e) => !replaced.has(e.seq))
    .sort((a, b) => a.seq - b.seq);
  return all.slice(-max);
}

/** A line's text; a line of quiet checks says since when, in the viewer's time. */
export function lineText(e) {
  if (!e.since || !e.checks || !e.summary) return e.text;
  return `Nothing changed in ${e.checks} checks since ${timeOf(e.since).slice(0, 5)}. ${e.summary}.`;
}

/**
 * Whether the agent is checking right now, or how long until its next check.
 * A "check" event with no "done"/"error" after it means a check is running.
 */
export function nextCheck(events, intervalMinutes, now = Date.now()) {
  const last = [...events].reverse().find((e) => ["check", "done", "error"].includes(e.kind));
  if (!last) return { state: "unknown" };
  if (last.kind === "check") return { state: "checking" };
  if (!intervalMinutes) return { state: "unknown" };
  const due = new Date(last.at).getTime() + intervalMinutes * 60_000;
  return { state: "waiting", seconds: Math.max(0, Math.round((due - now) / 1000)) };
}

export function countdown(seconds) {
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

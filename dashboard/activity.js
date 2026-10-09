// The agent's activity as a live terminal: what it checks, decides and pays, as it happens.
// The page asks the hub only for events after the last one it has.

import { HUB_PATH } from "./config.js";
import { call } from "./hub.js";

export const fetchActivity = (shop, after = 0, fetchImpl = fetch) =>
  call(`${HUB_PATH}/shops/${shop.toLowerCase()}/activity?after=${Number(after) || 0}`, { fetchImpl });

const LABELS = {
  check: "CHECK", done: "DONE", new: "NEW", changed: "UPDATE", closed: "CLOSED",
  sending: "SENDING", paid: "PAID", recorded: "ERP", not_paid: "NOT PAID", signature: "SIGNED",
  answer: "YOU", limits: "LIMITS", error: "ERROR", waiting: "WAITING",
};

export const labelOf = (kind) => LABELS[kind] ?? String(kind ?? "").toUpperCase();

/** Who did it: the agent (Claude), its reflexes, the owner, a supplier, the contract, the ERP,
 * or a guardrail that stopped something. */
const ACTOR_OF = {
  agent: "AGENT",
  reflex: "REFLEX", check: "REFLEX", done: "REFLEX", new: "REFLEX", changed: "REFLEX", waiting: "REFLEX",
  you: "YOU", answer: "YOU",
  signature: "SUPPLIER",
  sending: "CONTRACT", paid: "CONTRACT", limits: "CONTRACT",
  recorded: "ERP", closed: "ERP",
  not_paid: "GUARD", guard: "GUARD",
  error: "ERROR",
};
export const ACTORS = ["AGENT", "REFLEX", "YOU", "SUPPLIER", "CONTRACT", "ERP", "GUARD"];
export const actorOf = (kind) => ACTOR_OF[kind] ?? "REFLEX";

/** The events one filter chip shows: all, or one actor's. */
export const byActor = (events, actor) => (actor ? events.filter((e) => actorOf(e.kind) === actor) : events);

/** "2026-10-09T20:19:45+00:00" -> "20:19:45" in the viewer's own time. */
export function timeOf(at, locale) {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(locale, { hour12: false });
}

/** Keep the newest events, no duplicates, in order. */
export function merge(current, incoming, max = 300) {
  const seen = new Set(current.map((e) => e.seq));
  const all = [...current, ...incoming.filter((e) => !seen.has(e.seq))].sort((a, b) => a.seq - b.seq);
  return all.slice(-max);
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

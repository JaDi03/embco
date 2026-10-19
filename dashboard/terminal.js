// The agent's console: every check, every time the model was woken, what it decided and why,
// and the money it moved, as one line each, oldest first. This is the record of the agent at work.

import { actorOf, choiceLabel, lineText, timeOf, whyText } from "./activity.js";
import { feedOf, quietText } from "./feed.js";

const TONE_OF_CHOICE = { PAY_NOW: "ok", SCHEDULE: "ok", HOLD: "bad", ASK: "ask" };

/**
 * The lines, oldest first: {key, at, tag, text, tone, tx}. key is stable across repaints so new
 * lines can be told apart from the ones already shown.
 */
export function linesOf(events) {
  const lines = [];
  const items = feedOf(events);
  const inSession = new Set(items.filter((i) => i.type === "work").flatMap((i) => i.session.items.map((e) => e.tx)).filter(Boolean));
  for (const item of items) {
    if (item.type === "work") {
      const s = item.session;
      const at = s.at;
      let when = at;  // each step shows when it returned; what follows it, the last step's time
      lines.push({ key: `w:${at}`, at, tag: "WAKE", text: whyText(s.woke).replace(/^Why: /, ""), tone: "wake" });
      for (const t of s.tools ?? []) {  // each tool the model called, as it called it
        when = t.done_at ?? when;
        lines.push({ key: `t:${t.seq}`, at: when, tag: "TOOL", text: t.text, tone: t.refused ? "refused" : "tool" });
      }
      s.items.forEach((e, i) => {
        if (e.tx) {
          lines.push({ key: `$:${e.tx}`, at: when, tag: "MOVED", text: e.text, tone: "ok", tx: e.tx });
          return;
        }
        const label = choiceLabel(e);
        lines.push({ key: `d:${e.seq ?? `${at}:${i}`}`, at: when, tag: label ? "DECIDE" : "AGENT",
          text: label ? `${label.toUpperCase()} · ${e.text}` : e.text, tone: TONE_OF_CHOICE[e.choice] ?? "agent" });
      });
      if (s.failed) lines.push({ key: `f:${at}`, at: when, tag: "ERROR", text: s.failed, tone: "bad" });
      if (s.said) lines.push({ key: `s:${at}`, at: when, tag: "SAYS", text: s.said, tone: "say" });
      if (s.steps) lines.push({ key: `m:${at}`, at: when, tag: "", text: `session done · ${s.steps} steps`, tone: "dim" });
    } else if (item.type === "money") {
      const e = item.event;
      if (inSession.has(e.tx)) continue;  // shown inside its session, where the agent moved it
      lines.push({ key: `$:${e.tx}`, at: item.at, tag: e.kind === "agent" ? "MOVED" : "PAID", text: e.text, tone: "ok", tx: e.tx });
    } else if (item.type === "note") {
      const e = item.event;
      lines.push({ key: `n:${e.seq}`, at: e.at, tag: actorOf(e.kind), text: lineText(e), tone: e.kind === "error" ? "bad" : "note" });
    } else {
      const last = item.events.at(-1);
      lines.push({ key: `q:${item.events[0].seq}`, at: last.at, tag: "CHECK", text: quietText(item.events), tone: "dim" });
    }
  }
  return lines;
}

/** "HH:MM:SS" in the viewer's time. */
export const clock = (at) => timeOf(at);

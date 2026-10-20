// The numbers an accountant looks for first, from what the agent last saw: what is due this week,
// what is overdue, where the money is and what the planned payments leave in the wallet.
// Amounts are exact USDC units (bigint); dates are the shop's YYYY-MM-DD strings.

import { formatUsdc, parseUsdc } from "./units.js";

const WEEK_DAYS = 7;
const PLAN_DAYS = 14;
const PAID = new Set(["SUBMITTED", "COMPLETE", "RECORDED"]);

/** USDC units of a decimal string; 0n for zero, empty or unreadable. */
export function units(text) {
  try {
    return parseUsdc(String(text ?? "").trim());
  } catch {
    return 0n;
  }
}

/** today plus `days`, as YYYY-MM-DD. */
export function addDays(day, days) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** The shop's local date, YYYY-MM-DD. */
export function today(now = new Date()) {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

const unpaid = (d) => !PAID.has(d.payment?.status);

/** Due this week and overdue, among the bills not paid yet. */
export function billsSummary(decisions, day) {
  const week = addDays(day, WEEK_DAYS);
  const dueWeek = { units: 0n, count: 0 };
  const overdue = { units: 0n, count: 0 };
  for (const d of (decisions ?? []).filter(unpaid)) {
    if (!d.due_date) continue;
    const amount = units(d.amount);
    if (d.due_date < day) {
      overdue.units += amount;
      overdue.count += 1;
    } else if (d.due_date <= week) {
      dueWeek.units += amount;
      dueWeek.count += 1;
    }
  }
  return { dueWeek, overdue };
}

/** The one line a bill's row shows: the agent's own words when it decided, else what is missing. */
export function reasonOf(task) {
  const agent = task.decision.agent;
  if (agent?.question) return agent.question;
  if (agent?.reason) return agent.reason;
  const missing = task.steps.find((s) => s.state === "todo") ?? task.steps.find((s) => s.state === "waiting");
  if (missing) return missing.text;
  return task.payment?.text ?? "";
}

/** The date the bill's money leaves: the agent's chosen day, else its due date. */
export function payDay(decision) {
  return decision.agent?.choice === "SCHEDULE" && decision.agent.pay_on ? decision.agent.pay_on : decision.due_date;
}

/**
 * Every place the owner's USDC is, with what it is for. earn and unified are the hub's views
 * (strings in USDC); a view with an error or missing adds nothing.
 */
export function moneyRows({ wallet, earn, unified }) {
  const rows = [{ key: "wallet", name: "Your wallet on Arc", units: wallet ?? 0n, note: "Supplier payments leave from here." }];
  for (const v of (earn && !earn.error ? earn.vaults ?? [] : [])) {
    const open = units(v.max_deposit_usdc) > 0n;
    rows.push({
      key: `earn:${v.key}`, name: `Earn · ${v.name}`, units: units(v.balance_usdc), muted: !open && units(v.balance_usdc) === 0n,
      note: v.approved_in_earn ? "Approved by you. Withdrawn to your wallet when a bill needs it, with no fee."
        : open ? "Not approved for the agent." : "Not taking deposits.",
    });
  }
  const view = unified && !unified.error ? unified : {};
  for (const w of view.wallets ?? []) {
    const amount = units(w.in_wallet);
    rows.push({
      key: `wallet:${w.chain}`, name: `Your wallet on ${chainName(w.chain)}`, units: amount, muted: amount === 0n,
      note: w.funding
        ? `The agent may move up to ${formatUsdc(units(w.agent_may_move))} of it to your Gateway balance, as you approved.`
        : "Not set up for the agent on this chain.",
    });
  }
  for (const c of view.chains ?? []) {
    const amount = units(c.available) + units(c.arriving);
    rows.push({
      key: `chain:${c.chain}`, group: "gateway", name: chainName(c.chain), units: amount, muted: amount === 0n,
      note: c.agent_authorized ? "The agent can bring it to your wallet on Arc, with a fee." : "The agent is not authorized on this chain.",
    });
  }
  return rows;
}

/** "Ethereum_Sepolia" -> "Ethereum Sepolia". */
function chainName(chain) {
  return chain.replace(/_/g, " ");
}

/** Total of the rows, and what sits in Earn. */
export function moneyTotals(rows) {
  const total = rows.reduce((sum, r) => sum + r.units, 0n);
  const inEarn = rows.filter((r) => r.key.startsWith("earn:")).reduce((sum, r) => sum + r.units, 0n);
  return { total, inEarn };
}

/**
 * The payments the agent plans in the next two weeks, in date order, with what each leaves in
 * the wallet. Bills on hold are counted apart: they leave only if the owner releases them.
 */
export function cashPlan(decisions, wallet, day) {
  const end = addDays(day, PLAN_DAYS);
  const planned = [];
  const held = { units: 0n, count: 0 };
  for (const d of (decisions ?? []).filter(unpaid)) {
    const when = payDay(d);
    if (!when || when > end) continue;
    if (d.action !== "PAY" || d.agent?.choice === "HOLD") {
      held.units += units(d.amount);
      held.count += 1;
      continue;
    }
    planned.push({ day: when < day ? day : when, supplier: d.supplier, invoice: d.invoice, units: units(d.amount) });
  }
  planned.sort((a, b) => a.day.localeCompare(b.day) || a.invoice.localeCompare(b.invoice));
  let left = wallet ?? 0n;
  for (const row of planned) {
    left -= row.units;
    row.left = left;
  }
  const out = planned.reduce((sum, r) => sum + r.units, 0n);
  return { rows: planned, out, short: left < 0n ? -left : 0n, held };
}

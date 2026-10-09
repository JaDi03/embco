// What the owner has to do about each invoice, as a checklist: one place per invoice, with the
// question, every step still missing and the button for each step next to it.
//
// The agent says which steps are missing (its findings, by name); the contract says what the
// owner already did there (wallet approved, limits). A step done in the contract but not yet
// seen by the agent shows as "waiting for the agent's next check".

import { formatUsdc, parseUsdc } from "./units.js";

const SHORT = (w) => (w ? `${w.slice(0, 6)}...${w.slice(-4)}` : "");

export const SECTIONS = [
  { key: "needs", title: "Needs you", hint: "The agent will not pay these until you act." },
  { key: "paying", title: "Being paid", hint: "Approved: the agent pays them within your limits." },
  { key: "supplier", title: "Waiting for the supplier", hint: "The supplier has to confirm its wallet on its page." },
  { key: "held", title: "On hold: fix in ERPNext", hint: "Something in ERPNext does not add up. Fix it there; the agent looks again on its own." },
];

/** USDC units of a decimal amount written by the agent, rounded up to a whole cent. */
function units(amount) {
  try {
    return parseUsdc(String(amount));
  } catch {
    return null;
  }
}

/**
 * One task per decision.
 * facts: { wallet, approved, maxPerPayment, weeklyCap } from the supplier list and the contract
 * (bigints in USDC units); waiting: the agent has not read the owner's answer yet;
 * answer: the agent's last outcome for the owner's answer on this invoice.
 */
export function taskFor(decision, facts = {}, { waiting = false, answer = null } = {}) {
  const found = new Map((decision.findings ?? []).map((f) => [f.step, f]));
  const steps = [];
  const wallet = facts.wallet ?? null;
  const amountUnits = units(decision.amount);

  // 1. The supplier's wallet
  if (found.has("wallet_missing")) {
    steps.push({ key: "wallet", state: "todo", text: "There is no wallet for this supplier in ERPNext. Add it there: Bank Account, bank \"USDC on Arc\", account number = the wallet." });
  } else if (found.has("wallet_problem")) {
    steps.push({ key: "wallet", state: "todo", text: `The wallet in ERPNext cannot be used: ${found.get("wallet_problem").reason}.` });
  } else if (found.has("supplier_signature")) {
    steps.push({ key: "wallet", state: "waiting", text: `Waiting for the supplier to confirm its wallet ${SHORT(wallet)} on its page.`, action: { type: "notice" } });
  } else if (found.has("first_payment")) {
    steps.push({ key: "wallet", state: "done", text: `The supplier confirmed its wallet ${SHORT(wallet)} by signing.` });
  } else if (wallet) {
    steps.push({ key: "wallet", state: "done", text: `Wallet ${SHORT(wallet)}, already paid before.` });
  }

  // 2. Anything else to fix in ERPNext
  for (const f of decision.findings ?? []) {
    if (f.step === "order_receipt") {
      steps.push({ key: "order", state: "todo", text: `In ERPNext, link the invoice to its purchase order and receipt (${f.reason}).` });
    } else if (f.step === "other") {
      steps.push({ key: `other-${f.control}`, state: "todo", text: f.reason });
    }
  }

  // 3. The wallet approved in the contract (the contract refuses any other)
  const walletKnown = Boolean(wallet) && !found.has("wallet_missing") && !found.has("wallet_problem");
  if (walletKnown) {
    steps.push(facts.approved
      ? { key: "approved", state: "done", text: "Wallet approved in your contract." }
      : { key: "approved", state: "todo", text: "Approve this wallet in your contract: the contract pays only wallets you approved.", action: { type: "approve_wallet", wallet } });
  }

  // 4. The per-payment limit, offered once nothing in ERPNext is left to fix
  const fixFirst = steps.some((s) => s.state === "todo" && !s.action);
  const shown = amountUnits === null ? decision.amount : formatUsdc(amountUnits);
  const overForAgent = found.has("over_limit");
  const max = facts.maxPerPayment;
  const overOnChain = amountUnits !== null && typeof max === "bigint" ? amountUnits > max : overForAgent;
  if (overOnChain) {
    steps.push(fixFirst
      ? { key: "limit", state: "todo", text: `${shown} USDC is above your per-payment limit. Raise it once the steps above are fixed.` }
      : { key: "limit", state: "todo", text: `${shown} USDC is above your per-payment limit.`, action: { type: "raise_limit", amount: amountUnits } });
  } else if (overForAgent) {
    steps.push({ key: "limit", state: "waiting", text: "You raised the limit. The agent sees it in its next check.", action: { type: "check" } });
  } else {
    steps.push({ key: "limit", state: "done", text: "Within your per-payment limit." });
  }

  // 5. The owner's own answer, only when the agent asks something only a person can answer
  const question = found.get("first_payment") ?? (decision.findings ?? []).find(
    (f) => f.outcome === "ASK" && !["over_limit", "first_payment"].includes(f.step),
  );
  let decide = null;
  if (decision.action === "ASK" && question) {
    const blockedBy = steps.filter((s) => s.state !== "done");
    let state = blockedBy.length ? "locked" : "open";
    if (waiting) state = "sent";
    decide = {
      state,
      text: found.has("first_payment")
        ? "Your decision: this is the first payment to this wallet. Approve only if the supplier asked to be paid there (check on a phone number you already have)."
        : `Your decision: ${question.reason}.`,
      fingerprint: decision.fingerprint,
      refused: answer?.result === "REJECTED" ? answer.reason : null,
    };
  }

  return { decision, steps, decide, section: sectionOf(decision, steps), payment: paymentText(decision) };
}

function sectionOf(decision, steps) {
  if (decision.action === "PAY") return "paying";
  if (decision.action === "ASK") return "needs";
  if (steps.some((s) => s.key === "wallet" && s.state === "waiting") && !steps.some((s) => s.state === "todo")) return "supplier";
  return "held";
}

/** Where the money is, in words, for an invoice the agent decided to pay. */
export function paymentText(decision) {
  if (decision.action !== "PAY") return null;
  const p = decision.payment;
  if (!p) return { state: "next", text: "The agent pays it in its next check." };
  switch (p.status) {
    case "SUBMITTED": return { state: "sent", text: "Payment sent, waiting for Arc to confirm.", tx: p.tx_hash };
    case "COMPLETE": return { state: "paid", text: "Paid on Arc. Writing it into ERPNext.", tx: p.tx_hash };
    case "RECORDED": return { state: "paid", text: `Paid on Arc and recorded in ERPNext as ${p.erp_entry}.`, tx: p.tx_hash };
    case "BLOCKED": return { state: "blocked", text: `Not paid yet: ${p.reason}.` };
    case "FAILED": return { state: "blocked", text: `The payment failed and will be tried again: ${p.reason}.` };
    default: return { state: "next", text: "The agent pays it in its next check." };
  }
}

/** New limits that let this amount through: the weekly cap never ends below the per-payment one. */
export function raisedLimits(amountUnits, weeklyCap) {
  const max = BigInt(amountUnits);
  return { maxPerPayment: max, weeklyCap: weeklyCap >= max ? weeklyCap : max };
}

/** Tasks grouped by section, in the order the page shows them. */
export function groupTasks(tasks) {
  return SECTIONS.map((s) => ({ ...s, tasks: tasks.filter((t) => t.section === s.key) }))
    .filter((s) => s.tasks.length);
}

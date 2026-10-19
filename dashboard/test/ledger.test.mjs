import assert from "node:assert/strict";
import { test } from "node:test";

const ledger = await import("../ledger.js");

const USDC = (n) => BigInt(Math.round(n * 1_000_000));

function bill(invoice, amount, due, extra = {}) {
  return { invoice, supplier: `Supplier of ${invoice}`, amount, due_date: due, action: "PAY", payment: null, agent: null, ...extra };
}

test("due this week and overdue count only bills not paid yet", () => {
  const decisions = [
    bill("A", "58.0", "2026-10-14", { payment: { status: "RECORDED" } }),
    bill("B", "476.0", "2026-10-24"),
    bill("C", "310.0", "2026-10-21", { action: "HOLD" }),
    bill("D", "90.5", "2026-10-15"),
    bill("E", "40", "2026-11-30"),
  ];
  const { dueWeek, overdue } = ledger.billsSummary(decisions, "2026-10-19");
  assert.deepEqual(dueWeek, { units: USDC(786), count: 2 });
  assert.deepEqual(overdue, { units: USDC(90.5), count: 1 });
});

test("the row shows the agent's words first, then what is missing", () => {
  const steps = [{ state: "done", text: "ok" }, { state: "todo", text: "Approve this wallet" }];
  assert.equal(ledger.reasonOf({ decision: { agent: { reason: "Due today, all checks pass." } }, steps }), "Due today, all checks pass.");
  assert.equal(ledger.reasonOf({ decision: { agent: { question: "Pay it late?", reason: "x" } }, steps }), "Pay it late?");
  assert.equal(ledger.reasonOf({ decision: { agent: null }, steps }), "Approve this wallet");
});

test("where the money is: wallet, each vault and each chain, zero rows muted", () => {
  const rows = ledger.moneyRows({
    wallet: USDC(263.740334),
    earn: { vaults: [
      { key: "morpho", name: "Morpho USDC Vault", balance_usdc: "1500.000000", approved_in_earn: true, max_deposit_usdc: "100" },
      { key: "earnkit", name: "EarnKit USDC Vault", balance_usdc: "0.000000", approved_in_earn: false, max_deposit_usdc: "0" },
    ] },
    unified: { chains: [{ chain: "Ethereum_Sepolia", available: "0.137560", arriving: "0.000000", agent_authorized: true }] },
  });
  assert.deepEqual(rows.map((r) => [r.key, r.units, Boolean(r.muted)]), [
    ["wallet", USDC(263.740334), false],
    ["earn:morpho", USDC(1500), false],
    ["earn:earnkit", 0n, true],
    ["chain:Ethereum_Sepolia", USDC(0.13756), false],
  ]);
  assert.deepEqual(ledger.moneyTotals(rows), { total: USDC(1763.877894), inEarn: USDC(1500) });
});

test("a view with an error adds no rows", () => {
  const rows = ledger.moneyRows({ wallet: 5n, earn: { error: "node down" }, unified: null });
  assert.equal(rows.length, 1);
});

test("the cash plan follows the agent's dates and says how much is short", () => {
  const decisions = [
    bill("ACC-PINV-2026-00017", "476.0", "2026-10-24", { agent: { choice: "SCHEDULE", pay_on: "2026-10-20" } }),
    bill("NP-0412", "92.4", "2026-10-28"),
    bill("FD-7781", "310.0", "2026-10-21", { agent: { choice: "HOLD" } }),
    bill("LATE", "10", "2026-12-01"),
  ];
  const plan = ledger.cashPlan(decisions, USDC(263.74), "2026-10-19");
  assert.deepEqual(plan.rows.map((r) => [r.day, r.invoice, r.left]), [
    ["2026-10-20", "ACC-PINV-2026-00017", USDC(-212.26)],
    ["2026-10-28", "NP-0412", USDC(-304.66)],
  ]);
  assert.equal(plan.out, USDC(568.4));
  assert.equal(plan.short, USDC(304.66));
  assert.deepEqual(plan.held, { units: USDC(310), count: 1 });
});

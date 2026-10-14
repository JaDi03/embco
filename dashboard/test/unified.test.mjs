import { test } from "node:test";
import assert from "node:assert/strict";
import { unifiedView } from "../unified.js";

const lastRun = (chains) => ({ unified_balance: { chains } });

test("each chain the agent read, with what is there and whether it may spend it", () => {
  const view = unifiedView(lastRun([
    { chain: "Base_Sepolia", available: "120.500000", arriving: "0.000000", agent_authorized: true },
    { chain: "Arbitrum_Sepolia", available: "30.000000", arriving: "5.000000", agent_authorized: false },
    { chain: "Unichain_Sepolia", available: "0.000000", arriving: "0.000000", agent_authorized: false },
  ]));
  assert.equal(view.state, "ok");
  assert.equal(view.text, "150.5 USDC on other chains; the agent may bring 120.5 of it to your wallet on Arc.");
  assert.deepEqual(view.rows.map((r) => [r.name, r.available, r.arriving, r.authorized]), [
    ["Base Sepolia", 120500000n, 0n, true],
    ["Arbitrum Sepolia", 30000000n, 5000000n, false],
    ["Unichain Sepolia", 0n, 0n, false],
  ]);
});

test("nothing deposited yet says what to do first", () => {
  const view = unifiedView(lastRun([{ chain: "Base_Sepolia", available: "0.000000", arriving: "0.000000", agent_authorized: false }]));
  assert.equal(view.text, "No USDC on other chains yet. Deposit on a chain, then authorize the agent there.");
});

test("not connected or unreadable is said plainly, with no rows", () => {
  assert.equal(unifiedView(null).state, "off");
  assert.equal(unifiedView({ unified_balance: null }).rows.length, 0);
  const failed = unifiedView({ unified_balance: { error: "the Unified Balance Kit did not run" } });
  assert.equal(failed.state, "error");
  assert.equal(failed.text, "Could not read it: the Unified Balance Kit did not run");
});

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

test("only chains with a funding factory are offered", async () => {
  const { fundingChains } = await import("../unified.js");
  assert.deepEqual(fundingChains({ Base_Sepolia: "0xabc", Ethereum_Sepolia: "" }), ["Base_Sepolia"]);
});

test("the owner's wallets say whether each chain is set up and what the agent may move", async () => {
  const { walletsView } = await import("../unified.js");
  const rows = walletsView({ unified_balance: { wallets: [
    { chain: "Base_Sepolia", in_wallet: "400", funding: "0xf1", approved: "1000", agent_may_move: "150" },
    { chain: "Ethereum_Sepolia", in_wallet: "80", funding: null, approved: "0", agent_may_move: "0" },
  ] } });
  assert.deepEqual(rows.map((r) => [r.name, r.inWallet, r.setUp, r.agentMayMove]), [
    ["Base Sepolia", 400000000n, true, 150000000n],
    ["Ethereum Sepolia", 80000000n, false, 0n],
  ]);
  assert.deepEqual(walletsView({ unified_balance: { wallets: { error: "down" } } }), []);
});

test("setting up a chain is create, approve and authorize, skipping what is done", async () => {
  const { setupCalls } = await import("../unified.js");
  const base = {
    factory: "0x" + "fa".repeat(20), funding: "0x" + "f1".repeat(20), usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    gateway: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9", agent: "0x" + "a9".repeat(20),
    maxPerMove: 200000000n, weeklyCap: 500000000n,
  };
  const all = setupCalls({ ...base, create: true, delegate: true });
  assert.deepEqual(all.map((c) => [c.to, c.data.slice(0, 10)]), [
    [base.factory, "0x5165da30"],  // create(address,uint256,uint256)
    [base.usdc, "0x095ea7b3"],  // approve(address,uint256)
    [base.gateway, "0xe909ebfa"],  // addDelegate(address,address)
  ]);
  assert.ok(all[1].data.includes(base.funding.slice(2)));  // approves the owner's own funding contract
  assert.deepEqual(setupCalls({ ...base, create: false, delegate: false }).map((c) => c.to), [base.usdc]);
});

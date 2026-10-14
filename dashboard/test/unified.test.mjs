import { test } from "node:test";
import assert from "node:assert/strict";
import { chainStatus, turnOffCalls } from "../unified.js";

const SHOP = { agent: "0x" + "a9".repeat(20), maxPerMove: 100000000n, weeklyCap: 400000000n };
const READY = {
  funding: "0x" + "f1".repeat(20), fundingAgent: SHOP.agent.toUpperCase().replace("0X", "0x"), allowance: 1n,
  authorized: true, maxPerMove: SHOP.maxPerMove, weeklyCap: SHOP.weeklyCap,
};

test("a chain is on only when the agent may really move the owner's USDC there", () => {
  assert.equal(chainStatus(READY, SHOP), "on");
  assert.equal(chainStatus({ ...READY, funding: null }, SHOP), "off");
  assert.equal(chainStatus({ ...READY, allowance: 0n }, SHOP), "off");  // turned off: approval removed
  assert.equal(chainStatus({ ...READY, authorized: false }, SHOP), "off");  // half set up
  assert.equal(chainStatus({ ...READY, fundingAgent: "0x" + "b2".repeat(20) }, SHOP), "off");  // another agent
  assert.equal(chainStatus({ ...READY, maxPerMove: 150000000n }, SHOP), "update");  // the shop's limits changed
});

test("turning a chain off removes the approval and the agent's signer in Gateway, nothing else", () => {
  const usdc = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
  const gateway = "0x0077777d7EBA4688BDeF3E311b846F25870A19B9";
  const signer = "0x" + "e0".repeat(20);
  const calls = turnOffCalls({ funding: READY.funding, usdc, gateway, signer });
  assert.deepEqual(calls.map((c) => [c.to, c.data.slice(0, 10)]), [
    [usdc, "0x095ea7b3"],  // approve(address,uint256)
    [gateway, "0x020d308d"],  // removeDelegate(address,address)
  ]);
  assert.ok(calls[0].data.endsWith("0".repeat(64)));  // approves zero
  assert.ok(calls[1].data.includes(signer.slice(2)));
  assert.deepEqual(turnOffCalls({ funding: null, usdc, gateway, signer }).map((c) => c.to), [gateway]);
});

test("only chains with a funding factory are offered", async () => {
  const { fundingChains } = await import("../unified.js");
  assert.deepEqual(fundingChains({ Base_Sepolia: "0xabc", Ethereum_Sepolia: "" }), ["Base_Sepolia"]);
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

test("a gasless agent's funding contract is the agent's, and Gateway authorizes its signer", async () => {
  const { setupCalls } = await import("../unified.js");
  const agent = "0x" + "a9".repeat(20);
  const signer = "0x" + "e0".repeat(20);
  const [create, , authorize] = setupCalls({
    factory: "0x" + "fa".repeat(20), funding: "0x" + "f1".repeat(20), usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    gateway: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9", agent, signer,
    maxPerMove: 1n, weeklyCap: 2n, create: true, delegate: true,
  });
  assert.ok(create.data.includes(agent.slice(2)) && !create.data.includes(signer.slice(2)));
  assert.ok(authorize.data.includes(signer.slice(2)) && !authorize.data.includes(agent.slice(2)));
});

test("a chain set up with other limits gets the shop's limits in the same confirmation", async () => {
  const { setupCalls } = await import("../unified.js");
  const base = {
    factory: "0x" + "fa".repeat(20), funding: "0x" + "f1".repeat(20), usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    gateway: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9", agent: "0x" + "a9".repeat(20),
    maxPerMove: 150000000n, weeklyCap: 600000000n, create: false, delegate: false,
  };
  const calls = setupCalls({ ...base, setLimits: true });
  assert.deepEqual(calls.map((c) => [c.to, c.data.slice(0, 10)]), [
    [base.funding, "0xc4590d3f"],  // setLimits(uint256,uint256)
    [base.usdc, "0x095ea7b3"],
  ]);
  assert.deepEqual(setupCalls(base).map((c) => c.to), [base.usdc]);  // same limits: nothing to set
});

test("turning a chain on again gives its funding contract the shop's agent if it had another", async () => {
  const { setupCalls } = await import("../unified.js");
  const funding = "0x" + "f1".repeat(20);
  const calls = setupCalls({
    factory: "0x" + "fa".repeat(20), funding, usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    gateway: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9", agent: "0x" + "a9".repeat(20),
    maxPerMove: 1n, weeklyCap: 2n, create: false, delegate: true, setAgent: true, setLimits: true,
  });
  assert.deepEqual(calls.map((c) => c.data.slice(0, 10)), ["0xbcf685ed", "0xc4590d3f", "0x095ea7b3", "0xe909ebfa"]);
  assert.ok(calls.slice(0, 2).every((c) => c.to === funding));
});

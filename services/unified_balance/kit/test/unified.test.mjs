import { test } from "node:test";
import assert from "node:assert/strict";
import { DESTINATION, missingChains, run, sourceChains, spendParams } from "../unified.mjs";

const OWNER = "0x2471658CD7A7B10d4C92113B0777dA59317e839a";
const AGENT = "0xeAc91C9491F0D391c80f17C4267A3329Ad7Ba862";

test("a spend is signed by the agent from the owner's balance and minted to the owner on Arc", () => {
  const allocations = [{ chain: "Base_Sepolia", amount: "300.000000" }];
  const params = spendParams({ adapter: "adapter", owner: OWNER, agent: AGENT, amount: "300.000000", allocations });
  assert.deepEqual(params.from, [{ adapter: "adapter", address: AGENT, sourceAccount: OWNER, allocations }]);
  assert.deepEqual(params.to, { chain: "Arc_Testnet", recipientAddress: OWNER, useForwarder: true });
  assert.equal(params.amount, "300.000000");
});

test("the source chains are EVM testnets the agent's Circle wallet signs on, never Arc", () => {
  const chains = sourceChains();
  assert.ok(chains.includes("Base_Sepolia"));
  assert.ok(chains.includes("Arbitrum_Sepolia"));
  assert.ok(!chains.includes(DESTINATION));
  assert.ok(!chains.includes("Solana_Devnet"));
  assert.ok(!chains.some((c) => !c.includes("Sepolia") && !c.includes("Testnet") && !c.includes("Fuji")));
});

test("an unknown command is an answer, not a crash", async () => {
  assert.deepEqual(await run("send_anywhere", {}, {}), { error: "unknown command send_anywhere" });
});

test("only the chains where the agent's wallet does not exist yet are derived", () => {
  const wallets = [{ blockchain: "ARC-TESTNET" }, { blockchain: "BASE-SEPOLIA" }];
  assert.deepEqual(missingChains(["Base_Sepolia", "Ethereum_Sepolia"], wallets), ["Ethereum_Sepolia"]);
  assert.deepEqual(missingChains(["Base_Sepolia"], wallets), []);
});

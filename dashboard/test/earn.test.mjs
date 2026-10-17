import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const { setupEarnCalls, turnOffEarnCalls, vaultStatus } = await import("../earn.js");

const SHOP = { agent: "0x" + "a9".repeat(20), maxPerMove: 300000000n, weeklyCap: 1000000000n };
const READY = {
  earn: "0x" + "e1".repeat(20),
  earnAgent: SHOP.agent.toUpperCase().replace("0X", "0x"),
  usdcAllowance: 1000000000n,
  approvedVault: true,
  shareAllowance: 1n,
  maxPerPark: SHOP.maxPerMove,
  weeklyCap: SHOP.weeklyCap,
};

test("a vault is on only when ShopEarn, USDC allowance, vault allowlist, and share allowance are ready", () => {
  assert.equal(vaultStatus(READY, SHOP), "on");
  assert.equal(vaultStatus({ ...READY, earn: null }, SHOP), "off");
  assert.equal(vaultStatus({ ...READY, usdcAllowance: 0n }, SHOP), "off");
  assert.equal(vaultStatus({ ...READY, approvedVault: false }, SHOP), "off");
  assert.equal(vaultStatus({ ...READY, shareAllowance: 0n }, SHOP), "off");
  assert.equal(vaultStatus({ ...READY, earnAgent: "0x" + "b2".repeat(20) }, SHOP), "off");
  assert.equal(vaultStatus({ ...READY, maxPerPark: 150000000n }, SHOP), "update");
});

test("setting up a vault creates ShopEarn, approves USDC, whitelists the vault, and approves shares", () => {
  const base = {
    factory: "0x" + "fa".repeat(20),
    earn: "0x" + "e1".repeat(20),
    usdc: "0x3600000000000000000000000000000000000000",
    vault: "0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc",
    agent: SHOP.agent,
    maxPerPark: SHOP.maxPerMove,
    weeklyCap: SHOP.weeklyCap,
  };
  const all = setupEarnCalls({ ...base, create: true });
  assert.deepEqual(all.map((c) => [c.to, c.data.slice(0, 10)]), [
    [base.factory, "0x5165da30"], // create(address,uint256,uint256)
    [base.usdc, "0x095ea7b3"], // approve(address,uint256)
    [base.earn, "0x627f66c6"], // setVault(address,bool)
    [base.vault, "0x095ea7b3"], // approve(address,uint256) on the ERC-4626 vault shares
  ]);

  const updateOnly = setupEarnCalls({
    ...base,
    create: false,
    setAgent: true,
    setLimits: true,
    approveUsdc: false,
    approveVault: false,
    approveShares: false,
  });
  assert.deepEqual(updateOnly.map((c) => [c.to, c.data.slice(0, 10)]), [
    [base.earn, "0xbcf685ed"], // setAgent(address)
    [base.earn, "0xc4590d3f"], // setLimits(uint256,uint256)
  ]);
});

test("turning a vault off revokes the vault in ShopEarn and share allowance, and USDC only when last", () => {
  const earn = "0x" + "e1".repeat(20);
  const vault = "0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc";
  const usdc = "0x3600000000000000000000000000000000000000";

  const keepOther = turnOffEarnCalls({ earn, vault, usdc, otherVaultsOn: true });
  assert.deepEqual(keepOther.map((c) => [c.to, c.data.slice(0, 10)]), [
    [earn, "0x627f66c6"], // setVault(vault, false)
    [vault, "0x095ea7b3"], // vault.approve(earn, 0)
  ]);
  assert.ok(keepOther[1].data.endsWith("0".repeat(64)));

  const lastOff = turnOffEarnCalls({ earn, vault, usdc, otherVaultsOn: false });
  assert.equal(lastOff.length, 3);
  assert.equal(lastOff[2].to, usdc);
  assert.ok(lastOff[2].data.endsWith("0".repeat(64)));
});

test("withdrawAllCalls redeems every vault where the owner holds shares directly to the owner", async () => {
  const { withdrawAllCalls } = await import("../earn.js");
  const owner = "0x" + "11".repeat(20);
  const calls = withdrawAllCalls({
    owner,
    vaults: [
      { address: "0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc", shares: 250000000n },
      { address: "0xaaBbEf1d3971C710276Ed41EC791BBE14Cdb8e88", shares: 0n },
    ],
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].to, "0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc");
  assert.equal(calls[0].data.slice(0, 10), "0xba087652"); // redeem(uint256,address,address)
  assert.ok(calls[0].data.includes(owner.slice(2)));
});

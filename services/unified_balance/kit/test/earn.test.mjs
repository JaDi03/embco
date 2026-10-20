import { test } from "node:test";
import assert from "node:assert/strict";
import { vaultRow, vaults } from "../earn.mjs";

// What Circle's Earn Kit answered for Arc testnet on 2026-10-20
const MORPHO = { vaultAddress: "0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc", name: "MockMorphoVault (Arc Testnet)", protocol: "MORPHO",
  currentApy: 0.035, vaultFee: 0, status: "active", liquidity: "1000000.0" };

test("each vault becomes a plain row with its APY as text", () => {
  assert.deepEqual(vaultRow({ ...MORPHO, vaultAddress: MORPHO.vaultAddress.toUpperCase().replace("0X", "0x") }), {
    vault: MORPHO.vaultAddress, name: "MockMorphoVault (Arc Testnet)", protocol: "MORPHO",
    current_apy: "0.035", vault_fee: "0", status: "active", liquidity: "1000000.0" });
});

test("vaults are asked for on Arc, sorted by APY", async () => {
  const asked = [];
  const kit = { exploreVaults: async (params) => { asked.push(params); return { vaults: [MORPHO] }; } };
  const answer = await vaults({}, {}, kit);
  assert.deepEqual(asked, [{ chain: "Arc_Testnet", sortBy: "apy" }]);
  assert.equal(answer.vaults[0].current_apy, "0.035");
});

// The Earn vaults on Arc as Circle's Earn Kit lists them: each vault's current APY, fee, status
// and liquidity. Read only: no wallet, no key. The agent weighs these numbers before it parks.

import { EarnKit } from "@circle-fin/earn-kit";

export const EARN_CHAIN = "Arc_Testnet";

/** One plain row per vault, addresses in lower case so the agent's vault list can match them. */
export function vaultRow(v) {
  return {
    vault: String(v.vaultAddress).toLowerCase(),
    name: v.name,
    protocol: v.protocol,
    current_apy: String(v.currentApy ?? ""),
    vault_fee: String(v.vaultFee ?? ""),
    status: v.status,
    liquidity: String(v.liquidity ?? ""),
  };
}

export async function vaults(_input, _env, kit = new EarnKit()) {
  const { vaults: found } = await kit.exploreVaults({ chain: EARN_CHAIN, sortBy: "apy" });
  return { vaults: found.map(vaultRow) };
}

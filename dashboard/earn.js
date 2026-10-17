// Idle USDC yield on Arc Testnet through ERC-4626 vaults (Morpho, EarnKit) and the owner's
// zero-custody ShopEarn contract. Neither USDC nor vault shares ever sit in ShopEarn or the agent:
// both live in the owner's wallet, and the owner turns each vault on or off in MetaMask.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { EARN_ABI, EARN_FACTORY_ABI, ERC4626_ABI, USDC_ABI } from "./abi.js";
import { ARC_TESTNET, EARN_FACTORY_ADDRESS, EARN_VAULTS, USDC_ADDRESS } from "./config.js";
import { read } from "./shop.js";
import { AUTHORIZE_ALL } from "./units.js";

const BATCH_POLL_MS = 2000;
const BATCH_WAIT_MS = 180000;

/** What a vault's switch shows: "on" when the agent may park and unpark the owner's USDC there
 * with the shop's limits, "update" when it may but with other limits, "off" otherwise. */
export function vaultStatus(
  { earn, earnAgent, usdcAllowance, approvedVault, shareAllowance, maxPerPark, weeklyCap },
  shop,
) {
  const ready = Boolean(earn) && usdcAllowance > 0n && approvedVault && shareAllowance > 0n
    && String(earnAgent).toLowerCase() === String(shop.agent).toLowerCase();
  if (!ready) return "off";
  return maxPerPark === shop.maxPerMove && weeklyCap === shop.weeklyCap ? "on" : "update";
}

/** Read the owner's ShopEarn contract and ERC-4626 vault positions on Arc Testnet. */
export async function readEarn(shop, { factory = EARN_FACTORY_ADDRESS, vaults = EARN_VAULTS } = {}) {
  return read(async (provider) => {
    const usdc = new ethers.Contract(USDC_ADDRESS, USDC_ABI, provider);
    const factoryContract = new ethers.Contract(factory, EARN_FACTORY_ABI, provider);
    const earns = await factoryContract.earnsOf(shop.owner);
    const earn = earns.at(-1) ?? null;

    let earnAgent = null;
    let usdcAllowance = 0n;
    let maxPerPark = 0n;
    let weeklyCap = 0n;
    if (earn) {
      const earnContract = new ethers.Contract(earn, EARN_ABI, provider);
      [earnAgent, usdcAllowance, maxPerPark, weeklyCap] = await Promise.all([
        earnContract.agent(),
        usdc.allowance(shop.owner, earn),
        earnContract.maxPerPark(),
        earnContract.weeklyCap(),
      ]);
    }

    const rows = await Promise.all(vaults.map(async (v) => {
      const vaultContract = new ethers.Contract(v.address, ERC4626_ABI, provider);
      const shares = await vaultContract.balanceOf(shop.owner);
      const assets = shares > 0n ? await vaultContract.convertToAssets(shares) : 0n;
      let approvedVault = false;
      let shareAllowance = 0n;
      if (earn) {
        const earnContract = new ethers.Contract(earn, EARN_ABI, provider);
        [approvedVault, shareAllowance] = await Promise.all([
          earnContract.approvedVault(v.address),
          vaultContract.allowance(shop.owner, earn),
        ]);
      }
      const status = vaultStatus(
        { earn, earnAgent, usdcAllowance, approvedVault, shareAllowance, maxPerPark, weeklyCap },
        shop,
      );
      return { ...v, shares, assets, earn, status };
    }));

    return { earn, maxPerPark, weeklyCap, vaults: rows };
  });
}

/** The calls that let the agent park and unpark the owner's USDC in a vault on Arc, in order:
 * create the owner's ShopEarn (or update its agent/limits), approve USDC to ShopEarn, whitelist
 * the vault in ShopEarn, and approve the vault's shares to ShopEarn so the agent can unpark. */
export function setupEarnCalls({
  factory,
  earn,
  create,
  usdc = USDC_ADDRESS,
  vault,
  agent,
  maxPerPark,
  weeklyCap,
  setAgent = false,
  setLimits = false,
  approveUsdc = true,
  approveVault = true,
  approveShares = true,
}) {
  const calls = [];
  const earnIface = new ethers.Interface(EARN_ABI);
  const earnCall = (name, args) =>
    calls.push({ to: earn, data: earnIface.encodeFunctionData(name, args), value: "0x0" });

  if (create) {
    const data = new ethers.Interface(EARN_FACTORY_ABI).encodeFunctionData(
      "create",
      [agent, maxPerPark, weeklyCap],
    );
    calls.push({ to: factory, data, value: "0x0" });
  } else {
    if (setAgent) earnCall("setAgent", [agent]);
    if (setLimits) earnCall("setLimits", [maxPerPark, weeklyCap]);
  }
  if (approveUsdc) {
    calls.push({
      to: usdc,
      data: new ethers.Interface(USDC_ABI).encodeFunctionData("approve", [earn, AUTHORIZE_ALL]),
      value: "0x0",
    });
  }
  if (approveVault) earnCall("setVault", [vault, true]);
  if (approveShares) {
    calls.push({
      to: vault,
      data: new ethers.Interface(ERC4626_ABI).encodeFunctionData("approve", [earn, AUTHORIZE_ALL]),
      value: "0x0",
    });
  }
  return calls;
}

/** The calls that stop the agent from using a vault: remove the vault from ShopEarn's allowlist,
 * revoke ShopEarn's share allowance on that vault, and when no other vault stays on, revoke
 * ShopEarn's USDC allowance too. */
export function turnOffEarnCalls({ earn, vault, usdc = USDC_ADDRESS, otherVaultsOn = true }) {
  const calls = [];
  if (earn) {
    calls.push({
      to: earn,
      data: new ethers.Interface(EARN_ABI).encodeFunctionData("setVault", [vault, false]),
      value: "0x0",
    });
    calls.push({
      to: vault,
      data: new ethers.Interface(ERC4626_ABI).encodeFunctionData("approve", [earn, 0n]),
      value: "0x0",
    });
    if (!otherVaultsOn) {
      calls.push({
        to: usdc,
        data: new ethers.Interface(USDC_ABI).encodeFunctionData("approve", [earn, 0n]),
        value: "0x0",
      });
    }
  }
  return calls;
}

async function waitForCalls(provider, id) {
  for (let waited = 0; waited < BATCH_WAIT_MS; waited += BATCH_POLL_MS) {
    const status = await provider.request({ method: "wallet_getCallsStatus", params: [id] });
    if (status.status === 200) return status;
    if (status.status >= 400) throw new Error(`The wallet could not complete it (status ${status.status}).`);
    await new Promise((resolve) => setTimeout(resolve, BATCH_POLL_MS));
  }
  throw new Error("Still waiting for Arc; check your wallet's activity, then look again.");
}

async function sendCalls(provider, signer, owner, calls) {
  const chainId = ARC_TESTNET.chainIdHex;
  const capabilities = await provider.request({ method: "wallet_getCapabilities", params: [owner, [chainId]] })
    .catch(() => ({}));
  const atomic = capabilities?.[chainId]?.atomic?.status;
  if (atomic === "supported" || atomic === "ready") {
    const sent = await provider.request({
      method: "wallet_sendCalls",
      params: [{ version: "2.0.0", from: owner, chainId, atomicRequired: true, calls }],
    });
    await waitForCalls(provider, sent.id ?? sent.batchId);
  } else {
    for (const call of calls) await (await signer.sendTransaction({ to: call.to, data: call.data })).wait();
  }
}

/** Enable a vault on Arc for the shop's agent: creates ShopEarn if needed, syncs its agent and
 * limits with the shop's, approves USDC and vault shares, and whitelists the vault. */
export async function setUpVault(
  provider,
  vault,
  { agent, maxPerMove: maxPerPark, weeklyCap },
  factoryAddress = EARN_FACTORY_ADDRESS,
) {
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const factory = new ethers.Contract(factoryAddress, EARN_FACTORY_ABI, browser);
  const existing = (await factory.earnsOf(owner)).at(-1);
  const earn = existing ?? (await factory.predict(owner, agent, maxPerPark, weeklyCap));
  const usdc = new ethers.Contract(USDC_ADDRESS, USDC_ABI, browser);
  const vaultContract = new ethers.Contract(vault, ERC4626_ABI, browser);

  let setLimits = false;
  let setAgent = false;
  let approveUsdc = true;
  let approveVault = true;
  let approveShares = true;

  if (existing) {
    const current = new ethers.Contract(existing, EARN_ABI, browser);
    const [park, week, itsAgent, usdcAllow, vaultOk, shareAllow] = await Promise.all([
      current.maxPerPark(),
      current.weeklyCap(),
      current.agent(),
      usdc.allowance(owner, existing),
      current.approvedVault(vault),
      vaultContract.allowance(owner, existing),
    ]);
    setLimits = park !== maxPerPark || week !== weeklyCap;
    setAgent = itsAgent.toLowerCase() !== agent.toLowerCase();
    approveUsdc = usdcAllow < weeklyCap;
    approveVault = !vaultOk;
    approveShares = shareAllow === 0n;
  }

  const calls = setupEarnCalls({
    factory: factoryAddress,
    earn,
    create: !existing,
    usdc: USDC_ADDRESS,
    vault,
    agent,
    maxPerPark,
    weeklyCap,
    setAgent,
    setLimits,
    approveUsdc,
    approveVault,
    approveShares,
  });
  if (calls.length) await sendCalls(provider, signer, owner, calls);
  return earn;
}

/** Disable a vault on Arc so the agent cannot park or unpark in it. */
export async function turnOffVault(
  provider,
  vault,
  { otherVaultsOn = true } = {},
  factoryAddress = EARN_FACTORY_ADDRESS,
) {
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const factory = new ethers.Contract(factoryAddress, EARN_FACTORY_ABI, browser);
  const earn = (await factory.earnsOf(owner)).at(-1) ?? null;
  const calls = turnOffEarnCalls({ earn, vault, usdc: USDC_ADDRESS, otherVaultsOn });
  if (calls.length) await sendCalls(provider, signer, owner, calls);
}

/** Direct owner withdrawal: redeems all of the owner's shares in the ERC-4626 vault straight back
 * to the owner's wallet on Arc, without going through the agent. */
export async function withdrawFromVault(provider, vault) {
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const contract = new ethers.Contract(vault, ERC4626_ABI, signer);
  const shares = await contract.balanceOf(owner);
  if (shares === 0n) throw new Error("You have no shares in this vault to withdraw.");
  const tx = await contract.redeem(shares, owner, owner);
  await tx.wait();
  return tx.hash;
}

/** Calls that redeem all of the owner's shares across every vault with a positive balance straight
 * to the owner's wallet on Arc. */
export function withdrawAllCalls({ owner, vaults }) {
  const iface = new ethers.Interface(ERC4626_ABI);
  return vaults
    .filter((v) => v.shares > 0n)
    .map((v) => ({
      to: v.address,
      data: iface.encodeFunctionData("redeem", [v.shares, owner, owner]),
      value: "0x0",
    }));
}

/** Withdraw all of the owner's capital from every Arc Earn vault directly to MetaMask. */
export async function withdrawAllFromEarn(provider, vaults = EARN_VAULTS) {
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const withShares = await Promise.all(vaults.map(async (v) => {
    const contract = new ethers.Contract(v.address, ERC4626_ABI, browser);
    return { address: v.address, shares: await contract.balanceOf(owner) };
  }));
  const calls = withdrawAllCalls({ owner, vaults: withShares });
  if (!calls.length) throw new Error("You have no funds in Arc Earn vaults to withdraw.");
  await sendCalls(provider, signer, owner, calls);
}

// The chains the agent can pay from: USDC the owner keeps on other chains, which the agent brings
// to the owner's wallet on Arc through their Unified Balance (Circle Gateway) when an invoice needs
// it. The page reads each chain itself, and the owner turns a chain on or off with their own
// browser wallet. Circle's Unified Balance Kit is loaded only when needed.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { FUNDING_ABI, FUNDING_FACTORY_ABI, GATEWAY_WALLET_ABI, USDC_ABI } from "./abi.js";
import { FUNDING_FACTORIES, GATEWAY_WALLET_TESTNET } from "./config.js";
import { AUTHORIZE_ALL } from "./units.js";

const KIT = "./vendor/unified-balance-kit-1.8.0/index.js";
let loaded = null;

function kit() {
  loaded ??= import(KIT);
  return loaded;
}

/** The connected wallet on the chain, through the kit's viem adapter. */
async function adapterOn(provider, chain) {
  const { createViemAdapterFromProvider, resolveChainIdentifier } = await kit();
  const definition = resolveChainIdentifier(chain);
  const adapter = await createViemAdapterFromProvider({ provider, capabilities: { supportedChains: [definition] } });
  await adapter.ensureChain(definition);
  return adapter;
}

/** The chains where the owner can let the agent move funds: those with a funding factory. */
export function fundingChains(factories = FUNDING_FACTORIES) {
  return Object.entries(factories).filter(([, address]) => address).map(([chain]) => chain);
}

/** What a chain's switch shows: "on" when the agent may move the owner's USDC there with the
 * shop's limits, "update" when it may but with other limits, "off" otherwise (also half set up). */
export function chainStatus({ funding, fundingAgent, allowance, authorized, maxPerMove, weeklyCap }, shop) {
  const ready = Boolean(funding) && allowance > 0n && authorized
    && String(fundingAgent).toLowerCase() === String(shop.agent).toLowerCase();
  if (!ready) return "off";
  return maxPerMove === shop.maxPerMove && weeklyCap === shop.weeklyCap ? "on" : "update";
}

/** Each chain the agent could pay from, read on that chain's own node: the owner's USDC there and
 * its switch. `shop` holds the owner, the agent, its signer and the shop's limits. */
export async function readChains(shop, factories = FUNDING_FACTORIES) {
  const { resolveChainIdentifier } = await kit();
  return Promise.all(fundingChains(factories).map(async (chain) => {
    const definition = resolveChainIdentifier(chain);
    const node = new ethers.JsonRpcProvider(definition.rpcEndpoints[0], definition.chainId, { staticNetwork: true });
    const usdc = new ethers.Contract(definition.usdcAddress, USDC_ABI, node);
    const factory = new ethers.Contract(factories[chain], FUNDING_FACTORY_ABI, node);
    const gateway = new ethers.Contract(GATEWAY_WALLET_TESTNET, GATEWAY_WALLET_ABI, node);
    const [inWallet, fundings, authorized] = await Promise.all([
      usdc.balanceOf(shop.owner), factory.fundingsOf(shop.owner),
      gateway.isAuthorizedForBalance(definition.usdcAddress, shop.owner, shop.signer),
    ]);
    const funding = fundings.at(-1) ?? null;
    let facts = { funding, fundingAgent: null, allowance: 0n, authorized, maxPerMove: 0n, weeklyCap: 0n };
    if (funding) {
      const contract = new ethers.Contract(funding, FUNDING_ABI, node);
      const [fundingAgent, allowance, maxPerMove, weeklyCap] = await Promise.all([
        contract.agent(), usdc.allowance(shop.owner, funding), contract.maxPerMove(), contract.weeklyCap(),
      ]);
      facts = { funding, fundingAgent, allowance, authorized, maxPerMove, weeklyCap };
    }
    return { chain, name: chain.replaceAll("_", " "), inWallet, funding, status: chainStatus(facts, shop) };
  }));
}

/** The calls that stop the agent on a chain: no more of the owner's USDC there, and no spending of
 * what is already in their Unified Balance from it. The USDC stays the owner's. */
export function turnOffCalls({ funding, usdc, gateway, signer }) {
  const calls = [];
  if (funding) {
    calls.push({ to: usdc, data: new ethers.Interface(USDC_ABI).encodeFunctionData("approve", [funding, 0n]), value: "0x0" });
  }
  calls.push({ to: gateway, data: new ethers.Interface(GATEWAY_WALLET_ABI).encodeFunctionData("removeDelegate", [usdc, signer]), value: "0x0" });
  return calls;
}

const BATCH_POLL_MS = 2000;
const BATCH_WAIT_MS = 180000;

/** The calls that let the agent move funds on a chain, in order: create the owner's ShopFunding
 * for the agent (when it exists, give it this agent and the shop's limits if it has others),
 * approve it, and authorize the agent's signer in Gateway (skipped when it is). The signer is the
 * agent itself for an older shop. */
export function setupCalls({ factory, funding, create, usdc, gateway, agent, signer = agent, maxPerMove, weeklyCap, delegate, setLimits = false, setAgent = false }) {
  const calls = [];
  const fundingCall = (name, args) =>
    calls.push({ to: funding, data: new ethers.Interface(FUNDING_ABI).encodeFunctionData(name, args), value: "0x0" });
  if (create) {
    const data = new ethers.Interface(FUNDING_FACTORY_ABI).encodeFunctionData("create", [agent, maxPerMove, weeklyCap]);
    calls.push({ to: factory, data, value: "0x0" });
  } else {
    if (setAgent) fundingCall("setAgent", [agent]);
    if (setLimits) fundingCall("setLimits", [maxPerMove, weeklyCap]);
  }
  calls.push({ to: usdc, data: new ethers.Interface(USDC_ABI).encodeFunctionData("approve", [funding, AUTHORIZE_ALL]), value: "0x0" });
  if (delegate) {
    calls.push({ to: gateway, data: new ethers.Interface(GATEWAY_WALLET_ABI).encodeFunctionData("addDelegate", [usdc, signer]), value: "0x0" });
  }
  return calls;
}

/** Wait for a batch sent with wallet_sendCalls (EIP-5792): 200 is confirmed, 400 and up failed. */
async function waitForCalls(provider, id) {
  for (let waited = 0; waited < BATCH_WAIT_MS; waited += BATCH_POLL_MS) {
    const status = await provider.request({ method: "wallet_getCallsStatus", params: [id] });
    if (status.status === 200) return status;
    if (status.status >= 400) throw new Error(`The wallet could not complete it (status ${status.status}).`);
    await new Promise((resolve) => setTimeout(resolve, BATCH_POLL_MS));
  }
  throw new Error("Still waiting for the chain; check your wallet's activity, then look again.");
}

/** Send calls on one chain: one confirmation when the wallet batches calls (EIP-5792, MetaMask's
 * smart account through EIP-7702); otherwise one per call. */
async function sendCalls(provider, signer, owner, chainNumber, calls) {
  const chainId = ethers.toQuantity(chainNumber);
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

/** Let the agent move the owner's USDC on this chain into the owner's Unified Balance, within the
 * shop's limits: create the owner's ShopFunding there (or bring its limits to the shop's), approve
 * it, and authorize the agent in Gateway.
 * One confirmation when the wallet batches calls (EIP-5792, MetaMask's smart account through
 * EIP-7702); otherwise one per step. */
export async function setUpChain(provider, chain, { agent, signer: delegateAddress = agent, maxPerMove, weeklyCap }, factories = FUNDING_FACTORIES) {
  const { resolveChainIdentifier } = await kit();
  const definition = resolveChainIdentifier(chain);
  await adapterOn(provider, chain);  // the wallet is now on that chain
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const factory = new ethers.Contract(factories[chain], FUNDING_FACTORY_ABI, browser);
  const existing = (await factory.fundingsOf(owner)).at(-1);
  const funding = existing ?? (await factory.predict(owner, agent, maxPerMove, weeklyCap));
  let setLimits = false;
  let setAgent = false;
  if (existing) {
    const current = new ethers.Contract(existing, FUNDING_ABI, browser);
    const [move, week, itsAgent] = await Promise.all([current.maxPerMove(), current.weeklyCap(), current.agent()]);
    setLimits = move !== maxPerMove || week !== weeklyCap;
    setAgent = itsAgent.toLowerCase() !== agent.toLowerCase();
  }
  // read on chain: right after turning a chain off, Gateway's API can still say the old state
  const gateway = new ethers.Contract(GATEWAY_WALLET_TESTNET, GATEWAY_WALLET_ABI, browser);
  const authorized = await gateway.isAuthorizedForBalance(definition.usdcAddress, owner, delegateAddress);
  const calls = setupCalls({
    factory: factories[chain], funding, create: !existing, usdc: definition.usdcAddress,
    gateway: GATEWAY_WALLET_TESTNET, agent, signer: delegateAddress, maxPerMove, weeklyCap, delegate: !authorized,
    setLimits, setAgent,
  });
  await sendCalls(provider, signer, owner, definition.chainId, calls);
  return funding;
}

/** Stop the agent on this chain (see turnOffCalls), in the owner's wallet. */
export async function turnOff(provider, chain, { signer: delegateAddress }, factories = FUNDING_FACTORIES) {
  const { resolveChainIdentifier } = await kit();
  const definition = resolveChainIdentifier(chain);
  await adapterOn(provider, chain);  // the wallet is now on that chain
  const browser = new ethers.BrowserProvider(provider);
  const signer = await browser.getSigner();
  const owner = await signer.getAddress();
  const factory = new ethers.Contract(factories[chain], FUNDING_FACTORY_ABI, browser);
  const funding = (await factory.fundingsOf(owner)).at(-1) ?? null;
  const calls = turnOffCalls({ funding, usdc: definition.usdcAddress, gateway: GATEWAY_WALLET_TESTNET, signer: delegateAddress });
  await sendCalls(provider, signer, owner, definition.chainId, calls);
}

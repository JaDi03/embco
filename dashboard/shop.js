// Reading the factory, the owner's shop contract and its history from Arc Testnet.
// Reads go to the Canteen node through this site's /rpc proxy; history comes from the explorer's
// index, or from the node if the explorer fails. Writes go through MetaMask in app.js; nothing
// here can sign.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { FACTORY_ABI, SHOP_ABI, USDC_ABI } from "./abi.js";
import {
  ARC_TESTNET,
  EXPLORER_API,
  FACTORY_ADDRESS,
  FACTORY_DEPLOY_BLOCK,
  HISTORY_MAX_PAGES,
  LOG_CHUNK_BLOCKS,
  rpcUrl,
  USDC_ADDRESS,
} from "./config.js";


let reader = null;

/** The read-only provider, created once, with the network fixed. */
function readProvider() {
  if (!reader) {
    reader = new ethers.JsonRpcProvider(rpcUrl(), Number(ARC_TESTNET.chainId), { staticNetwork: true });
  }
  return reader;
}

/** Run a read against the Canteen node. */
export function read(fn) {
  return fn(readProvider());
}

export function factory(runner) {
  return new ethers.Contract(FACTORY_ADDRESS, FACTORY_ABI, runner);
}

export function shopContract(address, runner) {
  return new ethers.Contract(address, SHOP_ABI, runner);
}

export function usdc(runner) {
  return new ethers.Contract(USDC_ADDRESS, USDC_ABI, runner);
}

/** The owner's shop contract addresses, oldest first. */
export function shopsOf(owner) {
  return read((provider) => factory(provider).shopsOf(owner));
}

/** Everything the shop page shows, read in parallel on one node. */
export function shopState(shop, account) {
  return read(async (provider) => {
    const c = shopContract(shop, provider);
    const token = usdc(provider);
    const [owner, agent, maxPerPayment, weeklyCap, paused, remaining, balance, allowance] =
      await Promise.all([
        c.owner(),
        c.agent(),
        c.maxPerPayment(),
        c.weeklyCap(),
        c.paused(),
        c.remainingThisWeek(),
        token.balanceOf(account),
        token.allowance(account, shop),
      ]);
    return { owner, agent, maxPerPayment, weeklyCap, paused, remaining, balance, allowance };
  });
}

/** Raw logs of a contract from the explorer, newest first, a few pages at most. */
async function explorerLogs(address, fetchImpl = fetch) {
  const logs = [];
  let query = "";
  for (let page = 0; page < HISTORY_MAX_PAGES; page++) {
    const response = await fetchImpl(`${EXPLORER_API}/addresses/${address}/logs${query}`);
    if (!response.ok) throw new Error(`The Arc explorer answered ${response.status}. Try again in a moment.`);
    const body = await response.json();
    logs.push(...(body.items ?? []));
    if (!body.next_page_params) break;
    query = `?${new URLSearchParams(body.next_page_params)}`;
  }
  return logs;
}

/** Decode the shop's logs into approved suppliers and payments (newest first). */
export function decodeHistory(logs) {
  const iface = new ethers.Interface(SHOP_ABI);
  const events = [];
  for (const log of logs) {
    const parsed = iface.parseLog({ topics: (log.topics ?? []).filter(Boolean), data: log.data ?? "0x" });
    if (parsed) events.push({ parsed, block: log.block_number, index: log.index, tx: log.transaction_hash });
  }
  events.sort((a, b) => b.block - a.block || b.index - a.index);

  const latestDecision = new Map();
  const payments = [];
  for (const { parsed, tx } of events) {
    if (parsed.name === "PayeeSet") {
      const payee = parsed.args.payee;
      if (!latestDecision.has(payee)) latestDecision.set(payee, parsed.args.approved);
    } else if (parsed.name === "Paid") {
      payments.push({
        invoiceRef: parsed.args.invoiceRef,
        payee: parsed.args.payee,
        amount: parsed.args.amount,
        by: parsed.args.by,
        tx,
      });
    }
  }
  const suppliers = [...latestDecision].filter(([, approved]) => approved).map(([payee]) => payee);
  return { suppliers, payments };
}

/** The shop's logs from the node, in ranges it accepts, from the factory deployment on. */
async function nodeLogs(shop, provider) {
  const latest = await provider.getBlockNumber();
  const logs = [];
  for (let start = FACTORY_DEPLOY_BLOCK; start <= latest; start += LOG_CHUNK_BLOCKS) {
    const end = Math.min(start + LOG_CHUNK_BLOCKS - 1, latest);
    const chunk = await provider.getLogs({ address: shop, fromBlock: start, toBlock: end });
    logs.push(
      ...chunk.map((l) => ({ topics: l.topics, data: l.data, block_number: l.blockNumber, index: l.index, transaction_hash: l.transactionHash })),
    );
  }
  return logs;
}

export async function shopHistory(shop) {
  let logs;
  try {
    logs = await explorerLogs(shop);
  } catch {
    logs = await read((provider) => nodeLogs(shop, provider));
  }
  return decodeHistory(logs);
}

export { explorerLogs, nodeLogs };

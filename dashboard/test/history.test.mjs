import assert from "node:assert/strict";
import { test } from "node:test";

// The same ethers module the page imports.
import { ethers } from "../vendor/ethers-6.17.0.min.js";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const { decodeHistory, explorerLogs, nodeLogs } = await import("../shop.js");
const { rpcUrl } = await import("../config.js");
const { SHOP_ABI } = await import("../abi.js");
const { describeError } = await import("../errors.js");

const iface = new ethers.Interface(SHOP_ABI);
const SUPPLIER_A = "0x3333333333333333333333333333333333333333";
const SUPPLIER_B = "0x4444444444444444444444444444444444444444";
const AGENT = "0x2222222222222222222222222222222222222222";

/** A log shaped like the explorer's: topics padded with null, newest first. */
function log(name, args, block, index = 0) {
  const { topics, data } = iface.encodeEventLog(iface.getEvent(name), args);
  return { topics: [...topics, null, null, null].slice(0, 4), data, block_number: block, index, transaction_hash: `0xtx${block}` };
}

test("the latest decision per supplier wins and payments come newest first", () => {
  const ref1 = ethers.id("ACC-PINV-2026-00013");
  const ref2 = ethers.id("ACC-PINV-2026-00014");
  const logs = [
    log("Paid", [ref2, SUPPLIER_A, 50_000_000n, AGENT], 40),
    log("PayeeSet", [SUPPLIER_B, false], 30),
    log("Paid", [ref1, SUPPLIER_A, 125_500_000n, AGENT], 20),
    log("PayeeSet", [SUPPLIER_B, true], 11),
    log("PayeeSet", [SUPPLIER_A, true], 10),
  ];
  const history = decodeHistory(logs);
  assert.deepEqual(history.suppliers, [SUPPLIER_A]);
  assert.deepEqual(history.payments.map((p) => p.amount), [50_000_000n, 125_500_000n]);
  assert.equal(history.payments[1].invoiceRef, ref1);
  assert.equal(history.payments[1].by, AGENT);
  assert.equal(history.payments[0].tx, "0xtx40");
});

test("logs from other events or contracts are ignored", () => {
  const transfer = {
    topics: [ethers.id("Transfer(address,address,uint256)"), null, null, null],
    data: "0x",
    block_number: 5,
    index: 0,
  };
  assert.deepEqual(decodeHistory([transfer]), { suppliers: [], payments: [] });
});

test("explorer pages are followed until there is no next page", async () => {
  const pages = [
    { items: [log("PayeeSet", [SUPPLIER_A, true], 10)], next_page_params: { block_number: 9, index: 0 } },
    { items: [log("PayeeSet", [SUPPLIER_B, true], 8)], next_page_params: null },
  ];
  const urls = [];
  const fakeFetch = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => pages[urls.length - 1] };
  };
  const logs = await explorerLogs("0xshop", fakeFetch);
  assert.equal(logs.length, 2);
  assert.match(urls[1], /\?block_number=9&index=0$/);
});

test("a failing explorer is reported plainly", async () => {
  const fakeFetch = async () => ({ ok: false, status: 429 });
  await assert.rejects(explorerLogs("0xshop", fakeFetch), /explorer answered 429/);
});

test("chain traffic goes to this site's /rpc proxy, never a node URL", () => {
  assert.equal(rpcUrl(), "https://app.embco.xyz/rpc");
  assert.equal(rpcUrl("http://127.0.0.1:8099"), "http://127.0.0.1:8099/rpc");
});

test("node history is read in ranges under 100,000 blocks", async () => {
  const ranges = [];
  const provider = {
    getBlockNumber: async () => 65877241 + 250000,
    getLogs: async ({ fromBlock, toBlock }) => {
      ranges.push(toBlock - fromBlock + 1);
      return [];
    },
  };
  await nodeLogs("0xshop", provider);
  assert.deepEqual(ranges, [99999, 99999, 50003]);
});

test("an overloaded network reads as a sentence, not 'missing revert data'", () => {
  const busy = { shortMessage: "missing revert data", info: { amount: 5n } };
  assert.match(describeError(busy), /network is busy/);
  assert.match(describeError({ message: "rate limit exceeded" }), /network is busy/);
});

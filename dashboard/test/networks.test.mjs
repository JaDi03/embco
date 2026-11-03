import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.window = { location: { origin: "https://app.embco.xyz" } };
const { MAINNET, TESTNET, networkNamed, networkOfChain, otherNetwork, rpcUrlOf } = await import("../networks.js");
const { nodeLogs } = await import("../shop.js");
const { verifierPage } = await import("../arc-check.js");
const hub = await import("../hub.js");

test("the two networks are Arc's, as its docs and our deploys say", () => {
  assert.equal(TESTNET.chainId, 5042002n);
  assert.equal(MAINNET.chainId, 5042n);
  assert.equal(MAINNET.chainIdHex, "0x13b2");
  assert.equal(MAINNET.factory, "0x1304d82964c44E2a8cca4D5bc27E30C83A123a50");
  assert.equal(MAINNET.testMoney, false);
  assert.equal(TESTNET.testMoney, true);
});

test("a wallet's chain tells the network; any other chain is not Arc", () => {
  assert.equal(networkOfChain(5042n), MAINNET);
  assert.equal(networkOfChain("0x4cef52"), TESTNET);
  assert.equal(networkOfChain(1), null);
  assert.equal(otherNetwork(TESTNET), MAINNET);
});

test("a network is found by its name or the label a supplier page shows", () => {
  assert.equal(networkNamed("mainnet"), MAINNET);
  assert.equal(networkNamed("Arc mainnet"), MAINNET);
  assert.equal(networkNamed("Arc testnet"), TESTNET);
  assert.equal(networkNamed(undefined), TESTNET);
});

test("each network has its own door on this site; no node URL reaches the page", () => {
  assert.equal(rpcUrlOf(TESTNET), "https://app.embco.xyz/rpc");
  assert.equal(rpcUrlOf(MAINNET), "https://app.embco.xyz/rpc/mainnet");
});

test("on mainnet the node is asked for at most 10,000 blocks, from the mainnet factory on", async () => {
  const ranges = [];
  const provider = {
    getBlockNumber: async () => MAINNET.deployBlock + 25000,
    getLogs: async ({ fromBlock, toBlock }) => {
      ranges.push([fromBlock, toBlock - fromBlock + 1]);
      return [];
    },
  };
  await nodeLogs("0xshop", provider, MAINNET);
  assert.equal(ranges[0][0], MAINNET.deployBlock);
  assert.deepEqual(ranges.map(([, size]) => size), [10000, 10000, 5001]);
});

test("Arc's verifier is the one of the shop's network", () => {
  assert.match(verifierPage(MAINNET), /^https:\/\/explorer\.arc\.io\/address\/0x69e31Af302Ee222204AA78099D39a2956F5C95d6/);
  assert.match(verifierPage(), /explorer\.testnet\.arc\.io\/address\/0x2e23aFE44fe10378F9957030bf3B0d31998dddde/);
});

test("a new shop's agent wallets are made on the network chosen", async () => {
  const bodies = [];
  const impl = async (path, init) => {
    bodies.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ agent: "0xa", signer: "0xb", network: "mainnet" }) };
  };
  await hub.newAgentWallets({}, impl, "mainnet");
  await hub.newAgentWallets({}, impl);
  assert.deepEqual(bodies, [{ network: "mainnet" }, { network: "testnet" }]);
});

test("there is no draft mode on mainnet: real USDC settles the invoice", () => {
  assert.equal(hub.draftModeView({ connected: true, draft_mode: false, network: "mainnet" }), null);
  assert.equal(hub.draftModeView({ connected: true, draft_mode: true, network: "testnet" }).draft, true);
});

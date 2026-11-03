// The two Arc networks a shop can run on (services/network.py on the server). Everything here is
// public. The owner picks one with the network button; the page then reads and writes there.

import {
  ARC_TESTNET,
  DECISION_VERIFIER_ADDRESS,
  EXPLORER_API,
  FACTORY_ADDRESS,
  FACTORY_DEPLOY_BLOCK,
  JOURNAL_ANCHOR_ADDRESS,
  LOG_CHUNK_BLOCKS,
  RPC_PATH,
} from "./config.js";

export const TESTNET = {
  ...ARC_TESTNET,
  name: "testnet",
  rpcPath: RPC_PATH,
  factory: FACTORY_ADDRESS,
  deployBlock: FACTORY_DEPLOY_BLOCK,
  logChunk: LOG_CHUNK_BLOCKS,
  explorerApi: EXPLORER_API,
  journalAnchor: JOURNAL_ANCHOR_ADDRESS,
  decisionVerifier: DECISION_VERIFIER_ADDRESS,
  testMoney: true,  // test USDC: drafts in the ERP, Unified Balance funding, Earn, the faucet
};

// Arc docs, "Connect to Arc"; contracts deployed with contracts/script/Deploy*.s.sol.
export const MAINNET = {
  chainId: 5042n,
  chainIdHex: "0x13b2",
  chainName: "Arc Mainnet",
  explorer: "https://explorer.arc.io",
  nativeCurrency: ARC_TESTNET.nativeCurrency,
  name: "mainnet",
  rpcPath: "/rpc/mainnet",
  factory: "0x1304d82964c44E2a8cca4D5bc27E30C83A123a50",
  deployBlock: 29369377,
  logChunk: 10000,  // the most blocks the mainnet node returns events for in one call
  explorerApi: null,  // the mainnet explorer refuses scripts: history comes from the node
  journalAnchor: "0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8",
  decisionVerifier: "0x69e31Af302Ee222204AA78099D39a2956F5C95d6",
  testMoney: false,
};

export const NETWORKS = [TESTNET, MAINNET];

/** The network of a wallet's chain id (bigint, number or hex), or null when it is not Arc. */
export function networkOfChain(chainId) {
  const id = BigInt(chainId);
  return NETWORKS.find((n) => n.chainId === id) ?? null;
}

/** The network by its name ("testnet", "mainnet") or label ("Arc mainnet"); testnet if unknown. */
export function networkNamed(name) {
  const key = String(name ?? "").toLowerCase().replace(/^arc /, "");
  return NETWORKS.find((n) => n.name === key) ?? TESTNET;
}

/** The other network, for the switch button. */
export function otherNetwork(network) {
  return network === MAINNET ? TESTNET : MAINNET;
}

/** This site's door to the network's node: the node's access token stays on the server. */
export function rpcUrlOf(network, origin = window.location.origin) {
  return new URL(network.rpcPath, origin).href;
}

let active = TESTNET;

/** The network the page reads and writes on now. */
export function activeNetwork() {
  return active;
}

export function useNetwork(network) {
  active = network;
}

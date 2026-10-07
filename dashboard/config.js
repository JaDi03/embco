// Network and contract addresses. Everything here is public.

export const ARC_TESTNET = {
  chainId: 5042002n,
  chainIdHex: "0x4cef52",
  chainName: "Arc Testnet",
  explorer: "https://explorer.testnet.arc.io",
  // Arc's gas token is USDC: 18 decimals on the native interface.
  nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 },
};

// USDC's ERC-20 interface on Arc (6 decimals, same balance as the native one).
export const USDC_ADDRESS = "0x3600000000000000000000000000000000000000";

export const FACTORY_ADDRESS = "0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8";

export const FACTORY_DEPLOY_BLOCK = 65877241;

// All chain traffic goes through the Canteen node via this site's /rpc proxy, which keeps the
// node's token on the server. Never put a node URL with a token in this public page.
export const RPC_PATH = "/rpc";

// The explorer has every event indexed: one request instead of scanning the chain in chunks.
export const EXPLORER_API = "https://explorer.testnet.arc.io/api/v2";
export const HISTORY_MAX_PAGES = 10;

// If the explorer fails, history is read from the node, in ranges it accepts (under 100,000 blocks).
export const LOG_CHUNK_BLOCKS = 99999;

export function rpcUrl(origin = window.location.origin) {
  return new URL(RPC_PATH, origin).href;
}

export const FAUCET_URL = "https://faucet.circle.com";

// Wallets the agent is waiting to pay, written by the agent on this site.
export const APPROVALS_PATH = "/pending.json";

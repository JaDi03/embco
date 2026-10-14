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

export const FACTORY_ADDRESS = "0x5D455dF984C85abe160E4585E0c1139ded5d2c3b";

export const FACTORY_DEPLOY_BLOCK = 66943142;

// All chain traffic goes through the Canteen node via this site's /rpc proxy, which keeps the
// node's token on the server. Never put a node URL with a token in this public page.
export const RPC_PATH = "/rpc";

// The explorer has every event indexed: one request instead of scanning the chain in chunks.
export const EXPLORER_API = "https://explorer.testnet.arc.io/api/v2";
export const HISTORY_MAX_PAGES = 10;

// The node answers 413 to a batch of more than 10 calls; ethers batches up to 100 by default.
export const RPC_BATCH_MAX = 10;

// If the explorer fails, history is read from the node, in ranges it accepts (under 100,000 blocks).
export const LOG_CHUNK_BLOCKS = 99999;

// Arc's predeployed Memo contract: the agent pays through it, so each payment carries the
// invoice name on chain (docs.arc.io, transaction memos).
export const MEMO_ADDRESS = "0x5294E9927c3306DcBaDb03fe70b92e01cCede505";

export function rpcUrl(origin = window.location.origin) {
  return new URL(RPC_PATH, origin).href;
}

export const FAUCET_URL = "https://faucet.circle.com";

// ShopFundingFactory on each source chain of the owner's Unified Balance (contracts/script/
// DeployFunding.s.sol). A chain without one is not offered for the agent to move funds from.
export const FUNDING_FACTORIES = { Base_Sepolia: "0x69e31Af302Ee222204AA78099D39a2956F5C95d6", Ethereum_Sepolia: "0x69e31Af302Ee222204AA78099D39a2956F5C95d6" };

// Circle Gateway's GatewayWallet, the same address on every EVM testnet (Circle's Gateway docs).
export const GATEWAY_WALLET_TESTNET = "0x0077777d7EBA4688BDeF3E311b846F25870A19B9";

// Wallets the agent is waiting to pay, written by the agent on this site.
export const APPROVALS_PATH = "/pending.json";

// The hub API on this site: sign in with the owner's wallet, connect the ERP, read the agent.
export const HUB_PATH = "/api";

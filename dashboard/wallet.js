// The wallet connection, the EIP-6963 way: installed wallets announce themselves (mipd store), the
// person picks one, and the page remembers it to reconnect on the next visit, as wagmi does. Every
// call then goes to that wallet's EIP-1193 provider.

import { createStore } from "./vendor/mipd-0.0.7/index.js";
import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { ARC_TESTNET, rpcUrl } from "./config.js";

const REMEMBER = "embco-wallet";  // the rdns of the wallet last connected
const store = createStore();
let chosen = null;  // the EIP-6963 provider detail in use
let watcher = null;

/** The wallets in this browser. A wallet without EIP-6963 is still found on window.ethereum. */
export function wallets() {
  const announced = store.getProviders();
  if (announced.length || typeof window.ethereum === "undefined") return announced;
  return [{ info: { uuid: "window.ethereum", name: "Browser wallet", rdns: "window.ethereum", icon: "" },
    provider: window.ethereum }];
}

/** Called again when a wallet announces itself after the page started. */
export function onWallets(listener) {
  return store.subscribe(listener);
}

function remembered() {
  try {
    return localStorage.getItem(REMEMBER);
  } catch {
    return null;  // no storage (private window): connect again on each visit
  }
}

function remember(rdns) {
  try {
    if (rdns) localStorage.setItem(REMEMBER, rdns);
    else localStorage.removeItem(REMEMBER);
  } catch {
    // the choice lasts for this visit only
  }
}

/** The wallet this page connected to before, if it is still installed. */
export function rememberedWallet() {
  const rdns = remembered();
  return rdns ? wallets().find((w) => w.info.rdns === rdns) ?? null : null;
}

function use(detail) {
  if (chosen?.provider !== detail?.provider && watcher) {
    chosen?.provider.removeListener?.("accountsChanged", watcher);
    chosen?.provider.removeListener?.("chainChanged", watcher);
    detail?.provider.on?.("accountsChanged", watcher);
    detail?.provider.on?.("chainChanged", watcher);
  }
  chosen = detail;
}

/** The connected account without asking the person, or null. */
export async function reconnect() {
  const detail = chosen ?? rememberedWallet();
  if (!detail) return null;
  const [account] = await detail.provider.request({ method: "eth_accounts" });
  if (!account) {
    use(null);
    remember(null);
    return null;
  }
  use(detail);
  return ethers.getAddress(account);
}

/** Ask the chosen wallet to connect this page. */
export async function connect(detail) {
  const [account] = await detail.provider.request({ method: "eth_requestAccounts" });
  use(detail);
  remember(detail.info.rdns);
  return ethers.getAddress(account);
}

/** Forget the wallet here, and ask it to forget this page. Wallets that cannot still stay forgotten here. */
export async function disconnect() {
  const detail = chosen;
  use(null);
  remember(null);
  await detail?.provider.request({ method: "wallet_revokePermissions", params: [{ eth_accounts: {} }] })
    .catch(() => null);
}

export function provider() {
  if (!chosen) throw new Error("Connect your wallet first.");
  return new ethers.BrowserProvider(chosen.provider);
}

export async function onArc() {
  const network = await provider().getNetwork();
  return network.chainId === ARC_TESTNET.chainId;
}

/** Switch the wallet to Arc Testnet, adding the network first if the wallet does not know it. */
export async function switchToArc() {
  if (!chosen) throw new Error("Connect your wallet first.");
  try {
    await chosen.provider.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: ARC_TESTNET.chainIdHex }],
    });
  } catch (error) {
    const unknownChain = error.code === 4902 || error?.data?.originalError?.code === 4902;
    if (!unknownChain) throw error;
    await chosen.provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: ARC_TESTNET.chainIdHex,
          chainName: ARC_TESTNET.chainName,
          nativeCurrency: ARC_TESTNET.nativeCurrency,
          rpcUrls: [rpcUrl()],
          blockExplorerUrls: [ARC_TESTNET.explorer],
        },
      ],
    });
  }
}

/** Called when the person changes account or network in the connected wallet. */
export function onWalletChange(callback) {
  watcher = callback;
  chosen?.provider.on?.("accountsChanged", callback);
  chosen?.provider.on?.("chainChanged", callback);
}

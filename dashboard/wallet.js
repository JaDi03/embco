// MetaMask connection and the Arc Testnet network. Uses the injected EIP-1193 provider only.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { ARC_TESTNET, rpcUrl } from "./config.js";


export function hasWallet() {
  return typeof window.ethereum !== "undefined";
}

export async function connect() {
  const provider = new ethers.BrowserProvider(window.ethereum);
  await provider.send("eth_requestAccounts", []);
  return provider;
}

/** The connected account without asking the user, or null. */
export async function currentAccount() {
  if (!hasWallet()) return null;
  const accounts = await window.ethereum.request({ method: "eth_accounts" });
  return accounts[0] ? ethers.getAddress(accounts[0]) : null;
}

export async function onArc(provider) {
  const network = await provider.getNetwork();
  return network.chainId === ARC_TESTNET.chainId;
}

/** Switch MetaMask to Arc Testnet, adding the network first if MetaMask does not know it. */
export async function switchToArc() {
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: ARC_TESTNET.chainIdHex }],
    });
  } catch (error) {
    const unknownChain = error.code === 4902 || error?.data?.originalError?.code === 4902;
    if (!unknownChain) throw error;
    await window.ethereum.request({
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

/** Re-render when the user changes account or network in MetaMask. */
export function onWalletChange(callback) {
  if (!hasWallet()) return;
  window.ethereum.on("accountsChanged", callback);
  window.ethereum.on("chainChanged", callback);
}

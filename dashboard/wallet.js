// MetaMask connection and the Arc Testnet network, through the wallet's EIP-1193 provider.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { ARC_TESTNET, rpcUrl } from "./config.js";

/**
 * MetaMask when it announced itself, else the first wallet that did, else window.ethereum.
 * Another wallet (Rabby, for one) can take window.ethereum and pretend to be MetaMask.
 */
export function pickWallet(announced, injected) {
  const metamask = announced.find((w) => w.info?.rdns === "io.metamask");
  return (metamask ?? announced[0])?.provider ?? injected ?? null;
}

/** The installed wallets announce themselves when asked (EIP-6963). */
async function findWallet() {
  const announced = [];
  const listen = (event) => announced.push(event.detail);
  window.addEventListener("eip6963:announceProvider", listen);
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  await new Promise((resolve) => setTimeout(resolve, 200));
  window.removeEventListener("eip6963:announceProvider", listen);
  return pickWallet(announced, window.ethereum);
}

let found = null;
const eip1193 = () => (found ??= findWallet());

export async function hasWallet() {
  return (await eip1193()) !== null;
}

export async function browserProvider() {
  return new ethers.BrowserProvider(await eip1193());
}

export async function connect() {
  const provider = await browserProvider();
  await provider.send("eth_requestAccounts", []);
  return provider;
}

/** The connected account without asking the user, or null. */
export async function currentAccount() {
  const wallet = await eip1193();
  if (!wallet) return null;
  const accounts = await wallet.request({ method: "eth_accounts" });
  return accounts[0] ? ethers.getAddress(accounts[0]) : null;
}

/** Remove this site from MetaMask's connected sites. False when the wallet cannot do it. */
export async function disconnect() {
  const wallet = await eip1193();
  if (!wallet) return true;
  try {
    await wallet.request({ method: "wallet_revokePermissions", params: [{ eth_accounts: {} }] });
    return true;
  } catch {
    return false;
  }
}

export async function onArc(provider) {
  const network = await provider.getNetwork();
  return network.chainId === ARC_TESTNET.chainId;
}

/** Switch MetaMask to Arc Testnet, adding the network first if MetaMask does not know it. */
export async function switchToArc() {
  const wallet = await eip1193();
  try {
    await wallet.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: ARC_TESTNET.chainIdHex }],
    });
  } catch (error) {
    const unknownChain = error.code === 4902 || error?.data?.originalError?.code === 4902;
    if (!unknownChain) throw error;
    await wallet.request({
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
export async function onWalletChange(callback) {
  const wallet = await eip1193();
  if (!wallet) return;
  wallet.on("accountsChanged", callback);
  wallet.on("chainChanged", callback);
}

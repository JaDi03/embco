// Shared test setup: a local anvil node running the compiled EmbcoCampaigns and a mock
// USDC. Needs Foundry and `forge build` in contracts/; CI installs both. Locally the
// tests skip with a reason when either is missing; in CI that is an error.

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  createPublicClient,
  createTestClient,
  createWalletClient,
  parseEther,
  type Abi,
  type Address,
  type Hex,
  type HttpTransport,
  type PrivateKeyAccount,
  type PublicClient,
} from "viem";
import { foundry } from "viem/chains";
import { rpcTransport } from "../chain.ts";

const OUT = fileURLToPath(new URL("../../../../../contracts/out/", import.meta.url));
const artifact = (name: string) => {
  const path = join(OUT, `${name}.sol`, `${name}.json`);
  if (!existsSync(path)) return null;
  const json = JSON.parse(readFileSync(path, "utf8")) as { abi: Abi; bytecode: { object: Hex } };
  return { abi: json.abi, bytecode: json.bytecode.object };
};
export const CAMPAIGNS = artifact("EmbcoCampaigns");
export const USDC = artifact("MockUSDC");

function findAnvil(): string | null {
  const bin = join(homedir(), ".foundry", "bin");
  for (const candidate of [process.env.ANVIL, "anvil", join(bin, "anvil.exe"), join(bin, "anvil")]) {
    if (candidate && spawnSync(candidate, ["--version"]).status === 0) return candidate;
  }
  return null;
}
const ANVIL = findAnvil();

/** Why the anvil tests can't run here, or null when they can. */
export const missing = !ANVIL ? "anvil not found" : !CAMPAIGNS || !USDC ? "contracts not built (cd contracts && forge build)" : null;
if (missing && process.env.CI) throw new Error(`anvil tests can't run in CI: ${missing}`);

export interface Node {
  client: PublicClient;
  transport: HttpTransport;
  contract: Address;
  usdc: Address;
  /** Sends a tx from `account` with the full compiled ABI and returns the function's result. */
  call(account: PrivateKeyAccount, address: Address, abi: Abi, functionName: string, args: unknown[]): Promise<unknown>;
  stop(): void;
}

/** Starts anvil, funds `accounts` with gas and deploys both contracts from the first one. */
export async function startNode(accounts: PrivateKeyAccount[]): Promise<Node> {
  const port = 20_000 + Math.floor(Math.random() * 20_000);
  const child = spawn(ANVIL!, ["--port", String(port)], { stdio: "ignore" });
  // Through the scrubbing transport, so the tests also prove reverts still decode with it.
  const transport = rpcTransport(`http://127.0.0.1:${port}`);
  const client = createPublicClient({ chain: foundry, transport });
  for (let i = 0; ; i++) {
    try {
      await client.getChainId();
      break;
    } catch (err) {
      if (i > 100) throw err;
      await new Promise((r) => setTimeout(r, 100));
    }
  }
  const testClient = createTestClient({ chain: foundry, mode: "anvil", transport });
  for (const a of accounts) await testClient.setBalance({ address: a.address, value: parseEther("10") });

  const deployer = createWalletClient({ chain: foundry, transport, account: accounts[0] });
  const deploy = async (a: NonNullable<typeof USDC>, args: unknown[]) => {
    const hash = await deployer.deployContract({ abi: a.abi, bytecode: a.bytecode, args });
    return (await client.waitForTransactionReceipt({ hash })).contractAddress!;
  };
  const usdc = await deploy(USDC!, []);
  const contract = await deploy(CAMPAIGNS!, [usdc]);

  const call: Node["call"] = async (account, address, abi, functionName, args) => {
    const wallet = createWalletClient({ chain: foundry, transport, account });
    const { request, result } = await client.simulateContract({ account, address, abi, functionName, args });
    await client.waitForTransactionReceipt({ hash: await wallet.writeContract(request) });
    return result;
  };
  return { client, transport, contract, usdc, call, stop: () => child.kill() };
}

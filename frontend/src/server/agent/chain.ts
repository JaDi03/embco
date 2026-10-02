// The agent's link to EmbcoCampaigns. Before a review it reads one consistent
// snapshot of the campaign (every read at the same block, whose timestamp is "now"
// for the floor). After a review it sends the outcome to the contract. The contract
// is the last word: when it refuses, the refusal is journaled and the agent backs
// off (waits, or hands the submission to the agency); it never retries around it.

import {
  BaseError,
  ContractFunctionRevertedError,
  createPublicClient,
  createWalletClient,
  http,
  isAddress,
  zeroAddress,
  type Account,
  type Address,
  type Chain,
  type Hex,
  type PublicClient,
  type Transport,
  type WalletClient,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { arcTestnet } from "viem/chains";
import { CAMPAIGNS_ABI, SUBMISSION_STATUS, type SubmissionStatus } from "./contract.ts";
import type { CampaignState } from "./floor.ts";
import { createEntry, type HashSigner, type JournalEntry } from "./journal.ts";
import type { ReviewResult } from "./review.ts";

// --- reading ---------------------------------------------------------------

export interface CampaignSnapshot {
  campaignId: bigint;
  owner: Address;
  agent: Address;
  paused: boolean;
  state: CampaignState; // limits and spend as seen for one worker
  blockNumber: bigint;
  now: bigint; // timestamp of that block
}

/** Campaign limits and spend for one worker, all read at the same block. */
export async function readCampaign(
  client: PublicClient,
  contract: Address,
  campaignId: bigint,
  worker: Address,
): Promise<CampaignSnapshot> {
  const block = await client.getBlock();
  const at = { address: contract, abi: CAMPAIGNS_ABI, blockNumber: block.number } as const;
  const [c, w] = await Promise.all([
    client.readContract({ ...at, functionName: "getCampaign", args: [campaignId] }),
    client.readContract({ ...at, functionName: "getWorkerSpend", args: [campaignId, worker] }),
  ]);
  if (c.owner === zeroAddress) throw new Error(`campaign ${campaignId} does not exist`);
  return {
    campaignId,
    owner: c.owner,
    agent: c.agent,
    paused: c.paused,
    blockNumber: block.number,
    now: block.timestamp,
    state: {
      reward: c.reward,
      balance: c.balance,
      capPerPeriod: c.capPerPeriod,
      workerCapPerPeriod: c.workerCapPerPeriod,
      periodLength: c.periodLength,
      periodStart: c.periodStart,
      spentInPeriod: c.spentInPeriod,
      workerPeriodStart: w.periodStart,
      workerSpentInPeriod: w.spent,
    },
  };
}

export interface OnchainSubmission {
  submissionId: bigint;
  campaignId: bigint;
  worker: Address;
  taskId: number;
  status: SubmissionStatus;
  contentHash: Hex;
  decisionHash: Hex;
}

export async function readSubmission(client: PublicClient, contract: Address, submissionId: bigint): Promise<OnchainSubmission> {
  const s = await client.readContract({ address: contract, abi: CAMPAIGNS_ABI, functionName: "getSubmission", args: [submissionId] });
  return { submissionId, ...s, status: SUBMISSION_STATUS[s.status] ?? "none" };
}

// --- sending ---------------------------------------------------------------

export interface AgentCall {
  functionName: "pay" | "escalate" | "reject";
  args: readonly [submissionId: bigint, decisionHash: Hex];
}

/** Sends one agent call and returns its tx hash. A local key for now; a Circle DCW later. */
export interface TxSender {
  address: Address;
  send(call: AgentCall): Promise<Hex>;
}

export function localSender(wallet: WalletClient<Transport, Chain, Account>, contract: Address): TxSender {
  return {
    address: wallet.account.address,
    send: (call) => wallet.writeContract({ address: contract, abi: CAMPAIGNS_ABI, functionName: call.functionName, args: call.args }),
  };
}

/** The revert reason if `err` is the contract refusing; null for anything else (network, node, bug). */
function revertReason(err: unknown): string | null {
  if (!(err instanceof BaseError)) return null;
  const revert = err.walk((e) => e instanceof ContractFunctionRevertedError);
  if (!(revert instanceof ContractFunctionRevertedError)) return null;
  return revert.data?.errorName ?? revert.reason ?? "reverted";
}

type SendResult = { ok: true; txHash: Hex } | { ok: false; error: string; txHash: Hex | null };

async function send(input: SettleInput, call: AgentCall): Promise<SendResult> {
  const dryRun = (blockNumber?: bigint) =>
    input.client.simulateContract({ address: input.contract, abi: CAMPAIGNS_ABI, account: input.sender.address, ...call, blockNumber });
  const refusal = async (run: () => Promise<unknown>) => {
    try {
      await run();
      return null;
    } catch (err) {
      const reason = revertReason(err);
      if (reason === null) throw err; // not a refusal: let the caller retry later
      return reason;
    }
  };

  // Dry run first: a refusal then costs no gas and its reason is readable.
  const before = await refusal(() => dryRun());
  if (before) return { ok: false, error: before, txHash: null };

  let txHash: Hex | undefined;
  const atSend = await refusal(async () => {
    txHash = await input.sender.send(call);
  });
  if (atSend || !txHash) return { ok: false, error: atSend ?? "not sent", txHash: null };

  const receipt = await input.client.waitForTransactionReceipt({ hash: txHash });
  if (receipt.status === "success") return { ok: true, txHash };
  // State changed between the dry run and the block: replay it there to read why.
  const inBlock = await refusal(() => dryRun(receipt.blockNumber));
  return { ok: false, error: inBlock ?? "reverted", txHash };
}

// --- settling a review -----------------------------------------------------

export interface SettleInput {
  client: PublicClient;
  contract: Address;
  sender: TxSender;
  signer: HashSigner;
  review: ReviewResult; // its entry is not recorded yet
  /** Persists one journal entry. Called before any transaction that carries the entry's hash. */
  record(entry: JournalEntry): Promise<void>;
}

export interface Settlement {
  landed: AgentCall["functionName"] | null; // what the contract accepted, if anything
  txHash: Hex | null;
  entries: JournalEntry[]; // everything recorded, in order; the review's entry first
}

// What the agent does when the contract refuses a payment, by revert reason.
function afterRefusedPay(error: string): "wait" | "escalate" | "stop" {
  if (error === "InsufficientBudget" || error === "IsPaused") return "wait"; // new funds or the agency's resume fix it
  if (error === "WrongStatus" || error === "NotAgent") return "stop"; // already settled, or this agent was replaced
  return "escalate";
}

/**
 * Records the review's entry, then sends its outcome. Each campaign's journal is one
 * chain, so a campaign's submissions must be settled one at a time.
 */
export async function settle(input: SettleInput): Promise<Settlement> {
  const { review, signer } = input;
  const entries = [review.entry];
  await input.record(review.entry);

  const action = review.outcome.action;
  if (action === "wait") return { landed: null, txHash: null, entries };

  const campaignId = review.entry.campaignId;
  // The id comes from the signed entry, so the hash sent onchain always describes this submission.
  const { submissionId } = review.entry.data as { submissionId: string };
  const id = BigInt(submissionId);

  const append = async (last: JournalEntry, kind: "refusal" | "escalation", data: unknown) => {
    const entry = await createEntry(signer, campaignId, last, kind, data);
    entries.push(entry);
    await input.record(entry);
    return entry;
  };

  const first = await send(input, { functionName: action, args: [id, review.entry.hash] });
  if (first.ok) return { landed: action, txHash: first.txHash, entries };

  const next = action === "pay" ? afterRefusedPay(first.error) : "stop";
  const refusal = await append(review.entry, "refusal", {
    submissionId,
    call: action,
    decisionHash: review.entry.hash,
    error: first.error,
    txHash: first.txHash,
    next,
  });
  if (next !== "escalate") return { landed: null, txHash: null, entries };

  const escalation = await append(refusal, "escalation", {
    submissionId,
    reason: `the contract refused the payment: ${first.error}`,
    after: refusal.hash,
  });
  const second = await send(input, { functionName: "escalate", args: [id, escalation.hash] });
  if (second.ok) return { landed: "escalate", txHash: second.txHash, entries };

  await append(escalation, "refusal", {
    submissionId,
    call: "escalate",
    decisionHash: escalation.hash,
    error: second.error,
    txHash: second.txHash,
    next: "stop",
  });
  return { landed: null, txHash: null, entries };
}

// --- configuration ---------------------------------------------------------

/**
 * Arc Testnet clients plus the agent's key, from env. The key both signs journal
 * entries and sends transactions: one identity. It is a local key until the
 * Circle DCW signer lands. Errors never echo the values (the RPC URL carries a token).
 */
export function chainFromEnv(env: Record<string, string | undefined>) {
  const { RPC, CAMPAIGNS_ADDRESS, AGENT_PRIVATE_KEY } = env;
  if (!RPC || !CAMPAIGNS_ADDRESS || !AGENT_PRIVATE_KEY) {
    throw new Error("RPC, CAMPAIGNS_ADDRESS and AGENT_PRIVATE_KEY must be set");
  }
  if (!isAddress(CAMPAIGNS_ADDRESS)) throw new Error("CAMPAIGNS_ADDRESS is not an address");
  if (!/^0x[0-9a-fA-F]{64}$/.test(AGENT_PRIVATE_KEY)) throw new Error("AGENT_PRIVATE_KEY must be 0x followed by 64 hex characters");

  const account = privateKeyToAccount(AGENT_PRIVATE_KEY as Hex);
  const transport = http(RPC);
  const client = createPublicClient({ chain: arcTestnet, transport });
  const wallet = createWalletClient({ chain: arcTestnet, transport, account });
  const signer: HashSigner = { address: account.address, signHash: (hash) => account.signMessage({ message: { raw: hash } }) };
  return { client, contract: CAMPAIGNS_ADDRESS as Address, sender: localSender(wallet, CAMPAIGNS_ADDRESS), signer };
}

// Runs the agent against the real compiled contract on a local anvil node
// (see testing/anvil.ts for what that needs).

import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import {
  createPublicClient,
  createWalletClient,
  http,
  zeroAddress,
  type Abi,
  type AbiParameter,
  type Address,
  type Hex,
  type PublicClient,
} from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { foundry } from "viem/chains";
import { chainFromEnv, localSender, readCampaign, readSubmission, settle, type TxSender } from "./chain.ts";
import { CAMPAIGNS_ABI } from "./contract.ts";
import { verifyJournal, type HashSigner, type JournalEntry } from "./journal.ts";
import { reviewSubmission, type Agent } from "./review.ts";
import { CAMPAIGNS, missing, startNode, USDC, type Node } from "./testing/anvil.ts";

const skip = missing ?? false;

// --- the ABI the agent uses matches the compiled contract ----------------------

const typeOf = (p: AbiParameter): string =>
  "components" in p && p.components ? `(${p.components.map(typeOf).join(",")})${p.type.slice("tuple".length)}` : p.type;
const signature = (item: { name: string; inputs: readonly AbiParameter[] }) => `${item.name}(${item.inputs.map(typeOf).join(",")})`;

test("the hand-written ABI matches the compiled contract", { skip: CAMPAIGNS ? false : "contracts not built" }, () => {
  const compiled = CAMPAIGNS!.abi;
  for (const item of CAMPAIGNS_ABI) {
    if (item.type !== "function" && item.type !== "error") continue;
    const match = compiled.find((c) => c.type === item.type && c.name === item.name);
    assert.ok(match && match.type === item.type, `${item.type} ${item.name} is not in the contract`);
    assert.equal(signature(match), signature(item));
    if (item.type === "function" && match.type === "function") {
      assert.deepEqual(match.outputs.map(typeOf), item.outputs.map(typeOf), `outputs of ${item.name}`);
      assert.equal(match.stateMutability, item.stateMutability, `mutability of ${item.name}`);
    }
  }
});

// --- configuration ------------------------------------------------------------

test("the chain config comes from env and errors never echo the values", () => {
  const key = generatePrivateKey();
  assert.throws(() => chainFromEnv({}), /RPC, CAMPAIGNS_ADDRESS and AGENT_PRIVATE_KEY must be set/);
  const base = { RPC: "https://rpc.example/secret-token", CAMPAIGNS_ADDRESS: "0x73aF5bF164eB25E0c740Ed33cA561d5d6f90F41e" };
  assert.throws(
    () => chainFromEnv({ ...base, AGENT_PRIVATE_KEY: "0x1234" }),
    (err: Error) => /AGENT_PRIVATE_KEY must be/.test(err.message) && !err.message.includes("0x1234"),
  );
  assert.throws(() => chainFromEnv({ ...base, CAMPAIGNS_ADDRESS: "0xnope", AGENT_PRIVATE_KEY: key }), /not an address/);
  const chain = chainFromEnv({ ...base, AGENT_PRIVATE_KEY: key });
  assert.equal(chain.signer.address, privateKeyToAccount(key).address);
  assert.equal(chain.sender.address, chain.signer.address, "one identity signs the journal and the transactions");
});

/** Every piece of text an error carries, through all its causes: what a log could print. */
function allText(err: unknown): string {
  const parts: string[] = [String(err)];
  for (let e = err as Record<string, unknown> | undefined; e && typeof e === "object"; e = e.cause as typeof e) {
    for (const value of Object.values(e)) if (typeof value === "string") parts.push(value);
    if (Array.isArray(e.metaMessages)) parts.push(...e.metaMessages.map(String));
    parts.push(String(e.stack));
  }
  return parts.join("\n");
}

test("network errors never carry the RPC URL or its token", async () => {
  // Nothing listens on port 9: every request fails like an unreachable RPC.
  const url = "http://127.0.0.1:9/v1/secret-token-123";
  const plain = createPublicClient({ chain: foundry, transport: http(url, { retryCount: 0 }) });
  const leaked = await plain.getBlockNumber().then(() => "did not fail", allText);
  assert.match(leaked, /secret-token-123/, "viem alone puts the URL in its errors");

  const chain = chainFromEnv({ RPC: url, CAMPAIGNS_ADDRESS: zeroAddress, AGENT_PRIVATE_KEY: generatePrivateKey() });
  const scrubbed = await readCampaign(chain.client, chain.contract, 0n, zeroAddress).then(() => "did not fail", allText);
  assert.doesNotMatch(scrubbed, /secret-token-123/);
  assert.match(scrubbed, /<rpc>/);
});

// --- against the real contract on anvil -------------------------------------

let node: Node | undefined;
let client: PublicClient;
let contract: Address;
let usdc: Address;
const owner = privateKeyToAccount(generatePrivateKey());
const agent = privateKeyToAccount(generatePrivateKey());
const worker = privateKeyToAccount(generatePrivateKey());
const signer: HashSigner = { address: agent.address, signHash: (hash) => agent.signMessage({ message: { raw: hash } }) };
let sender: TxSender;

const REWARD = 50_000n; // 0.05 USDC

before(async () => {
  if (missing) return;
  node = await startNode([owner, agent, worker]);
  ({ client, contract, usdc } = node);
  sender = localSender(createWalletClient({ chain: foundry, transport: node.transport, account: agent }), contract);
});

after(() => node?.stop());

const call = (account: typeof owner, address: Address, abi: Abi, functionName: string, args: unknown[]) =>
  node!.call(account, address, abi, functionName, args);
const asOwner = (fn: string, args: unknown[]) => call(owner, contract, CAMPAIGNS!.abi, fn, args);

/** A funded campaign with one submission from the worker. */
async function setup() {
  const deposit = 1_000_000n;
  await call(owner, usdc, USDC!.abi, "mint", [owner.address, deposit]);
  await call(owner, usdc, USDC!.abi, "approve", [contract, deposit]);
  const params = {
    agent: agent.address,
    reward: REWARD,
    taskCount: 10,
    maxSubmissionsPerTask: 3,
    capPerPeriod: 500_000n,
    workerCapPerPeriod: 200_000n,
    periodLength: 86_400n,
  };
  const campaignId = (await asOwner("createCampaign", [params, deposit])) as bigint;
  const contentHash: Hex = `0x${"ab".repeat(32)}`;
  const submissionId = (await call(worker, contract, CAMPAIGNS!.abi, "submit", [campaignId, 0, contentHash])) as bigint;
  return { campaignId, submissionId, contentHash };
}

const says =
  (action: string): Agent =>
  async () => ({ action, reasons: ["the totals match the photo"] });

/** Reads the snapshot, reviews with `brain`, and lets `meanwhile` change the chain before settling. */
async function run(brain: Agent, meanwhile?: (ids: Awaited<ReturnType<typeof setup>>) => Promise<unknown>) {
  const ids = await setup();
  const snapshot = await readCampaign(client, contract, ids.campaignId, worker.address);
  const review = await reviewSubmission({
    agent: brain,
    signer,
    lastEntry: null,
    task: { kind: "receipt", instructions: "Transcribe every line and the total.", images: [] },
    submission: {
      campaignId: ids.campaignId.toString(),
      submissionId: ids.submissionId.toString(),
      worker: worker.address,
      taskId: 0,
      contentHash: ids.contentHash,
      answer: { lines: [{ description: "Coffee", amount: "3.50" }], total: "3.50" },
    },
    facts: { formatErrors: [], copyOf: null },
    otherAnswers: [],
    campaign: snapshot.state,
    now: snapshot.now,
  });
  await meanwhile?.(ids);
  const recorded: JournalEntry[] = [];
  const result = await settle({ client, contract, sender, signer, review, record: async (e) => void recorded.push(e) });
  assert.deepEqual(recorded, result.entries, "every entry is recorded, in order");
  assert.deepEqual(await verifyJournal(recorded, agent.address), { ok: true });
  return { ...ids, snapshot, review, result, onchain: await readSubmission(client, contract, ids.submissionId) };
}

const balanceOf = (a: Address) =>
  client.readContract({ address: usdc, abi: USDC!.abi, functionName: "balanceOf", args: [a] }) as Promise<bigint>;

test("reads one snapshot, pays the worker, and the payment points to the journal entry", { skip }, async () => {
  const before = await balanceOf(worker.address);
  const { snapshot, review, result, onchain } = await run(says("pay"));
  assert.equal(snapshot.agent, agent.address);
  assert.equal(snapshot.state.reward, REWARD);
  assert.equal(snapshot.state.balance, 1_000_000n);
  assert.equal(result.landed, "pay");
  assert.ok(result.txHash);
  assert.equal(await balanceOf(worker.address), before + REWARD);
  assert.equal(onchain.status, "paid");
  assert.equal(onchain.decisionHash, review.entry.hash);
});

test("when the contract refuses a payment, the agent escalates and the journal says why", { skip }, async () => {
  const before = await balanceOf(worker.address);
  // The agency lowers the cap after the snapshot: the floor passed, the contract won't.
  const { result, onchain } = await run(says("pay"), ({ campaignId }) => asOwner("setLimits", [campaignId, 0n, 0n]));
  assert.equal(result.landed, "escalate");
  assert.deepEqual(
    result.entries.map((e) => e.kind),
    ["payment", "refusal", "escalation"],
  );
  assert.equal((result.entries[1].data as { error: string }).error, "OverPeriodCap");
  assert.equal(onchain.status, "escalated");
  assert.equal(onchain.decisionHash, result.entries[2].hash);
  assert.equal(await balanceOf(worker.address), before, "no money moved");
});

test("a paused campaign makes a refused payment wait", { skip }, async () => {
  const { result, onchain } = await run(says("pay"), ({ campaignId }) => asOwner("pause", [campaignId]));
  assert.equal(result.landed, null);
  assert.deepEqual(
    result.entries.map((e) => e.kind),
    ["payment", "refusal"],
  );
  assert.deepEqual((result.entries[1].data as { error: string; next: string }).next, "wait");
  assert.equal(onchain.status, "submitted", "left open for when the agency resumes");
});

test("a submission the agency already settled is left alone", { skip }, async () => {
  const zero: Hex = `0x${"0".repeat(64)}`;
  const { result, onchain } = await run(says("pay"), ({ submissionId }) => asOwner("reject", [submissionId, zero]));
  assert.equal(result.landed, null);
  assert.deepEqual(result.entries[1].data, {
    submissionId: (result.entries[0].data as { submissionId: string }).submissionId,
    call: "pay",
    decisionHash: result.entries[0].hash,
    error: "WrongStatus",
    txHash: null,
    next: "stop",
  });
  assert.equal(onchain.status, "rejected");
});

test("a rejection lands onchain with its entry hash; a wait sends nothing", { skip }, async () => {
  const rejected = await run(says("reject"));
  assert.equal(rejected.result.landed, "reject");
  assert.equal(rejected.onchain.status, "rejected");
  assert.equal(rejected.onchain.decisionHash, rejected.review.entry.hash);

  const waited = await run(says("wait"));
  assert.equal(waited.result.landed, null);
  assert.equal(waited.result.txHash, null);
  assert.deepEqual(
    waited.result.entries.map((e) => e.kind),
    ["decision"],
  );
  assert.equal(waited.onchain.status, "submitted");
});

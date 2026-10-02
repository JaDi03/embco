// The agent's pass end to end, against the real contract on anvil (see testing/anvil.ts).

import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { createWalletClient, type Address, type PrivateKeyAccount, type PublicClient } from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { foundry } from "viem/chains";
import { localSender, type TxSender } from "./chain.ts";
import { verifyJournal, type HashSigner } from "./journal.ts";
import { processPending, type PendingInput } from "./pending.ts";
import type { Agent, AgentInput } from "./review.ts";
import { answerHash, MemoryStore } from "./store.ts";
import { CAMPAIGNS, missing, startNode, USDC, type Node } from "./testing/anvil.ts";

const skip = missing ?? false;
const account = () => privateKeyToAccount(generatePrivateKey());
const owner = account();
const agentKey = account();
const otherAgent = account();
const workers = [account(), account(), account()];
const squatter = account();
const signer: HashSigner = { address: agentKey.address, signHash: (hash) => agentKey.signMessage({ message: { raw: hash } }) };

let node: Node | undefined;
let client: PublicClient;
let contract: Address;
let sender: TxSender;

before(async () => {
  if (missing) return;
  node = await startNode([owner, agentKey, otherAgent, squatter, ...workers]);
  ({ client, contract } = node);
  sender = localSender(createWalletClient({ chain: foundry, transport: node.transport, account: agentKey }), contract);
});
after(() => node?.stop());

const REWARD = 50_000n;
const RECEIPT = { lines: [{ description: "Coffee", amount: "3.50" }], total: "3.50" };

const asOwner = (fn: string, args: unknown[]) => node!.call(owner, contract, CAMPAIGNS!.abi, fn, args);
const read = (fn: string, args: unknown[]) => client.readContract({ address: contract, abi: CAMPAIGNS!.abi, functionName: fn, args });

/** A store that starts after every submission made so far, so earlier tests don't leak in. */
async function freshStore() {
  const store = new MemoryStore();
  await store.setCursor((await read("submissionCount", [])) as bigint);
  return store;
}

async function newCampaign(store: MemoryStore, opts: { kind?: string; slots?: number; agent?: Address } = {}) {
  const deposit = 1_000_000n;
  await node!.call(owner, node!.usdc, USDC!.abi, "mint", [owner.address, deposit]);
  await node!.call(owner, node!.usdc, USDC!.abi, "approve", [contract, deposit]);
  const params = {
    agent: opts.agent ?? agentKey.address,
    reward: REWARD,
    taskCount: 10,
    maxSubmissionsPerTask: opts.slots ?? 3,
    capPerPeriod: 500_000n,
    workerCapPerPeriod: 200_000n,
    periodLength: 86_400n,
  };
  const id = (await asOwner("createCampaign", [params, deposit])) as bigint;
  store.putTask(id.toString(), 0, { kind: opts.kind ?? "receipt", instructions: "Transcribe every line and the total.", images: [] });
  return id;
}

/** Registers `committed` onchain and uploads `uploaded` (the same answer unless told otherwise). */
async function submit(store: MemoryStore, who: PrivateKeyAccount, campaignId: bigint, committed: unknown, uploaded: unknown = committed) {
  const id = (await node!.call(who, contract, CAMPAIGNS!.abi, "submit", [campaignId, 0, answerHash(committed)])) as bigint;
  if (uploaded !== undefined) store.putAnswer(id.toString(), uploaded);
  return id.toString();
}

/** A fake model that always answers `action` and remembers what it was shown. */
function model(action: string) {
  const seen: AgentInput[] = [];
  const agent: Agent = async (input) => {
    seen.push(input);
    return { action, reasons: ["looked at the photo"] };
  };
  return { agent, seen };
}

const pass = (store: MemoryStore, agent: Agent, opts: Partial<PendingInput> = {}) =>
  processPending({ client, contract, sender, signer, agent, store, ...opts });

const balanceOf = (a: Address) =>
  client.readContract({ address: node!.usdc, abi: USDC!.abi, functionName: "balanceOf", args: [a] }) as Promise<bigint>;

test("a pass finds a new submission, checks its answer against the hash and pays", { skip }, async () => {
  const store = await freshStore();
  const campaignId = await newCampaign(store);
  const id = await submit(store, workers[0], campaignId, RECEIPT);
  const before = await balanceOf(workers[0].address);

  const report = await pass(store, model("pay").agent);
  assert.deepEqual(report.discovered, [id]);
  assert.deepEqual(report.settled, [{ submissionId: id, outcome: "pay", landed: "pay" }]);
  assert.deepEqual(report.errors, []);
  assert.equal(await balanceOf(workers[0].address), before + REWARD);
  assert.equal(store.get(id)?.status, "paid");
  assert.deepEqual(await verifyJournal(store.journals.get(campaignId.toString())!, agentKey.address), { ok: true });
});

test("a registered submission without its real answer is never reviewed, then rejected so its slot frees up", { skip }, async () => {
  const store = await freshStore();
  const campaignId = await newCampaign(store, { slots: 1 });
  // Commits to one answer, uploads another: the upload doesn't count.
  const id = await submit(store, squatter, campaignId, RECEIPT, { ...RECEIPT, total: "9.99" });
  const { agent, seen } = model("pay");

  const early = await pass(store, agent);
  assert.deepEqual(early.skipped, [{ submissionId: id, reason: "waiting for the answer upload" }]);
  assert.equal(await read("openSlotsUsed", [campaignId, 0]), 1n, "the squatter holds the only slot");

  const late = await pass(store, agent, { answerGraceSeconds: 0n });
  assert.deepEqual(late.settled, [{ submissionId: id, outcome: "reject", landed: "reject" }]);
  assert.equal(seen.length, 0, "the model never saw it");
  const [entry] = store.journals.get(campaignId.toString())!;
  assert.match((entry.data as { agent: { reasons: string[] } }).agent.reasons[0], /^rule: no answer matching the registered hash/);
  assert.equal(await read("openSlotsUsed", [campaignId, 0]), 0n, "slot freed");
  await submit(store, workers[0], campaignId, RECEIPT); // an honest worker gets in
});

test("an agent that waits is asked again only when new evidence arrives", { skip }, async () => {
  const store = await freshStore();
  const campaignId = await newCampaign(store);
  const first = await submit(store, workers[0], campaignId, RECEIPT);
  const { agent, seen } = model("wait");

  await pass(store, agent);
  assert.equal(seen.length, 1);
  const quiet = await pass(store, agent);
  assert.equal(seen.length, 1, "no new evidence, no new model call");
  assert.deepEqual(quiet.skipped, [{ submissionId: first, reason: "nothing new since the last review" }]);

  const second = await submit(store, workers[1], campaignId, RECEIPT);
  await pass(store, agent);
  assert.equal(seen.length, 3, "both submissions reviewed again");
  const firstAgain = seen.find((s) => s.submission.submissionId === first && s.otherAnswers.length > 0);
  assert.deepEqual(
    firstAgain?.otherAnswers.map((o) => o.submissionId),
    [second],
  );
  assert.equal(store.get(first)?.status, "submitted");
});

test("a submission waiting past the limit goes to the agency without asking the model", { skip }, async () => {
  const store = await freshStore();
  const campaignId = await newCampaign(store);
  const id = await submit(store, workers[0], campaignId, RECEIPT);
  const { agent, seen } = model("wait");

  await pass(store, agent, { maxWaitSeconds: 0n });
  const report = await pass(store, agent, { maxWaitSeconds: 0n });
  assert.equal(seen.length, 1);
  assert.deepEqual(report.settled, [{ submissionId: id, outcome: "escalate", landed: "escalate" }]);
  assert.equal(store.get(id)?.status, "escalated");
  const entries = store.journals.get(campaignId.toString())!;
  assert.match((entries.at(-1)!.data as { agent: { reasons: string[] } }).agent.reasons[0], /^rule: waited more than 0 seconds/);
});

test("a copied text answer is caught, and only the later one counts as the copy", { skip }, async () => {
  const store = await freshStore();
  const campaignId = await newCampaign(store, { kind: "text" });
  const original = await submit(store, workers[0], campaignId, "The quick brown fox");
  const copy = await submit(store, workers[1], campaignId, "the quick  brown FOX");

  const report = await pass(store, model("pay").agent);
  assert.deepEqual(report.settled, [
    { submissionId: original, outcome: "pay", landed: "pay" },
    { submissionId: copy, outcome: "escalate", landed: "escalate" },
  ]);
});

test("other agents' campaigns are ignored and paused campaigns wait", { skip }, async () => {
  const store = await freshStore();
  const foreign = await newCampaign(store, { agent: otherAgent.address });
  await submit(store, workers[0], foreign, RECEIPT);
  const paused = await newCampaign(store);
  const id = await submit(store, workers[0], paused, RECEIPT);
  await asOwner("pause", [paused]);
  const { agent, seen } = model("pay");

  const report = await pass(store, agent);
  assert.deepEqual(report.discovered, [id]);
  assert.deepEqual(report.skipped, [{ submissionId: id, reason: "campaign paused" }]);
  assert.equal(seen.length, 0);
});

// --- the pass lock ------------------------------------------------------------

const offline = { client: undefined as never, contract: undefined as never, sender: undefined as never, signer, agent: model("pay").agent };

test("while another pass holds the lock, a pass does nothing", async () => {
  const store = new MemoryStore();
  const held = await store.lock(60);
  const report = await processPending({ ...offline, store });
  assert.equal(report.busy, true);
  assert.equal(await store.cursor(), 0n, "didn't even look for submissions");
  await held!.release();
  assert.ok(await store.lock(60), "free again once released");
});

test("only the holder can release the lock, and a lapsed lock can be taken over", async () => {
  const store = new MemoryStore();
  const lapsed = await store.lock(0);
  const current = await store.lock(60);
  assert.ok(current, "a lapsed lock is free");
  await lapsed!.release();
  assert.equal(await store.lock(60), null, "the old holder can't release the new holder's lock");
  await current!.release();
  assert.ok(await store.lock(60));
});

test("the lock is released even when the pass fails", async () => {
  const store = new MemoryStore();
  const down = async () => {
    throw new Error("rpc down");
  };
  const client = { readContract: down, getBlock: down } as never;
  await assert.rejects(processPending({ ...offline, client, store }), /rpc down/);
  assert.ok(await store.lock(60));
});

test("a pass whose lock is running out leaves the rest for the next one", { skip }, async () => {
  const store = await freshStore();
  const id = await submit(store, workers[0], await newCampaign(store), RECEIPT);
  const { agent, seen } = model("pay");

  const short = await pass(store, agent, { lockSeconds: 0 });
  assert.deepEqual(short.unfinished, [id]);
  assert.equal(seen.length, 0);
  const next = await pass(store, agent);
  assert.deepEqual(next.settled, [{ submissionId: id, outcome: "pay", landed: "pay" }]);
});

test("one failing submission doesn't stop the pass", { skip }, async () => {
  const store = await freshStore();
  const broken = await submit(store, workers[0], await newCampaign(store), RECEIPT);
  const fine = await submit(store, workers[1], await newCampaign(store), RECEIPT);
  const answer = store.answer.bind(store);
  store.answer = async (id) => {
    if (id === broken) throw new Error("storage timeout");
    return answer(id);
  };

  const report = await pass(store, model("pay").agent);
  assert.deepEqual(report.errors, [{ submissionId: broken, error: "storage timeout" }]);
  assert.deepEqual(report.settled, [{ submissionId: fine, outcome: "pay", landed: "pay" }]);
});

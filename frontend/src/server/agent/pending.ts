// One pass of the agent: find new submissions onchain, then review and settle every
// open one it can. Runtime-agnostic: a loop on a server or a cron endpoint just calls
// processPending. Submissions of one campaign are handled one at a time, because each
// campaign's journal is a single chain, and a pass holds the store's lock so two
// passes never overlap.

import { type Address, type PublicClient } from "viem";
import { readCampaign, readSubmission, settle, type TxSender } from "./chain.ts";
import { CAMPAIGNS_ABI } from "./contract.ts";
import type { AgentAction, SubmissionFacts } from "./floor.ts";
import { findCopy, parseReceipt } from "./formats.ts";
import { canonical, type HashSigner } from "./journal.ts";
import { reviewSubmission, type Agent, type AgentInput, type Task } from "./review.ts";
import { answerHash, type AgentStore, type TrackedSubmission } from "./store.ts";

export interface PendingInput {
  client: PublicClient;
  contract: Address;
  sender: TxSender;
  signer: HashSigner;
  agent: Agent;
  store: AgentStore;
  /** How long a worker has to upload the answer matching the hash they registered. */
  answerGraceSeconds?: bigint;
  /** How long a submission may stay open waiting for more evidence before the agency decides. */
  maxWaitSeconds?: bigint;
  /** Most onchain submissions read per pass when catching up. */
  maxScan?: bigint;
  /** How long a pass may hold the lock. It stops taking new submissions in the last fifth. */
  lockSeconds?: number;
}

export interface PassReport {
  discovered: string[];
  settled: { submissionId: string; outcome: AgentAction; landed: string | null }[];
  skipped: { submissionId: string; reason: string }[];
  errors: { submissionId: string; error: string }[];
  busy: boolean; // another pass held the lock, so this one did nothing
  unfinished: string[]; // open submissions left for the next pass because the lock was running out
}

const HOUR = 3_600n;

/** Decisions made by a fixed rule instead of the model; the journal shows them as such. */
const rule =
  (action: "reject" | "escalate", reason: string): Agent =>
  async () => ({ action, reasons: [`rule: ${reason}`] });

/** Reads new submission ids and tracks the ones for campaigns this agent serves. */
async function discover(input: PendingInput, report: PassReport): Promise<void> {
  const { client, contract, store } = input;
  const [count, block] = await Promise.all([
    client.readContract({ address: contract, abi: CAMPAIGNS_ABI, functionName: "submissionCount" }),
    client.getBlock(),
  ]);
  const from = await store.cursor();
  const to = count < from + (input.maxScan ?? 500n) ? count : from + (input.maxScan ?? 500n);
  const agents = new Map<bigint, Address>();

  for (let id = from; id < to; id++) {
    const s = await readSubmission(client, contract, id);
    if (!agents.has(s.campaignId)) {
      const c = await client.readContract({ address: contract, abi: CAMPAIGNS_ABI, functionName: "getCampaign", args: [s.campaignId] });
      agents.set(s.campaignId, c.agent);
    }
    if (agents.get(s.campaignId) !== input.sender.address || s.status !== "submitted") continue;
    await store.track({
      submissionId: id.toString(),
      campaignId: s.campaignId.toString(),
      worker: s.worker,
      taskId: s.taskId,
      contentHash: s.contentHash,
      firstSeen: block.timestamp,
      status: s.status,
      lastEvidence: null,
      waitingSince: null,
    });
    report.discovered.push(id.toString());
  }
  await store.setCursor(to);
}

/** The uploaded answer, only if it is the one the worker committed to onchain. */
async function committedAnswer(store: AgentStore, s: TrackedSubmission): Promise<{ answer: unknown } | null> {
  const answer = await store.answer(s.submissionId);
  if (answer === undefined) return null;
  return answerHash(answer) === s.contentHash ? { answer } : null;
}

function factsFor(task: Task, s: TrackedSubmission, answer: unknown, others: AgentInput["otherAnswers"]): SubmissionFacts {
  // Receipts should agree with each other: a match is consensus, not a copy.
  if (task.kind === "receipt") return { formatErrors: parseReceipt(answer).errors, copyOf: null };
  if (typeof answer !== "string") return { formatErrors: ["answer is not text"], copyOf: null };
  // Only an earlier answer can have been copied.
  const earlier = others
    .filter((o) => BigInt(o.submissionId) < BigInt(s.submissionId) && typeof o.answer === "string")
    .map((o) => ({ submissionId: o.submissionId, text: o.answer as string }));
  return { formatErrors: [], copyOf: findCopy(answer, earlier) };
}

async function processOne(input: PendingInput, s: TrackedSubmission, report: PassReport): Promise<void> {
  const { client, contract, store } = input;
  const skip = (reason: string) => void report.skipped.push({ submissionId: s.submissionId, reason });

  const onchain = await readSubmission(client, contract, BigInt(s.submissionId));
  if (onchain.status !== "submitted") {
    await store.update(s.submissionId, { status: onchain.status });
    return skip(`already ${onchain.status}`);
  }
  const snap = await readCampaign(client, contract, BigInt(s.campaignId), s.worker);
  if (snap.agent !== input.sender.address) return skip("this agent no longer serves the campaign");
  if (snap.paused) return skip("campaign paused");

  const grace = input.answerGraceSeconds ?? HOUR;
  const maxWait = input.maxWaitSeconds ?? 24n * HOUR;
  const committed = await committedAnswer(store, s);
  const task = await store.task(s.campaignId, s.taskId);
  let agent = input.agent;
  let evidence: string | null = null;
  let facts: SubmissionFacts = { formatErrors: [], copyOf: null };
  const otherAnswers: AgentInput["otherAnswers"] = [];

  if (!committed) {
    // A registered submission without its answer holds a task slot; free it after the grace period.
    if (snap.now - s.firstSeen < grace) return skip("waiting for the answer upload");
    agent = rule("reject", `no answer matching the registered hash was uploaded within ${grace} seconds`);
  } else {
    if (!task) return skip("task not found in the store");
    for (const o of await store.forTask(s.campaignId, s.taskId)) {
      if (o.submissionId === s.submissionId || o.status === "rejected") continue;
      const other = await committedAnswer(store, o);
      if (other) otherAnswers.push({ submissionId: o.submissionId, worker: o.worker, answer: other.answer });
    }
    facts = factsFor(task, s, committed.answer, otherAnswers);
    evidence = canonical({ others: otherAnswers.map((o) => o.submissionId).sort(), funded: snap.state.balance >= snap.state.reward });
    if (s.waitingSince !== null && snap.now - s.waitingSince >= maxWait) {
      agent = rule("escalate", `waited more than ${maxWait} seconds for more evidence`);
    } else if (evidence === s.lastEvidence) {
      return skip("nothing new since the last review");
    }
  }

  const review = await reviewSubmission({
    agent,
    signer: input.signer,
    lastEntry: await store.lastEntry(s.campaignId),
    task: task ?? { kind: "unknown", instructions: "", images: [] },
    submission: {
      campaignId: s.campaignId,
      submissionId: s.submissionId,
      worker: s.worker,
      taskId: s.taskId,
      contentHash: s.contentHash,
      answer: committed?.answer ?? null,
    },
    facts,
    otherAnswers,
    campaign: snap.state,
    now: snap.now,
  });
  const result = await settle({ client, contract, sender: input.sender, signer: input.signer, review, record: (e) => store.append(e) });
  report.settled.push({ submissionId: s.submissionId, outcome: review.outcome.action, landed: result.landed });

  const landedStatus = { pay: "paid", escalate: "escalated", reject: "rejected" } as const;
  if (result.landed) await store.update(s.submissionId, { status: landedStatus[result.landed] });
  else await store.update(s.submissionId, { lastEvidence: evidence, waitingSince: s.waitingSince ?? snap.now });
}

export async function processPending(input: PendingInput): Promise<PassReport> {
  const report: PassReport = { discovered: [], settled: [], skipped: [], errors: [], busy: false, unfinished: [] };
  const seconds = input.lockSeconds ?? 600;
  const lock = await input.store.lock(seconds);
  if (!lock) return { ...report, busy: true };
  // Stop taking work well before the lock lapses, so the next pass never overlaps this one.
  const stopAt = lock.expiresAt - seconds * 200;
  try {
    await discover(input, report);
    for (const s of await input.store.open()) {
      if (Date.now() >= stopAt) {
        report.unfinished.push(s.submissionId);
        continue;
      }
      try {
        await processOne(input, s, report);
      } catch (err) {
        // RPC errors are already scrubbed by rpcTransport; the next pass retries.
        report.errors.push({ submissionId: s.submissionId, error: err instanceof Error ? err.message : String(err) });
      }
    }
    return report;
  } finally {
    await lock.release();
  }
}

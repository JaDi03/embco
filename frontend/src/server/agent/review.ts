// One review of one submission: the agent decides, the floor checks the decision,
// and the signed journal records all three steps. The entry hash is what goes
// onchain as `decisionHash` when the outcome is a payment, escalation or rejection.

import type { Address, Hex } from "viem";
import { applyFloor, type AgentAction, type AgentDecision, type CampaignState, type FloorResult, type SubmissionFacts } from "./floor.ts";
import { scanAnswer } from "./guard.ts";
import { createEntry, type EntryKind, type HashSigner, type JournalEntry } from "./journal.ts";

export interface Submission {
  campaignId: string;
  submissionId: string;
  worker: Address;
  taskId: number;
  contentHash: Hex;
  answer: unknown;
}

export interface TaskImage {
  mediaType: "image/jpeg" | "image/png" | "image/webp" | "image/gif";
  data: string; // base64, no data: prefix
}

/** The task as the worker saw it: what was asked and the source images (e.g. a receipt photo). */
export interface Task {
  kind: string; // e.g. "receipt"
  instructions: string;
  images: TaskImage[];
}

/** What the agent sees when it decides. */
export interface AgentInput {
  task: Task;
  submission: Submission;
  facts: SubmissionFacts;
  otherAnswers: { submissionId: string; worker: Address; answer: unknown }[];
  withheld: number; // other answers hidden from the agent because they looked like manipulation
}

/** The agent's brain: a fake in tests, a real model in production. Its output is untrusted input. */
export type Agent = (input: AgentInput) => Promise<unknown>;

export interface ReviewInput {
  agent: Agent;
  signer: HashSigner;
  lastEntry: JournalEntry | null; // last journal entry of this campaign
  task: Task;
  submission: Submission;
  facts: SubmissionFacts;
  otherAnswers: AgentInput["otherAnswers"];
  campaign: CampaignState;
  now: bigint; // unix seconds, same clock the contract uses
}

export interface ReviewResult {
  decision: AgentDecision; // what the agent said (or the fallback when it failed)
  outcome: FloorResult; // what actually happens
  entry: JournalEntry;
}

const ACTIONS: readonly AgentAction[] = ["pay", "wait", "reject", "escalate"];

const KIND: Record<AgentAction, EntryKind> = {
  pay: "payment",
  wait: "decision",
  reject: "rejection",
  escalate: "escalation",
};

/** Anything that isn't a well-formed decision becomes an escalation; it can never become a payment. */
function parseDecision(raw: unknown): AgentDecision {
  const value = raw as { action?: unknown; reasons?: unknown };
  const reasons = Array.isArray(value?.reasons) ? value.reasons.filter((r): r is string => typeof r === "string") : [];
  if (!ACTIONS.includes(value?.action as AgentAction) || reasons.length === 0) {
    return { action: "escalate", reasons: ["agent returned a malformed decision"] };
  }
  return { action: value.action as AgentAction, reasons: reasons.slice(0, 10).map((r) => r.slice(0, 500)) };
}

export async function reviewSubmission(input: ReviewInput): Promise<ReviewResult> {
  const { submission, campaign } = input;

  // Screening runs here, on every review, so no caller can skip it.
  const injection = [...new Set([...(input.facts.injection ?? []), ...scanAnswer(submission.answer)])];
  const facts: SubmissionFacts = { ...input.facts, injection };
  const otherAnswers = input.otherAnswers.filter((o) => scanAnswer(o.answer).length === 0);
  const withheld = input.otherAnswers.length - otherAnswers.length;

  let decision: AgentDecision;
  try {
    decision = parseDecision(await input.agent({ task: input.task, submission, facts, otherAnswers, withheld }));
  } catch (err) {
    decision = { action: "escalate", reasons: [`agent failed: ${err instanceof Error ? err.message : String(err)}`] };
  }

  const outcome = applyFloor(decision, campaign, facts, input.now);
  const entry = await createEntry(input.signer, submission.campaignId, input.lastEntry, KIND[outcome.action], {
    submissionId: submission.submissionId,
    worker: submission.worker,
    taskId: submission.taskId,
    contentHash: submission.contentHash,
    reward: campaign.reward,
    agent: decision,
    screen: { injection, withheldOtherAnswers: withheld },
    floor: outcome.violations,
    outcome: outcome.action,
  });
  return { decision, outcome, entry };
}

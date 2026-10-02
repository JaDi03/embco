// One review of one submission: the agent decides, the floor checks the decision,
// and the signed journal records all three steps. The entry hash is what goes
// onchain as `decisionHash` when the outcome is a payment, escalation or rejection.

import type { Address, Hex } from "viem";
import { applyFloor, type AgentAction, type AgentDecision, type CampaignState, type FloorResult, type SubmissionFacts } from "./floor.ts";
import { createEntry, type EntryKind, type HashSigner, type JournalEntry } from "./journal.ts";

export interface Submission {
  campaignId: string;
  submissionId: string;
  worker: Address;
  taskId: number;
  contentHash: Hex;
  answer: unknown;
}

/** What the agent sees when it decides. */
export interface AgentInput {
  submission: Submission;
  facts: SubmissionFacts;
  otherAnswers: { submissionId: string; worker: Address; answer: unknown }[];
}

/** The agent's brain: a fake in tests, Claude in production. Its output is untrusted input. */
export type Agent = (input: AgentInput) => Promise<unknown>;

export interface ReviewInput {
  agent: Agent;
  signer: HashSigner;
  lastEntry: JournalEntry | null; // last journal entry of this campaign
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
  return { action: value.action as AgentAction, reasons };
}

export async function reviewSubmission(input: ReviewInput): Promise<ReviewResult> {
  const { submission, facts, campaign } = input;

  let decision: AgentDecision;
  try {
    decision = parseDecision(await input.agent({ submission, facts, otherAnswers: input.otherAnswers }));
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
    floor: outcome.violations,
    outcome: outcome.action,
  });
  return { decision, outcome, entry };
}

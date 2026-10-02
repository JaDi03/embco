// The code floor under the agent's decision. The agent decides; this only checks
// that the decision breaks no rule. It never upgrades a decision and never judges
// quality: blocking, waiting, rejecting and escalating always pass, and a "pay"
// goes through only when every rule holds.

export type AgentAction = "pay" | "wait" | "reject" | "escalate";

export interface AgentDecision {
  action: AgentAction;
  reasons: string[]; // what the agent weighed; required for every decision
}

/** Campaign state as the contract sees it (USDC base units, unix seconds). */
export interface CampaignState {
  reward: bigint;
  balance: bigint;
  capPerPeriod: bigint;
  workerCapPerPeriod: bigint;
  periodLength: bigint;
  periodStart: bigint;
  spentInPeriod: bigint;
  workerPeriodStart: bigint;
  workerSpentInPeriod: bigint;
}

export interface SubmissionFacts {
  formatErrors: string[]; // from formats.ts
  copyOf: string | null; // submission id this answer copies, if any
  injection?: string[]; // from guard.ts; review.ts always fills it
}

export type Rule = "reasons" | "format" | "copy" | "injection" | "budget" | "period-cap" | "worker-cap";

export interface Violation {
  rule: Rule;
  detail: string;
}

export interface FloorResult {
  action: AgentAction; // what actually happens
  violations: Violation[];
}

/** Spend counted in the current period, rolled over exactly like EmbcoCampaigns._rollPeriods. */
function currentSpend(spent: bigint, start: bigint, length: bigint, now: bigint): bigint {
  return now >= start + length ? 0n : spent;
}

export function applyFloor(
  decision: AgentDecision,
  campaign: CampaignState,
  facts: SubmissionFacts,
  now: bigint,
): FloorResult {
  if (decision.reasons.every((r) => !r.trim())) {
    return { action: "escalate", violations: [{ rule: "reasons", detail: "the agent gave no reasons" }] };
  }
  if (decision.action !== "pay") return { action: decision.action, violations: [] };

  const violations: Violation[] = [];
  for (const e of facts.formatErrors) violations.push({ rule: "format", detail: e });
  if (facts.copyOf !== null) {
    violations.push({ rule: "copy", detail: `answer is identical to submission ${facts.copyOf}` });
  }
  for (const f of facts.injection ?? []) violations.push({ rule: "injection", detail: `answer looks like a manipulation attempt: ${f}` });

  const { reward } = campaign;
  if (campaign.balance < reward) {
    violations.push({ rule: "budget", detail: `campaign balance ${campaign.balance} is below the reward ${reward}` });
  }
  const spent = currentSpend(campaign.spentInPeriod, campaign.periodStart, campaign.periodLength, now);
  if (spent + reward > campaign.capPerPeriod) {
    violations.push({ rule: "period-cap", detail: `paying would reach ${spent + reward} of the ${campaign.capPerPeriod} period cap` });
  }
  const workerSpent = currentSpend(campaign.workerSpentInPeriod, campaign.workerPeriodStart, campaign.periodLength, now);
  if (workerSpent + reward > campaign.workerCapPerPeriod) {
    violations.push({
      rule: "worker-cap",
      detail: `paying would reach ${workerSpent + reward} of the ${campaign.workerCapPerPeriod} per-worker cap`,
    });
  }

  if (violations.length === 0) return { action: "pay", violations };
  // Only an empty budget is fixed by waiting for funds; anything else needs the agency.
  const onlyBudget = violations.every((v) => v.rule === "budget");
  return { action: onlyBudget ? "wait" : "escalate", violations };
}

import { test } from "node:test";
import assert from "node:assert/strict";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import type { CampaignState } from "./floor.ts";
import { verifyJournal, type HashSigner } from "./journal.ts";
import { reviewSubmission, type Agent, type ReviewInput } from "./review.ts";

const NOW = 1_000_000n;

function localSigner(): HashSigner {
  const account = privateKeyToAccount(generatePrivateKey());
  return { address: account.address, signHash: (hash) => account.signMessage({ message: { raw: hash } }) };
}

const campaign: CampaignState = {
  reward: 50_000n,
  balance: 100_000_000n,
  capPerPeriod: 1_000_000n,
  workerCapPerPeriod: 100_000n,
  periodLength: 86_400n,
  periodStart: NOW - 10n,
  spentInPeriod: 0n,
  workerPeriodStart: NOW - 10n,
  workerSpentInPeriod: 0n,
};

function input(agent: Agent, signer: HashSigner, over: Partial<ReviewInput> = {}): ReviewInput {
  return {
    agent,
    signer,
    lastEntry: null,
    task: { kind: "receipt", instructions: "Transcribe every line and the total.", images: [] },
    submission: {
      campaignId: "1",
      submissionId: "7",
      worker: "0x00000000000000000000000000000000000000a1",
      taskId: 3,
      contentHash: `0x${"ab".repeat(32)}`,
      answer: { lines: [{ description: "Coffee", amount: "3.50" }], total: "3.50" },
    },
    facts: { formatErrors: [], copyOf: null },
    otherAnswers: [],
    campaign,
    now: NOW,
    ...over,
  };
}

const says =
  (decision: unknown): Agent =>
  async () =>
    decision;

test("a confident agent pays, and the journal records it signed", async () => {
  const signer = localSigner();
  const r = await reviewSubmission(input(says({ action: "pay", reasons: ["sharp image, total matches"] }), signer));
  assert.equal(r.outcome.action, "pay");
  assert.equal(r.entry.kind, "payment");
  const data = r.entry.data as { agent: { action: string }; outcome: string; reward: string };
  assert.equal(data.agent.action, "pay");
  assert.equal(data.outcome, "pay");
  assert.equal(data.reward, "50000");
  assert.deepEqual(await verifyJournal([r.entry], signer.address), { ok: true });
});

test("when the floor stops a pay, the journal keeps both the agent's call and the block", async () => {
  const signer = localSigner();
  const r = await reviewSubmission(
    input(says({ action: "pay", reasons: ["looks right"] }), signer, {
      campaign: { ...campaign, workerSpentInPeriod: 100_000n },
    }),
  );
  assert.equal(r.outcome.action, "escalate");
  assert.equal(r.entry.kind, "escalation");
  const data = r.entry.data as { agent: { action: string }; floor: { rule: string }[] };
  assert.equal(data.agent.action, "pay");
  assert.equal(data.floor[0].rule, "worker-cap");
});

test("the agent sees the facts and other answers before deciding", async () => {
  let seen: unknown;
  const agent: Agent = async (i) => {
    seen = i;
    return { action: "wait", reasons: ["blurry date, want a second worker"] };
  };
  const others = [{ submissionId: "5", worker: "0x00000000000000000000000000000000000000b2" as const, answer: "x" }];
  const r = await reviewSubmission(input(agent, localSigner(), { otherAnswers: others }));
  assert.equal(r.outcome.action, "wait");
  assert.equal(r.entry.kind, "decision");
  assert.deepEqual((seen as { otherAnswers: unknown }).otherAnswers, others);
});

test("a malformed or failing agent can only escalate, never pay", async () => {
  const signer = localSigner();
  for (const bad of [{ action: "transfer", reasons: ["x"] }, { action: "pay" }, { action: "pay", reasons: [] }, "pay", null]) {
    const r = await reviewSubmission(input(says(bad), signer));
    assert.equal(r.outcome.action, "escalate", JSON.stringify(bad));
  }
  const failing: Agent = async () => {
    throw new Error("timeout");
  };
  const r = await reviewSubmission(input(failing, signer));
  assert.equal(r.outcome.action, "escalate");
  assert.match(r.decision.reasons[0], /agent failed: timeout/);
});

test("reviews of one campaign extend the same signed chain", async () => {
  const signer = localSigner();
  const first = await reviewSubmission(input(says({ action: "pay", reasons: ["ok"] }), signer));
  const second = await reviewSubmission(
    input(says({ action: "reject", reasons: ["total doesn't match"] }), signer, {
      lastEntry: first.entry,
      submission: { ...input(says(null), signer).submission, submissionId: "8" },
    }),
  );
  assert.equal(second.entry.kind, "rejection");
  assert.equal(second.entry.prev, first.entry.hash);
  assert.deepEqual(await verifyJournal([first.entry, second.entry], signer.address), { ok: true });
});

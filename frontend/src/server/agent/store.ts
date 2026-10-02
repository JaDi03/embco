// Where the agent keeps what it can't keep onchain: the submissions it is tracking,
// the workers' answers, the tasks and the decision journal. An interface, so the loop
// doesn't care where it lives: in memory for tests, Supabase in production.

import { keccak256, toHex, type Address, type Hex } from "viem";
import { canonical, GENESIS, type JournalEntry } from "./journal.ts";
import type { SubmissionStatus } from "./contract.ts";
import type { Task } from "./review.ts";

/** The hash a worker registers onchain for an answer. The worker app must compute it the same way. */
export function answerHash(answer: unknown): Hex {
  return keccak256(toHex(canonical(answer)));
}

export interface TrackedSubmission {
  submissionId: string;
  campaignId: string;
  worker: Address;
  taskId: number;
  contentHash: Hex;
  firstSeen: bigint; // block time the agent first saw it onchain
  status: SubmissionStatus; // last status read onchain
  lastEvidence: string | null; // what the agent had to go on at its last review
  waitingSince: bigint | null; // block time of the first review that left it open
}

/** A held pass lock. Only its holder can release it; it lapses on its own at `expiresAt`. */
export interface PassLock {
  expiresAt: number; // ms since epoch
  release(): Promise<void>;
}

export interface AgentStore {
  /** Takes the pass lock for `seconds`, or returns null while another pass holds it. */
  lock(seconds: number): Promise<PassLock | null>;

  /** Next onchain submission id the agent hasn't looked at. */
  cursor(): Promise<bigint>;
  setCursor(next: bigint): Promise<void>;

  track(submission: TrackedSubmission): Promise<void>;
  update(submissionId: string, patch: Partial<Omit<TrackedSubmission, "submissionId">>): Promise<void>;
  /** Tracked submissions still open onchain, oldest first. */
  open(): Promise<TrackedSubmission[]>;
  /** Every tracked submission to one task, whatever its status. */
  forTask(campaignId: string, taskId: number): Promise<TrackedSubmission[]>;

  /** The answer uploaded for a submission (untrusted), or undefined if none. */
  answer(submissionId: string): Promise<unknown>;
  task(campaignId: string, taskId: number): Promise<Task | null>;

  lastEntry(campaignId: string): Promise<JournalEntry | null>;
  /** Appends to the campaign's chain. Must refuse an entry that doesn't extend the last one. */
  append(entry: JournalEntry): Promise<void>;
}

export class MemoryStore implements AgentStore {
  private next = 0n;
  private readonly tracked = new Map<string, TrackedSubmission>();
  private readonly answers = new Map<string, unknown>();
  private readonly tasks = new Map<string, Task>();
  readonly journals = new Map<string, JournalEntry[]>();

  // Test setup: what the worker and agency apps will write.
  putAnswer(submissionId: string, answer: unknown) {
    this.answers.set(submissionId, structuredClone(answer));
  }
  putTask(campaignId: string, taskId: number, task: Task) {
    this.tasks.set(`${campaignId}:${taskId}`, task);
  }
  get(submissionId: string) {
    return this.tracked.get(submissionId);
  }

  private held: { token: symbol; expiresAt: number } | null = null;

  async lock(seconds: number) {
    const now = Date.now();
    if (this.held && this.held.expiresAt > now) return null;
    const held = { token: Symbol("pass"), expiresAt: now + seconds * 1000 };
    this.held = held;
    return {
      expiresAt: held.expiresAt,
      release: async () => {
        if (this.held?.token === held.token) this.held = null; // never someone else's lock
      },
    };
  }

  async cursor() {
    return this.next;
  }
  async setCursor(next: bigint) {
    this.next = next;
  }
  async track(s: TrackedSubmission) {
    if (!this.tracked.has(s.submissionId)) this.tracked.set(s.submissionId, { ...s });
  }
  async update(submissionId: string, patch: Partial<Omit<TrackedSubmission, "submissionId">>) {
    const s = this.tracked.get(submissionId);
    if (!s) throw new Error(`submission ${submissionId} is not tracked`);
    Object.assign(s, patch);
  }
  async open() {
    return [...this.tracked.values()]
      .filter((s) => s.status === "submitted")
      .sort((a, b) => Number(BigInt(a.submissionId) - BigInt(b.submissionId)))
      .map((s) => ({ ...s }));
  }
  async forTask(campaignId: string, taskId: number) {
    return [...this.tracked.values()].filter((s) => s.campaignId === campaignId && s.taskId === taskId).map((s) => ({ ...s }));
  }
  async answer(submissionId: string) {
    return structuredClone(this.answers.get(submissionId));
  }
  async task(campaignId: string, taskId: number) {
    return this.tasks.get(`${campaignId}:${taskId}`) ?? null;
  }
  async lastEntry(campaignId: string) {
    return this.journals.get(campaignId)?.at(-1) ?? null;
  }
  async append(entry: JournalEntry) {
    const chain = this.journals.get(entry.campaignId) ?? [];
    if (chain.some((e) => e.hash === entry.hash)) return; // same entry again: already recorded
    if (entry.prev !== (chain.at(-1)?.hash ?? GENESIS)) throw new Error(`journal fork in campaign ${entry.campaignId}`);
    this.journals.set(entry.campaignId, [...chain, entry]);
  }
}

// The agent's decision journal: append-only and hash-chained, one chain per
// campaign, every entry signed by the agent's key. An entry's hash is what
// goes onchain as `decisionHash`, so each payment points back to its reasoning.
// Storage lives elsewhere; this module only builds and verifies entries.

import { keccak256, toHex, recoverMessageAddress, type Address, type Hex } from "viem";

export type EntryKind = "decision" | "payment" | "escalation" | "rejection" | "refusal" | "report";

export interface JournalEntry {
  campaignId: string;
  seq: number;
  at: string; // ISO timestamp
  kind: EntryKind;
  data: unknown;
  prev: Hex;
  hash: Hex;
  sig: Hex;
}

/** Signs a 32-byte hash as an EIP-191 message. Backed by a local key in tests and by a Circle DCW in production. */
export interface HashSigner {
  address: Address;
  signHash(hash: Hex): Promise<Hex>;
}

export const GENESIS: Hex = `0x${"0".repeat(64)}`;

/** Deterministic JSON: sorted keys, bigints as strings. */
export function canonical(value: unknown): string {
  return JSON.stringify(value, (_k, v) => {
    if (typeof v === "bigint") return v.toString();
    if (v && typeof v === "object" && !Array.isArray(v)) {
      return Object.fromEntries(
        Object.keys(v)
          .sort()
          .map((k) => [k, (v as Record<string, unknown>)[k]]),
      );
    }
    return v;
  });
}

function entryHash(e: Omit<JournalEntry, "hash" | "sig">): Hex {
  const { campaignId, seq, at, kind, data, prev } = e;
  return keccak256(toHex(canonical({ campaignId, seq, at, kind, data, prev })));
}

/** Build and sign the entry that follows `last` (null for the first entry of a campaign). */
export async function createEntry(
  signer: HashSigner,
  campaignId: string,
  last: JournalEntry | null,
  kind: EntryKind,
  data: unknown,
  at = new Date().toISOString(),
): Promise<JournalEntry> {
  if (last && last.campaignId !== campaignId) throw new Error("previous entry belongs to another campaign");
  const body = {
    campaignId,
    seq: last ? last.seq + 1 : 0,
    at,
    kind,
    data: JSON.parse(canonical(data)) as unknown,
    prev: last?.hash ?? GENESIS,
  };
  const hash = entryHash(body);
  return { ...body, hash, sig: await signer.signHash(hash) };
}

export type VerifyResult = { ok: true } | { ok: false; seq: number; reason: string };

/** Anyone can check a campaign's whole chain with only the agent's public address. */
export async function verifyJournal(entries: JournalEntry[], agent: Address): Promise<VerifyResult> {
  let prev = GENESIS;
  const campaignId = entries[0]?.campaignId;
  for (const [i, e] of entries.entries()) {
    if (e.campaignId !== campaignId) return { ok: false, seq: i, reason: "mixed campaigns" };
    if (e.seq !== i) return { ok: false, seq: i, reason: "sequence gap" };
    if (e.prev !== prev) return { ok: false, seq: i, reason: "broken chain" };
    if (entryHash(e) !== e.hash) return { ok: false, seq: i, reason: "content altered" };
    const signer = await recoverMessageAddress({ message: { raw: e.hash }, signature: e.sig });
    if (signer.toLowerCase() !== agent.toLowerCase()) return { ok: false, seq: i, reason: "not signed by agent" };
    prev = e.hash;
  }
  return { ok: true };
}

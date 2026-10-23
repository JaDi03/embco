// One journal entry, or every entry behind one decision of the agent, proved against the roots on
// Arc, here in the browser. Same rules and same files as services/anchor/proof.py and decision.py:
// a proof made here is checked by `embco check-proof`, and one made there is checked here. Proofs
// are made when asked for, from the journal and the anchors (as RFC 6962 logs serve them), and
// never stored.

import { chainHash, merkleTree, sessionFor, verifyInclusion } from "./journal.js";

const GENESIS = "0".repeat(64);
const FIELDS = ["kind", "run", "invoice", "at", "body", "hash"];
const HOW_TO_CHECK = "embco check-proof <this file> --rpc-url <Arc Testnet node>";

function bodyOf(entry) {
  try {
    const body = JSON.parse(entry.body);
    return body && typeof body === "object" ? body : {};
  } catch {
    return {};
  }
}

/** An entry that no anchor on Arc covers yet: the next anchor will. */
export class NotAnchored extends Error {
  constructor(number) {
    super(`entry ${number} is not covered by an anchor on Arc yet`);
    this.number = number;
  }
}

/** The numbers (from 1) of the entries behind the agent's decision on `invoice` in `session`:
 * the session, the choice, the rule check it was made on, that check's run and policy, the owner's
 * answers to that check, the payments the session sent and the MANIFEST that lists them all. */
export function decisionEntries(entries, session, invoice) {
  const rows = entries.map((e, i) => ({ n: i + 1, e, b: bodyOf(e) }));
  const agent = rows.find((r) => r.e.kind === "AGENT" && r.e.invoice === invoice && r.b.session === session);
  if (!agent) throw new Error(`no decision on ${invoice} in session ${session}`);
  const mark = agent.b.fingerprint;
  const found = new Set([agent.n]);
  const before = rows.filter((r) => r.n < agent.n);
  for (const r of rows) {
    if (r.e.kind === "SESSION" && r.b.id === session) found.add(r.n);
    if ((r.e.kind === "PAYMENT" || r.e.kind === "MANIFEST") && r.e.invoice === invoice && r.b.session === session) found.add(r.n);
  }
  for (const r of before) {
    if (r.e.kind === "ANSWER" && r.e.invoice === invoice && r.b.fingerprint === mark) found.add(r.n);
  }
  const check = before.filter((r) => r.e.kind === "DECISION" && r.e.invoice === invoice && r.b.fingerprint === mark).at(-1);
  if (check) {
    found.add(check.n);
    const run = rows.find((r) => r.e.kind === "RUN" && r.e.run === check.e.run);
    if (run) {
      found.add(run.n);
      const policy = rows.filter((r) => r.n < run.n && r.e.kind === "POLICY" && r.b.policy === run.b.policy).at(-1);
      if (policy) found.add(policy.n);
    }
  }
  return [...found].sort((a, b) => a - b);
}

/** Makes proofs over one journal; one tree per anchor, so many proofs cost one tree each. */
export function prover(data, anchors) {
  const entries = data.entries;
  const trees = new Map();
  const treeOf = (anchor) => {
    if (!trees.has(anchor.size)) {
      trees.set(anchor.size, (async () => {
        const tree = await merkleTree(entries.slice(0, anchor.size).map((e) => e.hash));
        if ((await tree.root()) !== anchor.root) {
          throw new Error(`the first ${anchor.size} entries of this file do not match Arc, so no proof can be made from it`);
        }
        return tree;
      })());
    }
    return trees.get(anchor.size);
  };

  async function entry(number) {
    if (!(number >= 1 && number <= entries.length)) {
      throw new Error(`this file has entries 1 to ${entries.length}, not ${number}`);
    }
    const covering = anchors.filter((a) => a.size >= number && a.size <= entries.length);
    if (!covering.length) throw new NotAnchored(number);
    const anchor = covering.reduce((a, b) => (b.size < a.size ? b : a));
    const index = number - 1;
    const tree = await treeOf(anchor);
    return {
      shop: data.shop, number, index, size: anchor.size, root: anchor.root,
      previous_hash: index ? entries[index - 1].hash : GENESIS,
      entry: Object.fromEntries(FIELDS.map((f) => [f, entries[index][f]])),
      proof: await tree.proof(index),
      how_to_check: HOW_TO_CHECK,
    };
  }

  async function decision(session, invoice) {
    const proofs = [];
    for (const n of decisionEntries(entries, session, invoice)) proofs.push(await entry(n));
    return { shop: data.shop, session, invoice, proofs };
  }

  return { entry, decision };
}

/** Every decision of the agent that has a session id, proved; the ones not anchored yet are
 * counted, not proved. */
export async function allDecisionProofs(data, anchors) {
  const make = prover(data, anchors);
  const proofs = [];
  let pending = 0;
  for (const e of data.entries) {
    const session = e.kind === "AGENT" ? bodyOf(e).session : undefined;
    if (!session) continue;
    try {
      proofs.push(await make.decision(session, e.invoice));
    } catch (error) {
      if (!(error instanceof NotAnchored)) throw error;
      pending += 1;
    }
  }
  return { proofs, pending };
}

/** Recompute the entry, its place in the tree and whether Arc holds that root. The root is
 * taken only from `anchors` (read from Arc): whatever the bundle claims is checked. */
export async function checkEntryProof(bundle, anchors) {
  const entry = bundle?.entry;
  const index = bundle?.index;
  const size = bundle?.size;
  const ok = entry && typeof entry.hash === "string" && typeof bundle.previous_hash === "string"
    && typeof bundle.root === "string" && Array.isArray(bundle.proof)
    && Number.isInteger(index) && Number.isInteger(size);
  if (!ok) throw new Error("not a proof bundle: it needs the entry, index, size, root and proof");
  const root = bundle.root.toLowerCase();
  const contentMatchesHash = (await chainHash(bundle.previous_hash, entry)) === entry.hash;
  const included = await verifyInclusion(entry.hash, index, size, bundle.proof, root);
  const anchor = anchors.find((a) => a.size === size && a.root === root);
  return { contentMatchesHash, included, anchored: Boolean(anchor), anchoredAt: anchor?.anchoredAt ?? null,
    ok: contentMatchesHash && included && Boolean(anchor) };
}

export const isDecisionProof = (data) => Boolean(data) && typeof data.session === "string"
  && typeof data.invoice === "string" && Array.isArray(data.proofs);

/** Each proof against Arc, that every proved entry belongs to this decision, and that none its
 * MANIFEST lists was left out. `listed` is false for decisions written before the MANIFEST. */
export async function checkDecisionProof(bundle, anchors) {
  if (!isDecisionProof(bundle) || !bundle.proofs.length) {
    throw new Error("not a decision proof: it needs the session, the invoice and the proofs");
  }
  const proofs = [];
  for (const p of bundle.proofs) proofs.push(await checkEntryProof(p, anchors));
  const unlinked = unlinkedEntry(bundle.proofs, bundle.session, bundle.invoice);
  const manifests = bundle.proofs.filter((p) => p.entry?.kind === "MANIFEST");
  const listed = manifests.length === 1;
  const held = new Set(bundle.proofs.map((p) => p.entry?.hash));
  const missing = listed ? (bodyOf(manifests[0].entry).entries ?? []).filter((h) => !held.has(h)).length : 0;
  return { proofs, unlinked, listed, missing,
    ok: unlinked === null && listed && missing === 0 && proofs.every((p) => p.ok) };
}

function unlinkedEntry(bundleProofs, session, invoice) {
  // ordered by `index`, the place the audit path proves, not by the `number` the bundle states
  const rows = bundleProofs.map((p) => ({ n: p.index, e: p.entry, b: bodyOf(p.entry) }));
  const agents = rows.filter((r) => r.e.kind === "AGENT");
  if (agents.length !== 1) return "a decision proof must hold exactly one AGENT entry";
  const manifests = rows.filter((r) => r.e.kind === "MANIFEST");
  if (manifests.length > 1) return "a decision proof must hold at most one MANIFEST entry";
  const manifest = manifests[0];
  const mark = agents[0].b.fingerprint;
  const runs = new Map(rows.filter((r) => r.e.kind === "RUN").map((r) => [r.e.run, r.b.policy]));
  const checked = new Set(rows.filter((r) => r.e.kind === "DECISION").map((r) => r.e.run));
  for (const { n, e, b } of rows) {
    const linked = {
      SESSION: b.id === session,
      AGENT: e.invoice === invoice && b.session === session,
      PAYMENT: e.invoice === invoice && b.session === session,
      MANIFEST: e.invoice === invoice && b.session === session,
      ANSWER: e.invoice === invoice && b.fingerprint === mark,
      DECISION: e.invoice === invoice && b.fingerprint === mark && runs.has(e.run),
      RUN: checked.has(e.run),
      POLICY: [...runs.values()].includes(b.policy),
    }[e.kind] ?? false;
    if (!linked) return `the ${e.kind} entry at ${e.at} is not part of this decision`;
    // only a payment state written after the manifest can be missing from it
    if (manifest && e.kind !== "MANIFEST" && !(manifest.b.entries ?? []).includes(e.hash)
      && !(e.kind === "PAYMENT" && n > manifest.n)) {
      return `the ${e.kind} entry at ${e.at} is not in the decision's MANIFEST`;
    }
  }
  return null;
}

/** What proves a receipt: the decision that paid it, when its session has an id, or else the
 * session entry that moved it. Null when no session of the agent moved it. */
export function receiptEvidence(entries, tx) {
  const found = sessionFor(entries, tx);
  if (!found) return null;
  return found.session && found.invoice ? { session: found.session, invoice: found.invoice }
    : { number: found.index + 1 };
}

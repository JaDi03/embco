// The shop's journal, checked here in the browser, not on our server: the hash chain is
// recomputed entry by entry, then the Merkle root (RFC 9162) of the first N entries is compared
// with every root the agent wrote on Arc. Same rules as agent/memory (models.py, merkle.py).

const DOMAIN = "embco-journal-v1";
const GENESIS = "0".repeat(64);
const encoder = new TextEncoder();

async function sha256(bytes) {
  return new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
}

const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (hex) => Uint8Array.from(hex.match(/../g) ?? [], (h) => parseInt(h, 16));

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

/** The entry as Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 * writes it: its fields are strings, integers or null, and JSON.stringify escapes strings the
 * same way. */
export function canonicalContent(e) {
  const value = (v) => (v === null || v === undefined ? "null" : JSON.stringify(v));
  return `{"at":${value(e.at)},"body":${value(e.body)},"invoice":${value(e.invoice)},`
    + `"kind":${value(e.kind)},"run":${value(e.run)}}`;
}

/** The exact text an entry's hash is taken of (what DecisionVerifier hashes on Arc). */
export function chainText(previous, entry) {
  return `${DOMAIN}\n${previous}\n${canonicalContent(entry)}`;
}

export async function chainHash(previous, entry) {
  return toHex(await sha256(encoder.encode(chainText(previous, entry))));
}

/** {ok: true} when every stored hash is the hash of its entry and the one before it. */
export async function verifyChain(entries) {
  let previous = GENESIS;
  for (const [i, e] of entries.entries()) {
    const expected = await chainHash(previous, e);
    if (expected !== e.hash) return { ok: false, entry: i + 1, kind: e.kind };
    previous = expected;
  }
  return { ok: true };
}

async function subtree(nodes) {
  if (nodes.length === 1) return nodes[0];
  const split = 2 ** (Math.ceil(Math.log2(nodes.length)) - 1);
  const [left, right] = await Promise.all([subtree(nodes.slice(0, split)), subtree(nodes.slice(split))]);
  return sha256(concat(Uint8Array.of(1), left, right));
}

/** RFC 9162 Merkle tree hash over leaves given as hex (the entries' chain hashes). */
export async function merkleRoot(leavesHex) {
  if (!leavesHex.length) return toHex(await sha256(new Uint8Array()));
  const leaves = await Promise.all(leavesHex.map((h) => sha256(concat(Uint8Array.of(0), fromHex(h)))));
  return toHex(await subtree(leaves));
}

/** One RFC 9162 tree over leaves given as hex, for its root and the audit path of any leaf
 * (section 2.1.3.1). Inner hashes are kept, so many proofs over one tree cost one tree. */
export async function merkleTree(leavesHex) {
  const leaves = await Promise.all(leavesHex.map((h) => sha256(concat(Uint8Array.of(0), fromHex(h)))));
  const kept = new Map();
  const splitOf = (lo, hi) => lo + 2 ** (Math.ceil(Math.log2(hi - lo)) - 1);
  const hash = (lo, hi) => {
    if (hi - lo === 1) return Promise.resolve(leaves[lo]);
    const key = `${lo}:${hi}`;
    if (!kept.has(key)) {
      const split = splitOf(lo, hi);
      kept.set(key, Promise.all([hash(lo, split), hash(split, hi)])
        .then(([left, right]) => sha256(concat(Uint8Array.of(1), left, right))));
    }
    return kept.get(key);
  };
  const path = async (index, lo, hi) => {
    if (hi - lo <= 1) return [];
    const split = splitOf(lo, hi);
    return index < split
      ? [...await path(index, lo, split), await hash(split, hi)]
      : [...await path(index, split, hi), await hash(lo, split)];
  };
  return {
    size: leaves.length,
    root: async () => toHex(leaves.length ? await hash(0, leaves.length) : await sha256(new Uint8Array())),
    proof: async (index) => {
      if (!(Number.isInteger(index) && index >= 0 && index < leaves.length)) {
        throw new Error(`leaf ${index} is not in a tree of ${leaves.length} leaves`);
      }
      return (await path(index, 0, leaves.length)).map(toHex);
    },
  };
}

const HASH = /^[0-9a-f]{64}$/;

/** RFC 9162 section 2.1.3.2, as agent/memory/merkle.py: does the leaf sit at `index` in a tree of
 * `size` leaves with this root? False on any bad input, never an error. */
export async function verifyInclusion(leafHex, index, size, proofHex, rootHex) {
  if (!(Number.isInteger(index) && Number.isInteger(size) && index >= 0 && index < size)) return false;
  if (![leafHex, rootHex, ...proofHex].every((h) => typeof h === "string" && HASH.test(h))) return false;
  let fn = index;
  let sn = size - 1;
  let r = await sha256(concat(Uint8Array.of(0), fromHex(leafHex)));
  for (const p of proofHex) {
    if (sn === 0) return false;
    if (fn % 2 === 1 || fn === sn) {
      r = await sha256(concat(Uint8Array.of(1), fromHex(p), r));
      if (fn % 2 === 0) {
        while (fn && fn % 2 === 0) {
          fn = Math.floor(fn / 2);
          sn = Math.floor(sn / 2);
        }
      }
    } else {
      r = await sha256(concat(Uint8Array.of(1), r, fromHex(p)));
    }
    fn = Math.floor(fn / 2);
    sn = Math.floor(sn / 2);
  }
  return sn === 0 && toHex(r) === rootHex;
}

/** What does not match: one sentence per anchor the copy fails. Empty means it matches. */
export async function mismatches(entries, anchors) {
  const hashes = entries.map((e) => e.hash);
  const problems = [];
  for (const [i, a] of anchors.entries()) {
    if (a.size > hashes.length) {
      problems.push(`Anchor ${i + 1} covers ${a.size} entries but the journal has only ${hashes.length}: entries were removed.`);
    } else if ((await merkleRoot(hashes.slice(0, a.size))) !== a.root) {
      problems.push(`Anchor ${i + 1}: the first ${a.size} entries do not give the root written on Arc.`);
    }
  }
  return problems;
}

/** A downloaded journal read from its text; throws a sentence when it is not one. */
export function parseJournal(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("That file is not JSON.");
  }
  const fields = ["kind", "run", "invoice", "at", "body", "hash"];
  const ok = data && typeof data.shop === "string" && Array.isArray(data.entries)
    && data.entries.every((e) => e && fields.every((f) => f in e));
  if (!ok) throw new Error("That is not an Embco journal download: it needs a shop and its entries.");
  return data;
}

/** The audit as rows: the chain, the root of the whole copy and one row per anchor read from Arc. */
export async function report(entries, anchors) {
  const hashes = entries.map((e) => e.hash);
  const rows = [];
  for (const [i, a] of anchors.entries()) {
    const fileRoot = a.size <= hashes.length ? await merkleRoot(hashes.slice(0, a.size)) : null;
    rows.push({ number: i + 1, size: a.size, anchoredAt: a.anchoredAt, arcRoot: a.root, fileRoot,
      matches: fileRoot === a.root });
  }
  return { chain: await verifyChain(entries), entries: entries.length,
    root: await merkleRoot(hashes), rows };
}

/** The whole check: the chain, then every anchor. */
export async function check(entries, anchors) {
  const chain = await verifyChain(entries);
  if (!chain.ok) {
    return { ok: false, problems: [`Entry ${chain.entry} (${chain.kind}) does not match its hash.`] };
  }
  const problems = await mismatches(entries, anchors);
  const last = anchors.at(-1);
  return { ok: problems.length === 0, problems, entries: entries.length,
    anchored: last?.size ?? 0, anchoredAt: last?.anchoredAt ?? null };
}

function parsed(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** The session that moved this transaction: what woke the agent, each tool it called with what
 * it read, and its summary. Null when the journal has no such session. */
export function sessionOf(entries, tx) {
  const found = sessionFor(entries, tx);
  if (!found) return null;
  const body = parsed(entries[found.index].body);
  const steps = body.trace ?? [];
  return {
    at: entries[found.index].at,
    woke: (body.wakes ?? []).map((w) => w.text),
    steps: steps.map((s) => ({ tool: s.tool, args: s.args ?? {}, ok: s.ok !== false, tx: s.tx ?? "",
      refused: s.refused ?? "", saw: s.saw === undefined ? null : parsed(s.saw) })),
    summary: body.summary ?? "",
  };
}

/** Where the session that moved this transaction is: {index, session, invoice}, or null. A
 * payment says which session sent it (its PAYMENT entries carry the session's id); for a move of
 * funds, or a payment from before sessions had an id, the session's own steps say it. */
export function sessionFor(entries, tx) {
  const wanted = String(tx ?? "").toLowerCase();
  if (!wanted) return null;
  const same = (value) => String(value ?? "").toLowerCase() === wanted;
  const payment = entries.find((e) => e.kind === "PAYMENT" && same(parsed(e.body).tx_hash) && parsed(e.body).session);
  if (payment) {
    const id = parsed(payment.body).session;
    const index = entries.findIndex((e) => e.kind === "SESSION" && parsed(e.body).id === id);
    if (index >= 0) return { index, session: id, invoice: payment.invoice };
  }
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].kind !== "SESSION") continue;
    const body = parsed(entries[i].body);
    const step = (body.trace ?? []).find((s) => same(s.tx));
    if (!step && !(body.funds_moved ?? []).some((m) => same(m.tx))) continue;
    const invoice = step?.tool === "pay_now" ? step.args?.invoice ?? null : null;
    return { index: i, session: body.id || null, invoice };
  }
  return null;
}

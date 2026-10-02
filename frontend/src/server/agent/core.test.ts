import { test } from "node:test";
import assert from "node:assert/strict";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { parseUsdc, formatUsdc, PrecisionError } from "./money.ts";
import { Ledger, LedgerError, type Transaction } from "./ledger.ts";
import { createEntry, verifyJournal, type HashSigner, type JournalEntry } from "./journal.ts";

// --- money -----------------------------------------------------------------

test("parses and formats USDC exactly", () => {
  assert.equal(parseUsdc("249.995"), 249_995_000n);
  assert.equal(parseUsdc("0.000001"), 1n);
  assert.equal(formatUsdc(249_995_000n), "249.995000");
  assert.equal(formatUsdc(-1n), "-0.000001");
});

test("refuses more precision than USDC has instead of rounding", () => {
  assert.throws(() => parseUsdc("1.0000001"), PrecisionError);
  assert.throws(() => parseUsdc("1e3"), PrecisionError);
  assert.throws(() => parseUsdc("12,50"), PrecisionError);
});

// --- ledger ----------------------------------------------------------------

const payment = (submissionId: string, amount: string): Transaction => ({
  date: "2026-10-01",
  narration: `Reward for submission ${submissionId}`,
  doc: { kind: "submission", id: submissionId },
  postings: [
    { account: "Expenses:Labor:Tasks", amount: parseUsdc(amount) },
    { account: "Assets:Escrow:USDC", amount: -parseUsdc(amount) },
  ],
});

test("books a balanced payment with its document", () => {
  const l = new Ledger();
  l.post(payment("7", "0.05"));
  assert.equal(l.balance("Expenses:Labor"), parseUsdc("0.05"));
  assert.equal(l.balance("Assets:Escrow:USDC"), -parseUsdc("0.05"));
});

test("refuses an unbalanced entry instead of booking round-off", () => {
  const l = new Ledger();
  assert.throws(
    () =>
      l.post({
        ...payment("8", "0.05"),
        postings: [
          { account: "Expenses:Labor:Tasks", amount: parseUsdc("0.05") },
          { account: "Assets:Escrow:USDC", amount: -parseUsdc("0.049995") },
        ],
      }),
    /unbalanced by 0.000005/,
  );
});

test("refuses entries without a document, bad dates and duplicates", () => {
  const l = new Ledger();
  assert.throws(() => l.post({ ...payment("x", "1"), doc: { kind: "submission", id: "" } }), LedgerError);
  assert.throws(() => l.post({ ...payment("y", "1"), date: "01/10/2026" }), /bad date/);
  l.post(payment("9", "1"));
  assert.throws(() => l.post(payment("9", "1")), /already booked/);
});

test("balance assertion catches books that disagree with the chain", () => {
  const l = new Ledger();
  l.post({
    date: "2026-10-01",
    narration: "Campaign funding",
    doc: { kind: "funding", id: "0xabc" },
    postings: [
      { account: "Assets:Escrow:USDC", amount: parseUsdc("20") },
      { account: "Equity:Funding", amount: -parseUsdc("20") },
    ],
  });
  l.assertBalance("Assets:Escrow:USDC", parseUsdc("20"));
  assert.throws(() => l.assertBalance("Assets:Escrow:USDC", parseUsdc("19.999999")), /books say 20.000000/);
});

test("exports beancount with exact six decimals", () => {
  const l = new Ledger();
  l.post({ ...payment("10", "0.000001"), txHash: "0xfeed" });
  const out = l.toBeancount();
  assert.match(out, /Expenses:Labor:Tasks\s+0\.000001 USDC/);
  assert.match(out, /doc: "submission:10"/);
  assert.match(out, /tx: "0xfeed"/);
});

// --- journal ---------------------------------------------------------------

function localSigner(): HashSigner {
  const account = privateKeyToAccount(generatePrivateKey());
  return { address: account.address, signHash: (hash) => account.signMessage({ message: { raw: hash } }) };
}

async function chain(signer: HashSigner, campaignId: string, n: number): Promise<JournalEntry[]> {
  const entries: JournalEntry[] = [];
  for (let i = 0; i < n; i++) {
    entries.push(
      await createEntry(signer, campaignId, entries.at(-1) ?? null, "decision", { action: "pay", amount: 50_000n, i }),
    );
  }
  return entries;
}

test("journal is signed, chained and verifiable by address", async () => {
  const agent = localSigner();
  const entries = await chain(agent, "1", 3);
  assert.equal(entries[2].seq, 2);
  assert.equal(entries[1].prev, entries[0].hash);
  assert.equal((entries[0].data as { amount: string }).amount, "50000", "bigints stored as strings");
  assert.deepEqual(await verifyJournal(entries, agent.address), { ok: true });
});

test("journal detects tampering, reordering and impostors", async () => {
  const agent = localSigner();
  const entries = await chain(agent, "1", 3);

  const altered = structuredClone(entries);
  (altered[0].data as { amount: string }).amount = "5000000";
  assert.deepEqual(await verifyJournal(altered, agent.address), { ok: false, seq: 0, reason: "content altered" });

  assert.equal((await verifyJournal([entries[1], entries[2]], agent.address)).ok, false, "dropped entry");
  assert.equal((await verifyJournal([entries[0], entries[2]], agent.address)).ok, false, "gap");

  const impostor = localSigner();
  assert.deepEqual(await verifyJournal(entries, impostor.address), { ok: false, seq: 0, reason: "not signed by agent" });
});

test("journal chains are per campaign", async () => {
  const agent = localSigner();
  const [a] = await chain(agent, "1", 1);
  await assert.rejects(createEntry(agent, "2", a, "decision", {}), /another campaign/);
  const [b] = await chain(agent, "2", 1);
  assert.deepEqual(await verifyJournal([a, { ...b, seq: 1, prev: a.hash }], agent.address), {
    ok: false,
    seq: 1,
    reason: "mixed campaigns",
  });
});

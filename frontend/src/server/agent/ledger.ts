// Document-is-entry double-entry ledger, following "Agents and Ledgers in 2026":
// every transaction must point at a document, must balance exactly, and a
// document can't be booked twice. Problems are refused loudly, never rounded.

import { formatUsdc } from "./money.ts";

export type DocKind = "funding" | "submission" | "withdrawal";

export interface DocRef {
  kind: DocKind;
  id: string;
}

export interface Posting {
  account: string;
  amount: bigint; // USDC base units, positive = debit
}

export interface Transaction {
  date: string; // YYYY-MM-DD
  narration: string;
  doc: DocRef;
  postings: Posting[];
  txHash?: string; // onchain witness, when there is one
}

export class LedgerError extends Error {}

const ACCOUNT = /^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z][A-Za-z0-9-]*)+$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export class Ledger {
  readonly transactions: Transaction[] = [];
  private readonly booked = new Set<string>();

  post(tx: Transaction): void {
    if (!tx.doc?.id) throw new LedgerError("refused: transaction has no source document");
    if (!DATE.test(tx.date)) throw new LedgerError(`refused: bad date "${tx.date}"`);
    const key = `${tx.doc.kind}:${tx.doc.id}`;
    if (this.booked.has(key)) throw new LedgerError(`refused: ${key} is already booked`);
    if (tx.postings.length < 2) throw new LedgerError("refused: needs at least two postings");
    for (const p of tx.postings) {
      if (!ACCOUNT.test(p.account)) throw new LedgerError(`refused: bad account name "${p.account}"`);
      if (p.amount === 0n) throw new LedgerError("refused: zero posting");
    }
    const sum = tx.postings.reduce((s, p) => s + p.amount, 0n);
    if (sum !== 0n) {
      throw new LedgerError(`refused: unbalanced by ${formatUsdc(sum)} USDC (no silent round-off)`);
    }
    this.booked.add(key);
    this.transactions.push(structuredClone(tx));
  }

  has(doc: DocRef): boolean {
    return this.booked.has(`${doc.kind}:${doc.id}`);
  }

  balance(account: string): bigint {
    let total = 0n;
    for (const tx of this.transactions) {
      for (const p of tx.postings) {
        if (p.account === account || p.account.startsWith(account + ":")) total += p.amount;
      }
    }
    return total;
  }

  /** Beancount-style assertion: the books must agree with the outside world exactly. */
  assertBalance(account: string, expected: bigint): void {
    const actual = this.balance(account);
    if (actual !== expected) {
      throw new LedgerError(
        `balance assertion failed for ${account}: books say ${formatUsdc(actual)}, witness says ${formatUsdc(expected)}`,
      );
    }
  }

  toBeancount(): string {
    const accounts = new Set(this.transactions.flatMap((t) => t.postings.map((p) => p.account)));
    const first = this.transactions.map((t) => t.date).sort()[0] ?? "2026-01-01";
    const lines = ['option "operating_currency" "USDC"', ""];
    for (const a of [...accounts].sort()) lines.push(`${first} open ${a} USDC`);
    for (const tx of this.transactions) {
      lines.push("", `${tx.date} * ${JSON.stringify(tx.narration)}`);
      lines.push(`  doc: "${tx.doc.kind}:${tx.doc.id}"`);
      if (tx.txHash) lines.push(`  tx: "${tx.txHash}"`);
      for (const p of tx.postings) lines.push(`  ${p.account.padEnd(40)} ${formatUsdc(p.amount).padStart(16)} USDC`);
    }
    return lines.join("\n") + "\n";
  }
}

// Deterministic format checks per task type. They find hard facts (a total that
// doesn't add up, a copied text); they never judge quality, that's the agent's job.

export interface ReceiptLine {
  description: string;
  amount: string; // decimal text as written on the receipt, e.g. "12.50"
}

export interface ReceiptAnswer {
  lines: ReceiptLine[];
  total: string;
}

const AMOUNT = /^(\d+)(?:\.(\d{1,2}))?$/;

/** Receipt amounts in cents. More than 2 decimals or anything non-numeric is a format error, not something to round. */
function cents(text: string): bigint | null {
  const m = AMOUNT.exec(text.trim());
  if (!m) return null;
  return BigInt(m[1]) * 100n + BigInt((m[2] ?? "").padEnd(2, "0"));
}

export function checkReceipt(answer: ReceiptAnswer): string[] {
  const errors: string[] = [];
  if (answer.lines.length === 0) errors.push("receipt has no lines");

  let sum = 0n;
  for (const [i, line] of answer.lines.entries()) {
    if (!line.description.trim()) errors.push(`line ${i + 1} has no description`);
    const value = cents(line.amount);
    if (value === null) errors.push(`line ${i + 1} amount "${line.amount}" is not a valid amount`);
    else sum += value;
  }

  const total = cents(answer.total);
  if (total === null) errors.push(`total "${answer.total}" is not a valid amount`);
  else if (errors.length === 0 && sum !== total) {
    errors.push(`lines add up to ${formatCents(sum)} but the total says ${formatCents(total)}`);
  }
  return errors;
}

function formatCents(value: bigint): string {
  return `${value / 100n}.${(value % 100n).toString().padStart(2, "0")}`;
}

/** Free-text tasks (write, translate): an answer identical to another worker's, ignoring case and spacing, is a copy. */
export function findCopy(text: string, others: { submissionId: string; text: string }[]): string | null {
  const norm = (s: string) => s.trim().replace(/\s+/g, " ").toLowerCase();
  const mine = norm(text);
  if (!mine) return null;
  return others.find((o) => norm(o.text) === mine)?.submissionId ?? null;
}

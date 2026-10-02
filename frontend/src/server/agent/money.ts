// USDC amounts are bigint base units (6 decimals). Never floats, never rounding:
// an amount with more precision than USDC has is an error, not something to fix.

export const USDC_DECIMALS = 6;
const UNIT = 10n ** BigInt(USDC_DECIMALS);
const PATTERN = /^(-)?(\d+)(?:\.(\d{1,6}))?$/;

export class PrecisionError extends Error {}

export function parseUsdc(text: string): bigint {
  const m = PATTERN.exec(text.trim());
  if (!m) throw new PrecisionError(`not a USDC amount with at most 6 decimals: "${text}"`);
  const [, sign, whole, frac = ""] = m;
  const value = BigInt(whole) * UNIT + BigInt(frac.padEnd(USDC_DECIMALS, "0"));
  return sign ? -value : value;
}

export function formatUsdc(units: bigint): string {
  const sign = units < 0n ? "-" : "";
  const abs = units < 0n ? -units : units;
  const frac = (abs % UNIT).toString().padStart(USDC_DECIMALS, "0");
  return `${sign}${abs / UNIT}.${frac}`;
}

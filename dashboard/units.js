// USDC amounts as exact integers (6 decimals). No floating point ever touches money.

export const USDC_DECIMALS = 6;
const SCALE = 10n ** BigInt(USDC_DECIMALS);
const AMOUNT = /^(\d+)(?:\.(\d{1,6}))?$/;

export class AmountError extends Error {}

/** "12.5" -> 12500000n. Accepts plain digits with up to 6 decimals; nothing else. */
export function parseUsdc(text) {
  const value = String(text ?? "").trim();
  const match = AMOUNT.exec(value);
  if (!match) {
    throw new AmountError("Enter an amount like 250 or 12.50 (up to 6 decimals).");
  }
  const whole = BigInt(match[1]);
  const fraction = BigInt((match[2] ?? "").padEnd(USDC_DECIMALS, "0"));
  const amount = whole * SCALE + fraction;
  if (amount === 0n) {
    throw new AmountError("The amount must be greater than zero.");
  }
  return amount;
}

/** 12500000n -> "12.5"; 0n -> "0". */
export function formatUsdc(amount) {
  const value = BigInt(amount);
  const sign = value < 0n ? "-" : "";
  const abs = value < 0n ? -value : value;
  const whole = abs / SCALE;
  const fraction = (abs % SCALE).toString().padStart(USDC_DECIMALS, "0").replace(/0+$/, "");
  const grouped = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction ? `${sign}${grouped}.${fraction}` : `${sign}${grouped}`;
}

export function shortAddress(address) {
  return address ? `${address.slice(0, 6)}...${address.slice(-4)}` : "";
}

export const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";

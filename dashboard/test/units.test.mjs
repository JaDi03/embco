import assert from "node:assert/strict";
import { test } from "node:test";

import { describeError } from "../errors.js";
import { AmountError, formatUsdc, parseUsdc, shortAddress } from "../units.js";

test("amounts become exact 6-decimal integers", () => {
  assert.equal(parseUsdc("250"), 250_000_000n);
  assert.equal(parseUsdc("12.5"), 12_500_000n);
  assert.equal(parseUsdc(" 0.000001 "), 1n);
  assert.equal(parseUsdc("1000000.123456"), 1_000_000_123_456n);
});

test("anything that is not a plain positive amount is refused", () => {
  for (const bad of ["", "abc", "-5", "1,000", "1.2345678", "1e3", "0", "0.000000", ".5", "5."]) {
    assert.throws(() => parseUsdc(bad), AmountError, `accepted ${JSON.stringify(bad)}`);
  }
});

test("amounts are shown without floating point and with thousands separators", () => {
  assert.equal(formatUsdc(0n), "0");
  assert.equal(formatUsdc(12_500_000n), "12.5");
  assert.equal(formatUsdc(1n), "0.000001");
  assert.equal(formatUsdc(1_234_567_890_000n), "1,234,567.89");
  assert.equal(formatUsdc(parseUsdc("999999999999.999999")), "999,999,999,999.999999");
});

test("addresses are shortened for display", () => {
  assert.equal(shortAddress("0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8"), "0x4d8e...95A8");
  assert.equal(shortAddress(null), "");
});

test("contract and wallet errors read as plain sentences", () => {
  assert.equal(describeError({ code: "ACTION_REJECTED" }), "You cancelled the request in MetaMask.");
  assert.equal(describeError({ code: 4001 }), "You cancelled the request in MetaMask.");
  assert.equal(
    describeError({ revert: { name: "NotOwner" } }),
    "Only the owner of this shop contract can do that.",
  );
  assert.match(describeError({ revert: { name: "OverWeeklyCap" } }), /this week's cap/);
  assert.match(describeError({ code: "INSUFFICIENT_FUNDS" }), /faucet/);
  assert.equal(describeError(new AmountError("bad")), "bad");
  assert.ok(describeError({ message: "x".repeat(500) }).length <= 203);
});

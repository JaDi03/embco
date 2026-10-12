import assert from "node:assert/strict";
import { test } from "node:test";

const { pickWallet } = await import("../wallet.js");

const metamask = { info: { rdns: "io.metamask" }, provider: { name: "metamask" } };
const rabby = { info: { rdns: "io.rabby" }, provider: { name: "rabby" } };
const injected = { name: "window.ethereum" };

test("MetaMask is used even when another wallet took window.ethereum", () => {
  assert.equal(pickWallet([rabby, metamask], injected), metamask.provider);
});

test("without MetaMask, the first wallet that announced itself is used", () => {
  assert.equal(pickWallet([rabby], injected), rabby.provider);
});

test("a wallet that does not announce itself is still found on window.ethereum", () => {
  assert.equal(pickWallet([], injected), injected);
});

test("no wallet at all is null", () => {
  assert.equal(pickWallet([], undefined), null);
});

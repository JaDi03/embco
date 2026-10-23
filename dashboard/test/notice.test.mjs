import { test } from "node:test";
import assert from "node:assert/strict";
import { setNotice, SUCCESS_MS } from "../notice.js";

// Just enough of the DOM for the status line: elements with children, text and click listeners.
function element(doc) {
  const node = {
    ownerDocument: doc, className: "", hidden: true, children: [], listeners: {},
    set textContent(text) { this.children = text ? [{ text }] : []; },
    get textContent() { return this.children.map((c) => c.text ?? c.textContent ?? "").join(""); },
    replaceChildren(...kids) { this.children = kids; },
    setAttribute(name, value) { this[name] = value; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
  };
  return node;
}
const doc = { createElement: () => element(doc) };
function clock() {
  const pending = new Map();
  let next = 0;
  return {
    setTimer: (fn, ms) => { next += 1; pending.set(next, { fn, ms }); return next; },
    clearTimer: (id) => pending.delete(id),
    pending,
    run: () => { for (const [id, t] of [...pending]) { pending.delete(id); t.fn(); } },
  };
}

test("something in progress stays, with no close button", () => {
  const node = element(doc);
  const c = clock();
  setNotice(node, "Create your shop contract: confirm it in MetaMask...", "info", "notice", c);
  assert.equal(node.hidden, false);
  assert.equal(node.className, "notice");
  assert.equal(node.textContent, "Create your shop contract: confirm it in MetaMask...");
  assert.equal(c.pending.size, 0);
});

test("a success hides itself after a few seconds, and can be closed before", () => {
  const node = element(doc);
  const c = clock();
  setNotice(node, "Link copied.", "success", "notice", c);
  assert.equal(node.className, "notice success");
  assert.equal(node.children[1].className, "notice-close");
  assert.equal([...c.pending.values()][0].ms, SUCCESS_MS);
  c.run();
  assert.equal(node.hidden, true);
  assert.equal(node.textContent, "");

  setNotice(node, "Saved.", "success", "notice", c);
  node.children[1].listeners.click();
  assert.equal(node.hidden, true);
  assert.equal(c.pending.size, 0);  // its timer went with it
});

test("an error stays until it is closed", () => {
  const node = element(doc);
  const c = clock();
  setNotice(node, "Could not copy the link.", "error", "notice modal-notice", c);
  assert.equal(node.className, "notice modal-notice error");
  assert.equal(c.pending.size, 0);
  node.children[1].listeners.click();
  assert.equal(node.hidden, true);
  assert.equal(node.className, "notice modal-notice");
});

test("a new notice replaces the old one and its timer", () => {
  const node = element(doc);
  const c = clock();
  setNotice(node, "Saved.", "success", "notice", c);
  setNotice(node, "Waiting for Arc to confirm...", "info", "notice", c);
  assert.equal(c.pending.size, 0);  // the success's timer must not hide the step in progress
  assert.equal(node.textContent, "Waiting for Arc to confirm...");
});

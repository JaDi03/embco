// The status line of a page. Something in progress stays until the next step replaces it; a success
// hides itself after a few seconds; an error stays until it is closed. Both can be closed with ×.

export const SUCCESS_MS = 8000;
const timers = new WeakMap();

/** Show `text` in `node` (empty text hides it). `base` is the node's own classes. */
export function setNotice(node, text, kind = "info", base = "notice", { setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  clearTimer(timers.get(node));
  timers.delete(node);
  node.className = kind === "info" ? base : `${base} ${kind}`;
  node.hidden = !text;
  if (!text) {
    node.replaceChildren();
    return;
  }
  if (kind === "info") {
    node.textContent = text;
    return;
  }
  const doc = node.ownerDocument;
  const message = doc.createElement("span");
  message.textContent = text;
  const close = doc.createElement("button");
  Object.assign(close, { type: "button", className: "notice-close", textContent: "×", title: "Close" });
  close.setAttribute("aria-label", "Close");
  close.addEventListener("click", () => setNotice(node, "", "info", base, { setTimer, clearTimer }));
  node.replaceChildren(message, close);
  if (kind === "success") {
    timers.set(node, setTimer(() => setNotice(node, "", "info", base, { setTimer, clearTimer }), SUCCESS_MS));
  }
}

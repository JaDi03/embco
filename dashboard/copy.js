// The copy button: an icon of two overlapping squares, the label on hover and for screen readers.
// Icons from Lucide ("copy" and "check", ISC license), inline so the page loads nothing more.

const SVG = 'viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
const COPY = `<svg ${SVG}><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>`
  + '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';
const DONE = `<svg ${SVG}><path d="M20 6 9 17l-5-5"/></svg>`;
const DONE_FOR_MS = 1500;

/**
 * A button that copies `value` (text, or a function that returns it) and shows a check for a
 * moment. `report(error)` is called after each try, with null when it worked.
 */
export function copyButton(label, value, report = () => {}, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `copy-button ${className}`.trim();
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = COPY;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(typeof value === "function" ? await value() : value);
    } catch (error) {
      report(error);
      return;
    }
    button.innerHTML = DONE;
    setTimeout(() => { button.innerHTML = COPY; }, DONE_FOR_MS);
    report(null);
  });
  return button;
}

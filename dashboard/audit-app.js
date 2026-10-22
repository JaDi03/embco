// The audit page: a downloaded journal checked in the browser against the roots the agent wrote on
// Arc. No sign-in and no upload; the roots come from the JournalAnchor contract, never from the file.

import { ARC_TESTNET, JOURNAL_ANCHOR_ADDRESS } from "./config.js";
import { describeError } from "./errors.js";
import { parseJournal, report } from "./journal.js";
import { journalAnchors } from "./shop.js";
import "./theme.js";

const $ = (id) => document.getElementById(id);
const short = (hex) => `${hex.slice(0, 12)}...${hex.slice(-6)}`;
const when = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

$("anchor-link").href = `${ARC_TESTNET.explorer}/address/${JOURNAL_ANCHOR_ADDRESS}`;

function setStatus(text, kind = "") {
  const status = $("audit-status");
  status.className = `journal-status ${kind}`.trim();
  status.textContent = text;
}

function clear() {
  $("audit-table-card").hidden = true;
  $("audit-facts").hidden = true;
  $("audit-rows").replaceChildren();
}

function cell(text, className = "") {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function render(data, audit) {
  const rows = audit.rows.map((r) => {
    const tr = document.createElement("tr");
    const result = r.matches ? "✓ Matches" : r.fileRoot === null ? "✗ Entries missing" : "✗ Different";
    tr.append(cell(r.number), cell(r.size), cell(when(r.anchoredAt)), cell(short(r.arcRoot), "mono"),
      cell(r.fileRoot ? short(r.fileRoot) : "-", "mono"), cell(result, r.matches ? "status-on" : "status-off"));
    return tr;
  });
  $("audit-rows").replaceChildren(...rows);
  $("audit-table-card").hidden = rows.length === 0;
  $("audit-shop").textContent = data.shop;
  $("audit-count").textContent = String(audit.entries);
  $("audit-chain").textContent = audit.chain.ok
    ? "Intact"
    : `Broken at entry ${audit.chain.entry} (${audit.chain.kind})`;
  $("audit-root").textContent = audit.root;
  $("audit-facts").hidden = false;
}

async function run(file) {
  clear();
  setStatus("Checking...");
  try {
    const data = parseJournal(await file.text());
    const anchors = await journalAnchors(data.shop);
    const audit = await report(data.entries, anchors);
    render(data, audit);
    const last = audit.rows.at(-1);
    if (!audit.chain.ok) {
      setStatus(`✗ The file does not match its own hashes: entry ${audit.chain.entry} (${audit.chain.kind}) was changed.`, "bad");
    } else if (!audit.rows.length) {
      setStatus("The chain is intact, but nothing is anchored on Arc for this shop yet, so there is nothing to compare with.");
    } else if (audit.rows.every((r) => r.matches)) {
      setStatus(`✓ This file matches every anchor on Arc. The last, on ${when(last.anchoredAt)}, covers `
        + `${last.size} of its ${audit.entries} entries: nothing anchored was changed, removed or reordered.`, "ok");
    } else {
      setStatus("✗ This file does not match Arc. Something anchored was changed, removed or reordered.", "bad");
    }
  } catch (error) {
    setStatus(error.message && !error.code ? error.message : `Could not check the file: ${describeError(error)}`, "bad");
  }
}

$("audit-file").addEventListener("change", (event) => {
  const [file] = event.target.files;
  if (file) run(file);
});

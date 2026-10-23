// The audit page: a downloaded journal checked in the browser against the roots the agent wrote on
// Arc. No sign-in and no upload; the roots come from the JournalAnchor contract, never from the file.

import { arcValue, askArc, sameAsArc, VERIFIER_PAGE } from "./arc-check.js";
import { ARC_TESTNET, DECISION_VERIFIER_ADDRESS, JOURNAL_ANCHOR_ADDRESS } from "./config.js";
import { describeError } from "./errors.js";
import { parseJournal, report } from "./journal.js";
import { allDecisionProofs, checkDecisionProof, isDecisionProof } from "./proof.js";
import { checklist, decisionStory, headline } from "./report.js";
import { proofFromHash } from "./share.js";
import { journalAnchors, read } from "./shop.js";
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
  $("decision-report").hidden = true;
  $("decision-report").replaceChildren();
  $("audit-decisions-card").hidden = true;
  $("audit-decisions").replaceChildren();
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== ""));
  return node;
}

const explorer = (path, text) => el("a", { href: `${ARC_TESTNET.explorer}/${path}`, target: "_blank", rel: "noopener", textContent: text });

function section(title, ...children) {
  return el("section", { className: "report-part" }, el("h3", { textContent: title }), ...children);
}

/** What the agent read, as a short list of its fields; the rest folded. */
function readView(read) {
  if (read === null || typeof read !== "object") return el("pre", { textContent: String(read) });
  const rows = Object.entries(read).map(([k, v]) => el("div", {},
    el("dt", { textContent: k.replaceAll("_", " ") }),
    el("dd", { textContent: Array.isArray(v) && !v.length ? "none"
      : v !== null && typeof v === "object" ? JSON.stringify(v) : String(v ?? "-") })));
  return el("dl", { className: "report-read" }, ...rows);
}

/** The verdict on top: Arc's own answer when the decision has a MANIFEST, else what this browser
 * found, said as such. */
function verdictOf(story, result, arc) {
  if (arc) {
    if (!arc.verified) return el("p", { className: "verdict bad", textContent: `✗ Arc says: ${arc.answer}.` });
    if (!result.ok || !sameAsArc(story, arc)) {
      return el("p", { className: "verdict bad",
        textContent: "✗ Arc verified the decision, but the records shown here do not match it. Rely only on Arc's answer." });
    }
    return el("p", { className: "verdict ok", textContent: "✓ Arc says: anchored and unchanged" });
  }
  if (!result.ok && result.listed === false && !result.unlinked && result.proofs.every((p) => p.ok)) {
    return el("p", { className: "verdict", textContent: "Anchored on Arc, checked in this browser. It was written "
      + "before decisions listed their records, so Arc cannot check it on its own." });
  }
  return result.ok
    ? el("p", { className: "verdict ok", textContent: "✓ Anchored on Arc, checked in this browser" })
    : el("p", { className: "verdict bad", textContent: "✗ Does not match Arc. Do not rely on this record." });
}

/** How to ask Arc without this page: the value, the contract, three steps. */
function askArcYourself(value) {
  const copy = el("button", { className: "btn btn-ghost btn-small", type: "button", textContent: "Copy the proof" });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Copy it from the box below";
    }
  });
  return section("Check it yourself on Arc",
    el("ol", { className: "report-steps" },
      el("li", {}, copy),
      el("li", {}, "Open ", el("a", { href: VERIFIER_PAGE, target: "_blank", rel: "noopener",
        textContent: "the verifier contract on Arc's explorer" }), " (no wallet needed)."),
      el("li", { textContent: "Paste it in verify and press Read. Arc answers with the decision it checked." })),
    el("details", {}, el("summary", { textContent: "The proof" }),
      el("textarea", { className: "mono arc-proof", readOnly: true, rows: 4, value })));
}

/** One decision in plain words, Arc's answer on top, and how it was checked. */
function renderDecision(bundle, result, arc = null, value = null) {
  const story = decisionStory(bundle);
  const checks = checklist(result);
  const box = $("decision-report");
  const verdict = verdictOf(story, result, arc);
  const steps = story.steps.map((s) => {
    const args = Object.values(s.args).join(", ");
    const head = `${s.tool}${args ? ` (${args})` : ""}${s.ok ? "" : `: refused, ${s.refused}`}`;
    return s.read === null ? el("li", { textContent: head })
      : el("li", {}, el("details", {}, el("summary", { textContent: head }), readView(s.read)));
  });
  const pay = story.payment;
  const parts = [
    verdict,
    el("h2", { className: "report-title", textContent: headline(story) }),
    el("p", { className: "hint", textContent: [story.at ? when(story.at) : "", story.dueDate ? `due ${story.dueDate}` : "",
      story.model ? `decided by the AI model ${story.model}` : ""].filter(Boolean).join(" · ") }),
    section("Why", el("p", { textContent: story.reason || story.question || "-" })),
    story.checks.length ? section("What the checks said before it decided", el("ul", { className: "report-checks" },
      ...story.checks.map((c) => el("li", { className: c.passed ? "status-on" : "status-off",
        textContent: `${c.passed ? "✓" : "✗"} ${c.control}: ${c.why}` })))) : null,
    story.answers.length ? section("The owner's answer", ...story.answers.map((a) =>
      el("p", { textContent: `${a.verdict}${a.note ? `: ${a.note}` : ""} (${when(a.at)})` }))) : null,
    story.limits.length ? section("Limits it worked within", el("dl", { className: "facts" },
      ...story.limits.map((l) => el("div", {}, el("dt", { textContent: l.name }), el("dd", { textContent: l.value }))))) : null,
    pay ? section("The payment", el("dl", { className: "facts" },
      el("div", {}, el("dt", { textContent: "State" }), el("dd", { textContent: pay.status === "RECORDED" ? "Paid and recorded in the ERP" : pay.status })),
      pay.tx ? el("div", {}, el("dt", { textContent: "Transaction on Arc" }), el("dd", {}, explorer(`tx/${pay.tx}`, short(pay.tx)))) : null,
      pay.wallet ? el("div", {}, el("dt", { textContent: "Paid to wallet" }), el("dd", {}, explorer(`address/${pay.wallet}`, short(pay.wallet)))) : null,
      pay.erpEntry ? el("div", {}, el("dt", { textContent: "Payment entry in the ERP" }), el("dd", { textContent: pay.erpEntry })) : null)) : null,
    section("What it looked at, in order",
      story.woke.length ? el("p", { className: "hint", textContent: `Woken because: ${story.woke.join(" ")}` }) : null,
      el("ol", { className: "report-steps" }, ...steps),
      story.summary ? el("p", { textContent: `It told the owner: “${story.summary}”` }) : null),
    value ? askArcYourself(value) : null,
    section("How this was checked",
      arc ? el("p", {}, "Arc checked the decision: the ",
        explorer(`address/${DECISION_VERIFIER_ADDRESS}`, "verifier contract"),
        " hashed its list of records, followed it to the summary the agent wrote on Arc and returned the decision"
        + " above. It has no owner and nobody can change it.") : null,
      el("ul", { className: "report-checklist" }, ...checks.items.map((i) =>
        el("li", { className: i.ok ? "status-on" : "status-off", textContent: `${i.ok ? "✓" : "✗"} ${i.text}` }))),
      el("p", { className: "hint" }, "Your browser compared each record shown here with the summary the agent wrote on Arc",
        checks.anchoredAt ? ` on ${when(checks.anchoredAt)}` : "", " (",
        explorer(`address/${JOURNAL_ANCHOR_ADDRESS}`, "see it on Arc"),
        "). A record changed by even one letter would not match.")),
    el("div", { className: "row no-print" },
      el("button", { className: "btn btn-ghost btn-small", type: "button", textContent: "Print or save as PDF",
        onclick: () => window.print() })),
  ];
  box.replaceChildren(...parts.filter(Boolean));
  box.hidden = false;
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

/** One decision: checked in this browser, and asked to Arc when it has a MANIFEST. */
async function runDecision(bundle, anchors = null) {
  if (typeof bundle.shop !== "string") throw new Error("That proof does not say which shop it belongs to.");
  const onArc = anchors ?? await journalAnchors(bundle.shop);
  const result = await checkDecisionProof(bundle, onArc);
  const value = arcValue(bundle, onArc);
  const arc = value ? await read((provider) => askArc(value, provider)) : null;
  setStatus("");
  renderDecision(bundle, result, arc, value);
}

/** Every decision of a journal, in plain words, each checked; a click opens it. */
async function listDecisions(data, anchors) {
  const { proofs } = await allDecisionProofs(data, anchors);
  const rows = [];
  for (const bundle of proofs.reverse()) {
    const result = await checkDecisionProof(bundle, anchors);
    const story = decisionStory(bundle);
    const open = el("button", { className: "btn btn-ghost btn-small", type: "button", textContent: "Open" });
    open.addEventListener("click", async () => {
      try {
        await runDecision(bundle, anchors);
        $("decision-report").scrollIntoView({ behavior: "smooth" });
      } catch (error) {
        setStatus(`Could not check it: ${describeError(error)}`, "bad");
      }
    });
    const older = !result.listed && !result.unlinked && result.proofs.every((p) => p.ok);
    rows.push(el("tr", {}, cell(story.at ? when(story.at) : "-"), cell(story.invoice),
      cell(`${story.did} ${story.amount ? `${Number(story.amount)} USDC` : ""}`.trim()),
      cell(result.ok ? "✓ Checked" : older ? "Anchored, no list of records" : "✗ Does not match",
        result.ok ? "status-on" : older ? "" : "status-off"),
      el("td", {}, open)));
  }
  $("audit-decisions").replaceChildren(...rows);
  $("audit-decisions-card").hidden = rows.length === 0;
  return proofs.length;
}

async function run(file) {
  clear();
  setStatus("Checking...");
  try {
    const text = await file.text();
    let raw = null;
    try {
      raw = JSON.parse(text);
    } catch {
      // parseJournal says it is not JSON
    }
    if (isDecisionProof(raw)) {
      await runDecision(raw);
      return;
    }
    const data = parseJournal(text);
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
      await listDecisions(data, anchors);
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

/** A link from an owner: the proof is in the hash, and is checked as soon as the page opens. */
async function openLink() {
  try {
    const bundle = await proofFromHash(location.hash);
    if (!bundle) return;
    clear();
    $("audit-pick-card").hidden = true;  // a reader with a link has nothing to choose
    setStatus("Checking against Arc...");
    await runDecision(bundle);
  } catch (error) {
    setStatus(error.message && !error.code ? error.message : `Could not check it: ${describeError(error)}`, "bad");
  }
}

openLink();
window.addEventListener("hashchange", openLink);

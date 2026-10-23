// The audit page: a downloaded journal checked in the browser against the roots the agent wrote on
// Arc. No sign-in and no upload; the roots come from the JournalAnchor contract, never from the file.

import { arcValue, askArc, sameAsArc, VERIFIER_PAGE } from "./arc-check.js";
import { ARC_TESTNET, DECISION_VERIFIER_ADDRESS, JOURNAL_ANCHOR_ADDRESS } from "./config.js";
import { describeError } from "./errors.js";
import { parseJournal, report } from "./journal.js";
import { allDecisionProofs, checkDecisionProof, isDecisionProof } from "./proof.js";
import { checklist, decisionStory, headline, plain } from "./report.js";
import { proofFromHash } from "./share.js";
import { journalAnchors, read } from "./shop.js";
import "./theme.js";
import { qrcode } from "./vendor/qrcode-generator-2.0.4/qrcode.mjs";

const $ = (id) => document.getElementById(id);
const short = (hex) => `${hex.slice(0, 12)}...${hex.slice(-6)}`;
// with its time zone: a sheet on paper must say which clock it means
const when = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "long" });

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

/** The stamp on the sheet: Arc's own answer when the decision has a MANIFEST, else what this
 * browser found, said as such; and, when it fails, why. */
function stampOf(story, result, arc) {
  const stamp = (tone, text) => el("p", { className: `stamp ${tone}` }, text,
    el("small", { textContent: `checked ${when(new Date().toISOString())}` }));
  const alert = (text) => el("p", { className: "sheet-alert", textContent: text });
  if (arc) {
    if (!arc.verified) return [stamp("bad", "Does not match Arc"), alert(`Arc says: ${arc.answer}. Do not rely on this record.`)];
    if (!result.ok || !sameAsArc(story, arc)) {
      return [stamp("bad", "Does not match Arc"),
        alert("Arc verified this decision, but the records shown on this sheet do not match it. Rely only on Arc's answer.")];
    }
    return [stamp("ok", "Verified on Arc"), null];
  }
  if (!result.ok && result.listed === false && !result.unlinked && result.proofs.every((p) => p.ok)) {
    return [stamp("plain", "Checked in this browser"), el("p", { className: "hint", textContent: "Its records are "
      + "anchored on Arc, unchanged. It was written before decisions listed their records, so Arc cannot check it on its own." })];
  }
  return result.ok ? [stamp("plain", "Checked in this browser"), null]
    : [stamp("bad", "Does not match Arc"), alert("Do not rely on this record.")];
}

/** A QR code of `url` with its caption: scanned with a phone, it opens the page on Arc's explorer. */
function qr(url, caption) {
  const code = qrcode(0, "M");
  code.addData(url);
  code.make();
  const svg = document.createElement("template");
  // the quiet zone of 4 modules a QR code needs around it to be read (ISO/IEC 18004)
  svg.innerHTML = code.createSvgTag({ cellSize: 4, margin: 16, scalable: true, alt: caption });
  return el("figure", { className: "sheet-qr" }, svg.content.firstChild, el("figcaption", { textContent: caption }));
}

/** How to check the sheet without this page: on paper, two codes; on screen, the proof to copy. */
function verifyBlock(story, value) {
  const copy = el("button", { className: "btn btn-ghost btn-small no-print", type: "button", textContent: "Copy the proof" });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Copy it from the box below";
    }
  });
  const tx = story.payment?.tx;
  return el("section", { className: "sheet-verify" },
    el("div", {},
      el("h3", { className: "sheet-kind", textContent: "Verify this sheet yourself" }),
      tx ? el("p", { textContent: "The payment: scan the code, or look up its transaction on Arc's explorer." }) : null,
      // the proof is too long for paper: on paper, where to get it; on screen, the proof itself
      value ? el("p", { className: "print-only", textContent: "The decision: ask the shop owner for the link to this "
        + "record. It opens this sheet and asks Arc's verifier contract again." }) : null,
      value ? el("p", { className: "no-print" }, "The decision: copy the proof, open the ",
        el("a", { href: VERIFIER_PAGE, target: "_blank", rel: "noopener", textContent: "verifier contract on Arc's explorer" }),
        ", paste it in ", el("b", { textContent: "verify" }), " and press ", el("b", { textContent: "Read" }),
        ". No wallet is needed. Arc answers with the decision it checked.") : null,
      value ? el("div", { className: "row no-print" }, copy) : null,
      value ? el("details", { className: "no-print" }, el("summary", { textContent: "The proof" }),
        el("textarea", { className: "mono arc-proof", readOnly: true, rows: 4, value })) : null),
    tx ? qr(`${ARC_TESTNET.explorer}/tx/${tx}`, "The payment on Arc") : el("span"));
}

const fact = (term, ...value) => [el("dt", { textContent: term }), el("dd", {}, ...value)];
const CHOICE = { PAY_NOW: "Pay now", SCHEDULE: "Schedule the payment", HOLD: "Hold", ASK: "Ask the owner" };
const PAYMENT = { RECORDED: "Paid and recorded in the ERP", COMPLETE: "Paid", SUBMITTED: "Sent, not final yet",
  FAILED: "Failed", BLOCKED: "Not sent" };
const mono = (text) => el("span", { className: "mono", textContent: text });

/** One decision as a sheet: the same on screen and on paper, for an accountant's file. */
function renderDecision(bundle, result, arc = null, value = null) {
  const story = decisionStory(bundle);
  const checks = checklist(result);
  const box = $("decision-report");
  const [stamp, note] = stampOf(story, result, arc);
  const steps = story.steps.map((s) => {
    const args = Object.values(s.args).join(", ");
    const head = `${s.tool}${args ? ` (${args})` : ""}${s.ok ? "" : `: refused, ${s.refused}`}`;
    return s.read === null ? el("li", { textContent: head })
      : el("li", {}, el("details", {}, el("summary", { textContent: head }), readView(s.read)));
  });
  const pay = story.payment;
  const anchoredAt = arc?.anchoredAt ?? checks.anchoredAt;
  const facts = [
    fact("Invoice", story.invoice),
    story.supplier ? fact("Supplier", story.supplier) : [],
    story.amount ? fact("Amount", `${Number(story.amount)} USDC`) : [],
    story.dueDate ? fact("Due", story.dueDate) : [],
    fact("Decision", CHOICE[story.choice] ?? (plain(story.choice) || "-")),
    story.at ? fact("Decided", `${when(story.at)}${story.model ? ` by the AI model ${story.model}` : ""}`) : [],
    pay ? fact("Payment", PAYMENT[pay.status] ?? plain(pay.status)) : [],
    pay?.tx ? fact("Transaction on Arc", explorer(`tx/${pay.tx}`, pay.tx)) : [],
    pay?.wallet ? fact("Paid to wallet", mono(pay.wallet)) : [],
    pay?.erpEntry ? fact("Payment entry in the ERP", pay.erpEntry) : [],
    fact("Shop contract", explorer(`address/${bundle.shop}`, bundle.shop)),
    arc?.owner && /^0x0+$/.test(arc.owner) === false ? fact("Shop owner", mono(arc.owner)) : [],
    anchoredAt ? fact("Anchored on Arc", when(anchoredAt)) : [],
  ].flat();
  const parts = [
    el("header", { className: "sheet-head" },
      el("div", {},
        el("p", { className: "sheet-kind", textContent: "Embco · record of a payment decision by the AI agent" }),
        el("h2", { className: "report-title", textContent: headline(story) })),
      stamp),
    note,
    el("dl", { className: "sheet-facts" }, ...facts),
    section("Why", el("p", { textContent: story.reason || story.question || "-" })),
    story.checks.length ? section("What the checks said before it decided", el("ul", { className: "report-checks" },
      ...story.checks.map((c) => el("li", { className: c.passed ? "status-on" : "status-off",
        textContent: `${c.passed ? "✓" : "✗"} ${c.control}: ${c.why}` })))) : null,
    story.answers.length ? section("The owner's answer", ...story.answers.map((a) =>
      el("p", { textContent: `${a.verdict}${a.note ? `: ${a.note}` : ""} (${when(a.at)})` }))) : null,
    story.limits.length ? section("Limits it worked within", el("dl", { className: "sheet-facts" },
      ...story.limits.flatMap((l) => fact(l.name, l.value)))) : null,
    el("section", { className: "report-part no-print" }, el("h3", { textContent: "What it looked at, in order" }),
      story.woke.length ? el("p", { className: "hint", textContent: `Woken because: ${story.woke.join(" ")}` }) : null,
      el("ol", { className: "report-steps" }, ...steps),
      story.summary ? el("p", { textContent: `It told the owner: “${story.summary}”` }) : null),
    section("How this was checked",
      arc ? el("p", {}, "Arc checked the decision: the ",
        explorer(`address/${DECISION_VERIFIER_ADDRESS}`, "verifier contract"),
        " hashed its list of records, followed it to the summary the agent wrote on Arc and returned the decision"
        + " above. It has no owner and nobody can change it.") : null,
      el("ul", { className: "report-checklist" }, ...checks.items.map((i) =>
        el("li", { className: i.ok ? "status-on" : "status-off", textContent: `${i.ok ? "✓" : "✗"} ${i.text}` }))),
      el("p", { className: "hint" }, "Each record was compared with the summary the agent wrote on Arc (",
        explorer(`address/${JOURNAL_ANCHOR_ADDRESS}`, "JournalAnchor"),
        "). A record changed by even one letter would not match.")),
    verifyBlock(story, value),
    el("p", { className: "sheet-foot", textContent: "Arc Testnet: a test network, where amounts have no real value. "
      + "What the agent read before deciding is shown on screen, not on paper." }),
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

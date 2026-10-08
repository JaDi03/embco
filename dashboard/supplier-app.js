// The supplier page: sign in with the wallet you are paid to, see each shop's invoices and
// payments, and confirm that wallet when a shop's agent asks for it.

import { ARC_TESTNET } from "./config.js";
import { describeError } from "./errors.js";
import * as suppliers from "./suppliers.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
const txUrl = (hash) => `${ARC_TESTNET.explorer}/tx/${hash}`;

function notify(text, kind = "info") {
  const notice = $("notice");
  notice.textContent = text;
  notice.className = kind === "info" ? "notice" : `notice ${kind}`;
  notice.hidden = !text;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function show(view) {
  for (const id of ["view-welcome", "view-page"]) $(id).hidden = id !== view;
  $("sign-out").hidden = view !== "view-page";
}

/** On a phone without a wallet, the MetaMask app opens this same page in its own browser. */
function metamaskAppLink() {
  const { host, pathname } = window.location;
  return `https://metamask.app.link/dapp/${host}${pathname}`;
}

async function signer() {
  const provider = await wallet.connect();
  if (!(await wallet.onArc(provider))) await wallet.switchToArc();
  return (await wallet.connect()).getSigner();
}

async function render() {
  const page = await suppliers.myPage();
  if (!page) {
    show("view-welcome");
    const hasWallet = wallet.hasWallet();
    $("sign-in").hidden = !hasWallet;
    $("no-wallet").hidden = hasWallet;
    $("open-in-metamask").href = metamaskAppLink();
    return;
  }
  show("view-page");
  $("wallet").textContent = page.wallet;
  $("no-shops").hidden = page.shops.length > 0;
  $("shops").replaceChildren(...page.shops.map(shopCard));
}

function shopCard(entry) {
  const card = el("article", { className: "card wide" });
  card.append(el("h2", { textContent: entry.company || "A shop" }));
  card.append(el("p", { className: "hint", textContent: `You are ${entry.supplier} for this shop.` }));
  if (entry.challenge) card.append(confirmBox(entry));
  const note = suppliers.signatureNote(entry);
  if (note) card.append(el("p", { className: `notice ${note.kind}`, textContent: note.text }));

  card.append(el("h3", { textContent: "Open invoices" }));
  const invoices = entry.invoices.map((i) => {
    const status = suppliers.invoiceStatus(i.status);
    const badge = el("span", { className: `badge badge-${status.kind}`, textContent: status.label });
    const due = i.due_date ? ` · due ${i.due_date}` : "";
    return el("li", {}, el("span", {}, `${i.invoice}${due}`), el("span", {}, badge,
      el("span", { className: "amount", textContent: `${suppliers.formatAmount(i.amount)} USDC` })));
  });
  card.append(el("ul", { className: "list" }, ...(invoices.length ? invoices
    : [el("li", { className: "empty", textContent: "No open invoices." })])));

  card.append(el("h3", { textContent: "Payments" }));
  const payments = entry.payments.map((p) => el("li", {},
    el("span", {}, `${p.invoice} · ${new Date(p.paid_at).toLocaleDateString()}`),
    el("span", {}, el("span", { className: "amount", textContent: `${suppliers.formatAmount(p.amount)} USDC ` }),
      el("a", { href: txUrl(p.tx_hash), target: "_blank", rel: "noopener", textContent: "See on the explorer" }))));
  card.append(el("ul", { className: "list" }, ...(payments.length ? payments
    : [el("li", { className: "empty", textContent: "No payments yet." })])));
  if (entry.network) card.append(el("p", { className: "hint", textContent: `Payments on ${entry.network}.` }));
  return card;
}

function confirmBox(entry) {
  const box = el("div", { className: "confirm" });
  const expires = new Date(entry.challenge.expires_at).toLocaleDateString();
  box.append(el("p", {},
    `${entry.company} asks you to confirm the wallet you are paid to: `,
    el("b", { className: "mono", textContent: entry.challenge.wallet }), `. Before ${expires}.`));
  if (entry.signature_received) {
    box.append(el("p", { className: "hint", textContent:
      "Your signature was received. The shop's agent records it within about 15 minutes." }));
    return box;
  }
  box.append(el("p", { className: "hint", textContent:
    "Sign only if this wallet is yours. It is a signature, not a payment: it moves no money." }));
  const button = el("button", { className: "btn btn-primary", type: "button", textContent: "Confirm my wallet" });
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      notify("Confirm the signature in MetaMask...");
      await suppliers.confirmWallet(await signer(), entry);
      notify("Received. The shop's agent records it within about 15 minutes.", "success");
      await render();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });
  box.append(button);
  return box;
}

function wire() {
  $("sign-in").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      notify("Sign in: confirm the signature in MetaMask (it is free)...");
      await suppliers.signInSupplier(await signer());
      notify("");
      await render();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });
  $("sign-out").addEventListener("click", async () => {
    await suppliers.signOutSupplier().catch(() => null);
    await render();
  });
}

function start() {
  $("explorer-link").href = ARC_TESTNET.explorer;
  wire();
  wallet.onWalletChange(() => suppliers.signOutSupplier().catch(() => null).then(render));
  render().catch((error) => notify(describeError(error), "error"));
}

start();

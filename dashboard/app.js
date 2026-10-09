// The owner's dashboard: connect MetaMask, create the shop contract, and run it.
// Every change is a transaction the owner signs in MetaMask; this page holds no keys.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { ARC_TESTNET, FAUCET_URL } from "./config.js";
import { describeError } from "./errors.js";
import { pendingApprovals } from "./approvals.js";
import * as activity from "./activity.js";
import * as chat from "./chat.js";
import * as hub from "./hub.js";
import { groupTasks, raisedLimits, taskFor } from "./needs.js";
import * as suppliers from "./suppliers.js";
import { factory, read, shopContract, shopHistory, shopsOf, shopState, usdc } from "./shop.js";
import { formatUsdc, parseUsdc, shortAddress, ZERO_ADDRESS } from "./units.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
const VIEWS = ["view-welcome", "view-network", "view-create", "view-shop"];

let session = { provider: null, account: null, shop: null };

function show(view) {
  for (const id of VIEWS) $(id).hidden = id !== view;
}

function notify(text, kind = "info") {
  const notice = $("notice");
  notice.textContent = text;
  notice.className = kind === "info" ? "notice" : `notice ${kind}`;
  notice.hidden = !text;
}

const addressUrl = (address) => `${ARC_TESTNET.explorer}/address/${address}`;
const txUrl = (hash) => `${ARC_TESTNET.explorer}/tx/${hash}`;

function link(text, href, className = "") {
  const a = document.createElement("a");
  a.textContent = text;
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  if (className) a.className = className;
  return a;
}

function setAccount(account) {
  const button = $("account-button");
  button.hidden = !account;
  button.textContent = account ? shortAddress(account) : "";
  button.title = account ?? "";
}

function readAddress(text, { allowEmpty = false } = {}) {
  const value = String(text ?? "").trim();
  if (!value && allowEmpty) return ZERO_ADDRESS;
  if (!ethers.isAddress(value)) throw new Error("That is not a valid wallet address (0x followed by 40 characters).");
  return ethers.getAddress(value);
}

// Rendering

async function render() {
  notify("");
  if (!wallet.hasWallet()) {
    show("view-welcome");
    $("connect-button").hidden = true;
    $("no-wallet").hidden = false;
    return;
  }
  const account = await wallet.currentAccount();
  setAccount(account);
  if (!account) {
    show("view-welcome");
    return;
  }
  const provider = new ethers.BrowserProvider(window.ethereum);
  session = { provider, account, shop: null };
  if (!(await wallet.onArc(provider))) {
    $("network-pill").hidden = true;
    show("view-network");
    return;
  }
  $("network-pill").hidden = false;
  notify("Looking for your shop contract...");
  const shops = await shopsOf(account);
  notify("");
  if (shops.length === 0) {
    show("view-create");
    return;
  }
  session.shop = shops[shops.length - 1];
  show("view-shop");
  await renderShop();
}

async function renderShop() {
  const { account, shop } = session;
  const state = await shopState(shop, account);
  const isOwner = state.owner.toLowerCase() === account.toLowerCase();

  const shopLink = $("shop-link");
  shopLink.textContent = shop;
  shopLink.href = addressUrl(shop);
  $("not-owner").hidden = isOwner;
  for (const el of document.querySelectorAll(".owner-only")) el.hidden = !isOwner;

  $("balance").textContent = `${formatUsdc(state.balance)} USDC`;
  $("allowance").textContent = `${formatUsdc(state.allowance)} USDC`;
  $("max-per-payment").textContent = `${formatUsdc(state.maxPerPayment)} USDC`;
  $("weekly-cap").textContent = `${formatUsdc(state.weeklyCap)} USDC`;
  $("remaining").textContent = `${formatUsdc(state.remaining)} USDC`;

  const hasAgent = state.agent !== ZERO_ADDRESS;
  const service = isOwner ? await hub.status(session.shop).catch(() => null) : null;
  const view = hub.agentSwitchView(service?.connected ? service.agent_on : null, state.paused);
  session.agentPaused = state.paused;
  const status = $("agent-status");
  status.textContent = !hasAgent ? "No agent yet" : view.status;
  status.className = hasAgent && view.on ? "status-on" : "status-off";
  $("agent-note").textContent = hasAgent ? view.note : "";
  $("agent-address").replaceChildren(hasAgent ? link(shortAddress(state.agent), addressUrl(state.agent)) : "none");
  const telegram = Boolean(service?.telegram_linked);
  $("telegram-state").textContent = !service ? "sign in to the embco service first" : telegram ? "linked: /stop and /pause work from your phone" : "not linked";
  $("telegram-link").hidden = !isOwner || !service?.connected || telegram;
  $("telegram-unlink").hidden = !isOwner || !telegram;
  if (telegram) $("telegram-code").hidden = true;
  $("agent-off").hidden = !isOwner || !view.on;
  $("agent-on").hidden = !isOwner || view.on;

  await Promise.all([renderHistory(isOwner), renderApprovals(isOwner), renderErp(isOwner, state)]);
}

// The shop's own agent on the owner's ERPNext, through the hub API.

function showErp(part) {
  for (const id of ["erp-signin", "erp-form", "erp-connected"]) $(id).hidden = id !== part;
}

function erpError(text) {
  $("erp-error").textContent = text;
  $("erp-error").hidden = !text;
}

function renderChecks(probe) {
  const list = $("erp-checks");
  list.hidden = !probe;
  if (!probe) return;
  list.replaceChildren(
    ...probe.checks.map((check) =>
      Object.assign(document.createElement("li"), {
        className: check.ok ? "ok" : "fail",
        textContent: check.detail,
      }),
    ),
  );
}

async function renderErp(isOwner, state) {
  $("suppliers-card").hidden = true;
  $("tasks-card").hidden = true;
  $("terminal-card").hidden = true;
  if (!isOwner) return;
  erpError("");
  let shop;
  try {
    shop = await hub.status(session.shop);
  } catch (error) {
    showErp(null);
    erpError(`The embco service is not reachable right now (${error.message}).`);
    return;
  }
  if (!shop) {
    renderChecks(null);
    showErp("erp-signin");
    return;
  }
  if (!shop.connected) {
    showErp("erp-form");
    return;
  }
  renderChecks(null);
  showErp("erp-connected");
  $("erp-url").textContent = shop.erp_url;
  $("erp-company").textContent = shop.company;
  $("erp-agent-wallet").replaceChildren(
    shop.agent_wallet ? link(shortAddress(shop.agent_wallet), addressUrl(shop.agent_wallet)) : "not created",
  );
  $("erp-set-agent").hidden = !hub.needsSetAgent(shop.agent_wallet, state.agent);
  const view = hub.lastRunView(shop.last_run);
  $("erp-last-at").textContent = view.at ? new Date(view.at).toLocaleString() : "not yet";
  $("erp-summary").textContent = view.text;
  $("erp-summary").className = view.state === "error" ? "status-off" : "";
  let list = null;
  try {
    list = await suppliers.listSuppliers(session.shop);
  } catch {
    list = null;  // the supplier card stays hidden; the tasks still show what the agent decided
  }
  await renderTasks(shop, state, list);
  renderSuppliers(shop, list);
  startTerminal();
}

// The agent's activity, live: polled every few seconds while the page is visible.

const terminal = { shop: null, events: [], interval: null, timer: null, ticker: null, loaded: false, chat: [], waiting: false };

function startTerminal() {
  $("terminal-card").hidden = false;
  if (terminal.shop === session.shop && terminal.timer) return;  // already running for this shop
  stopTerminal();
  Object.assign(terminal, { shop: session.shop, events: [], interval: null, loaded: false, chat: [], waiting: false });
  pollTerminal();
  terminal.timer = setInterval(pollTerminal, 4000);
  terminal.ticker = setInterval(paintNextCheck, 1000);
}

function stopTerminal() {
  clearInterval(terminal.timer);
  clearInterval(terminal.ticker);
  terminal.timer = terminal.ticker = null;
}

async function pollTerminal() {
  if (document.visibilityState === "hidden" || !terminal.shop) return;
  const after = terminal.events.at(-1)?.seq ?? 0;
  let body;
  try {
    body = await activity.fetchActivity(terminal.shop, after);
  } catch (error) {
    if (error.status === 401) stopTerminal();  // signed out: the ERP card asks to sign in again
    return;
  }
  terminal.interval = body.interval_minutes ?? terminal.interval;
  const fresh = body.events ?? [];
  const finished = terminal.loaded && fresh.some((e) => e.kind === "done");
  terminal.events = activity.merge(terminal.events, fresh);
  if (fresh.length || !terminal.loaded) paintTerminal(fresh.length > 0);
  terminal.loaded = true;
  paintNextCheck();
  await pollChat();
  if (finished) renderShop().catch(() => null);  // the agent finished a check: refresh the cards
}

function paintTerminal(scroll) {
  const box = $("terminal");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  if (!terminal.events.length) {
    box.replaceChildren(el("div", { className: "empty", textContent: "Waiting for the agent's first check..." }));
    return;
  }
  box.replaceChildren(...terminal.events.map((e) => {
    const text = el("span", { textContent: e.text });
    if (e.tx) text.append(" ", link("tx", txUrl(e.tx)));
    return el("div", { className: "line" },
      el("span", { className: "time", textContent: activity.timeOf(e.at) }),
      el("span", { className: `tag tag-${e.kind}`, textContent: activity.labelOf(e.kind) }),
      text);
  }));
  if (scroll && (atBottom || !terminal.loaded)) box.scrollTop = box.scrollHeight;
}

async function pollChat() {
  const after = terminal.chat.at(-1)?.seq ?? 0;
  let body;
  try {
    body = await chat.fetchChat(terminal.shop, after);
  } catch {
    return;
  }
  const fresh = body.entries ?? [];
  terminal.chat = activity.merge(terminal.chat, fresh, 200);
  terminal.waiting = Boolean(body.waiting);
  if (fresh.length || !$("chat-log").childElementCount) paintChat();
  $("chat-status").textContent = terminal.waiting ? "The agent is reading your message..." : "";
}

function paintChat() {
  const box = $("chat-log");
  if (!terminal.chat.length) {
    box.replaceChildren(el("div", { className: "empty", textContent: "Ask what is due, why something is on hold, or tell the agent what to do." }));
    return;
  }
  box.replaceChildren(...terminal.chat.map((entry) => {
    const b = chat.bubbleOf(entry);
    const bubble = el("div", { className: `bubble ${b.side}`, textContent: b.text });
    if (b.meta) bubble.append(el("span", { className: "meta", textContent: b.meta }));
    return bubble;
  }));
  box.scrollTop = box.scrollHeight;
}

function paintNextCheck() {
  const next = activity.nextCheck(terminal.events, terminal.interval);
  $("live-dot").className = next.state === "checking" ? "dot on" : "dot";
  $("next-check").textContent = next.state === "checking"
    ? "Checking now..."
    : next.state === "waiting"
      ? next.seconds > 0 ? `Next check in ${activity.countdown(next.seconds)}` : "Next check any moment"
      : "";
}

// What the agent needs from the owner: one checklist per invoice, each step with its button.

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function renderTasks(shop, state, list) {
  const lastRun = shop.last_run;
  if (!lastRun?.ok) return;
  const wallets = new Map((list?.suppliers ?? []).map((s) => [s.supplier, s.wallet]));
  const distinct = [...new Set([...wallets.values()].filter(Boolean))];
  const approved = new Map();
  try {
    await read(async (provider) => {
      const contract = shopContract(session.shop, provider);
      await Promise.all(distinct.map(async (w) => approved.set(w, await contract.approvedPayee(w))));
    });
  } catch {
    // unknown approvals show as "to do"; approving twice does no harm
  }
  const waiting = new Set(shop.answers_waiting ?? []);
  const tasks = (lastRun.decisions ?? []).map((d) => {
    const wallet = wallets.get(d.supplier) ?? null;
    return taskFor(d, {
      wallet, approved: wallet ? approved.get(wallet) === true : false,
      maxPerPayment: state.maxPerPayment, weeklyCap: state.weeklyCap,
    }, { waiting: waiting.has(d.invoice), answer: lastRun.answers?.[d.invoice] ?? null });
  });
  $("tasks-card").hidden = false;
  $("tasks-checked").textContent = `Last check ${new Date(lastRun.at).toLocaleTimeString()}`;
  const needs = tasks.filter((t) => t.section === "needs").length;
  $("tasks-summary").textContent = !tasks.length
    ? "No unpaid invoices. The agent checks your ERPNext every 15 minutes."
    : needs
      ? `${needs} invoice${needs > 1 ? "s need" : " needs"} you. Follow the steps in order; each button is right next to its step.`
      : "Nothing needs you right now.";
  const context = { company: shop.company, site: list?.site, state };
  $("tasks").replaceChildren(...groupTasks(tasks).map((group) => el("section", { className: "task-section" },
    el("h3", { textContent: `${group.title} (${group.tasks.length})` }),
    el("p", { className: "hint", textContent: group.hint }),
    ...group.tasks.map((task) => taskCard(task, context)))));
}

const MARKS = { done: "✓", todo: "✗", waiting: "…" };

function taskCard(task, context) {
  const d = task.decision;
  const amount = `${suppliers.formatAmount(d.amount)} USDC`;
  const title = task.section === "needs" ? `Pay ${amount} to ${d.supplier}?` : `${amount} to ${d.supplier}`;
  const due = d.due_date ? ` · due ${d.due_date}` : "";
  const card = el("div", { className: `task ${task.section}` },
    el("div", { className: "task-head" }, el("span", { textContent: title }),
      el("span", { className: "hint mono", textContent: `${d.invoice}${due}` })));
  if (task.payment) {
    const pay = el("p", { className: `pay-state ${task.payment.state}`, textContent: task.payment.text });
    if (task.payment.tx) pay.append(" ", link("See on the explorer", txUrl(task.payment.tx)));
    card.append(pay);
  }
  if (task.section !== "paying") {
    card.append(el("ol", { className: "steps" }, ...task.steps.map((step) => {
      const li = el("li", { className: `step step-${step.state}` },
        el("span", { className: "mark", textContent: MARKS[step.state] ?? "-" }),
        el("span", { className: "text", textContent: step.text }));
      const actions = stepActions(step, d, context);
      if (actions) li.append(actions);
      return li;
    })));
  }
  if (task.decide) card.append(decideBox(task));
  return card;
}

function stepActions(step, decision, context) {
  const action = step.action;
  if (!action || step.state === "done") return null;
  const row = el("div", { className: "row" });
  if (action.type === "approve_wallet") {
    const button = el("button", { className: "btn btn-primary", type: "button", textContent: "Approve this wallet" });
    button.addEventListener("click", () => act(button, "Approve wallet", (c) => c.setPayee(action.wallet, true))
      .then((ok) => ok && checkAndWait()));
    row.append(button);
  } else if (action.type === "raise_limit") {
    const limits = raisedLimits(action.amount, context.state.weeklyCap);
    const button = el("button", { className: "btn btn-primary", type: "button",
      textContent: `Raise the limit to ${formatUsdc(limits.maxPerPayment)} USDC` });
    button.addEventListener("click", () => act(button, "Raise the per-payment limit",
      (c) => c.setLimits(limits.maxPerPayment, limits.weeklyCap)).then((ok) => ok && checkAndWait()));
    row.append(button, el("span", { className: "hint", textContent: ` Now ${formatUsdc(context.state.maxPerPayment)} USDC.` }));
  } else if (action.type === "check") {
    row.append(checkButton("Check now"));
  } else if (action.type === "notice") {
    const email = el("button", { className: "btn btn-ghost", type: "button", textContent: "Email the supplier" });
    email.addEventListener("click", async () => {
      email.disabled = true;
      try {
        await suppliers.emailNotice(session.shop, decision.supplier);
        notify(`Notice sent to ${decision.supplier} from your ERPNext.`, "success");
      } catch (error) {
        notify(describeError(error), "error");
      } finally {
        email.disabled = false;
      }
    });
    const text = suppliers.noticeText({ company: context.company, supplier: decision.supplier,
      wallet: null, signatureNeeded: true, site: context.site });
    row.append(email, link("WhatsApp", suppliers.whatsappUrl(text), "btn btn-ghost"));
  } else {
    return null;
  }
  return row;
}

function decideBox(task) {
  const { decide, decision } = task;
  const box = el("div", { className: "decide" }, el("p", { textContent: decide.text }));
  if (decide.refused) {
    box.append(el("p", { className: "status-off", textContent: `Your last answer was not used: ${decide.refused}.` }));
  }
  if (decide.state === "sent") {
    box.append(el("p", { className: "hint", textContent: "Answer sent. The agent records it in its check." }),
      el("div", { className: "row" }, checkButton("Check now")));
    return box;
  }
  const note = el("input", { placeholder: "Note (optional), for example who you called", maxLength: 500 });
  const approve = el("button", { className: "btn btn-primary", type: "button", textContent: "Approve payment" });
  const reject = el("button", { className: "btn btn-danger", type: "button", textContent: "Reject" });
  const locked = decide.state === "locked";
  for (const b of [approve, reject]) b.disabled = locked;
  note.disabled = locked;
  const send = (verdict, button) => async () => {
    button.disabled = true;
    try {
      await hub.answer(session.shop, { invoice: decision.invoice, verdict, fingerprint: decide.fingerprint, note: note.value });
      notify(verdict === "APPROVE" ? "Approved. Asking the agent to check now..." : "Rejected. Asking the agent to check now...", "success");
      await checkAndWait();
    } catch (error) {
      notify(describeError(error), "error");
      button.disabled = false;
    }
  };
  approve.addEventListener("click", send("APPROVE", approve));
  reject.addEventListener("click", send("REJECT", reject));
  box.append(note, el("div", { className: "row" }, approve, reject));
  if (locked) box.append(el("p", { className: "hint", textContent: "Finish the steps above first." }));
  return box;
}

function checkButton(label) {
  const button = el("button", { className: "btn btn-ghost", type: "button", textContent: label });
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await checkAndWait();
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

/** Ask the agent to check now, then refresh the page once its check is done (about a minute at most). */
async function checkAndWait() {
  const before = (await hub.status(session.shop).catch(() => null))?.last_run?.at;
  try {
    await hub.checkNow(session.shop);
  } catch (error) {
    if (error.status !== 429) {
      notify(describeError(error), "error");
      return;
    }
  }
  notify("The agent is checking your ERPNext...");
  for (let i = 0; i < 12; i++) {
    await new Promise((resolve) => setTimeout(resolve, 5000));
    const now = await hub.status(session.shop).catch(() => null);
    if (now?.last_run?.at && now.last_run.at !== before) break;
  }
  notify("");
  await renderShop();
}

function renderSuppliers(shop, list) {
  if (!list) return;  // the card stays hidden; the ERP card already reports a service problem
  $("suppliers-card").hidden = false;
  $("supplier-site").textContent = list.site;
  if (!list.suppliers.length) {
    $("supplier-list").replaceChildren(Object.assign(document.createElement("li"), {
      className: "empty", textContent: "The agent has not seen any supplier yet.",
    }));
    return;
  }
  $("supplier-list").replaceChildren(...list.suppliers.map((row) => supplierRow(shop, list.site, row)));
}

function supplierRow(shop, site, row) {
  const li = document.createElement("li");
  const what = Object.assign(document.createElement("div"), { className: "supplier-row" });
  const title = document.createElement("span");
  if (row.signature_needed) {
    title.append(Object.assign(document.createElement("span"), { className: "badge badge-ask", textContent: "SIGN" }));
  }
  title.append(row.supplier);
  const detail = Object.assign(document.createElement("span"), {
    className: "reasons",
    textContent: `${row.wallet ? shortAddress(row.wallet) : "no wallet on file"} · ${row.open_invoices} open · ${row.payments} paid`
      + (row.signature_needed ? " · waiting for the supplier to confirm its wallet" : ""),
  });
  what.append(title, detail);

  const actions = Object.assign(document.createElement("div"), { className: "row" });
  const email = Object.assign(document.createElement("button"), {
    className: "btn btn-ghost", type: "button", textContent: "Email notice",
  });
  email.addEventListener("click", async () => {
    email.disabled = true;
    try {
      await suppliers.emailNotice(session.shop, row.supplier);
      notify(`Notice sent to ${row.supplier} from your ERPNext.`, "success");
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      email.disabled = false;
    }
  });
  const text = suppliers.noticeText({
    company: shop.company, supplier: row.supplier, wallet: row.wallet, signatureNeeded: row.signature_needed, site,
  });
  actions.append(email, link("WhatsApp", suppliers.whatsappUrl(text), "btn btn-ghost"));
  li.append(what, actions);
  return li;
}

async function renderApprovals(isOwner) {
  const card = $("approvals-card");
  card.hidden = true;
  if (!isOwner) return;
  let pending;
  try {
    const isApproved = (address) => read((provider) => shopContract(session.shop, provider).approvedPayee(address));
    pending = await pendingApprovals(session.shop, isApproved);
  } catch (error) {
    notify(describeError(error), "error");
    return;
  }
  if (!pending.length) return;
  card.hidden = false;
  $("approvals").replaceChildren(
    ...pending.map(({ wallet: address, invoices }) => {
      const li = document.createElement("li");
      const total = invoices.reduce((sum, i) => sum + i.amount, 0n);
      const detail = document.createElement("span");
      detail.append(link(address, addressUrl(address), "mono"));
      detail.append(` ${formatUsdc(total)} USDC for ${invoices.map((i) => i.invoice).join(", ")}`);
      const approve = Object.assign(document.createElement("button"), {
        className: "btn btn-primary",
        type: "button",
        textContent: "Approve",
      });
      approve.addEventListener("click", () =>
        act(approve, "Approve supplier", (shop) => shop.setPayee(address, true)),
      );
      li.append(detail, approve);
      return li;
    }),
  );
}

async function renderHistory(isOwner) {
  const suppliers = $("suppliers");
  const payments = $("payments");
  const loading = () => Object.assign(document.createElement("li"), { className: "empty", textContent: "Loading..." });
  suppliers.replaceChildren(loading());
  payments.replaceChildren(loading());

  let history;
  try {
    history = await shopHistory(session.shop);
  } catch (error) {
    const unavailable = () =>
      Object.assign(document.createElement("li"), { className: "empty", textContent: describeError(error) });
    suppliers.replaceChildren(unavailable());
    payments.replaceChildren(unavailable());
    return;
  }

  suppliers.replaceChildren(
    ...(history.suppliers.length
      ? history.suppliers.map((address) => {
          const li = document.createElement("li");
          li.append(link(address, addressUrl(address), "mono"));
          if (isOwner) {
            const remove = Object.assign(document.createElement("button"), {
              className: "btn btn-danger",
              type: "button",
              textContent: "Remove",
            });
            remove.addEventListener("click", () =>
              act(remove, "Remove supplier", async (shop) => shop.setPayee(address, false)),
            );
            li.append(remove);
          }
          return li;
        })
      : [Object.assign(document.createElement("li"), { className: "empty", textContent: "No approved suppliers yet." })]),
  );

  payments.replaceChildren(
    ...(history.payments.length
      ? history.payments.map((p) => {
          const li = document.createElement("li");
          const who = p.by.toLowerCase() === session.account.toLowerCase() ? "you" : "the agent";
          const amount = Object.assign(document.createElement("span"), {
            className: "amount",
            textContent: `${formatUsdc(p.amount)} USDC`,
          });
          const detail = document.createElement("span");
          detail.append(`to ${shortAddress(p.payee)}, paid by ${who}, invoice ${p.invoiceRef.slice(0, 10)}... `);
          detail.append(link("view", txUrl(p.tx)));
          li.append(amount, detail);
          return li;
        })
      : [Object.assign(document.createElement("li"), { className: "empty", textContent: "No payments yet." })]),
  );
}

// Actions: each one is a transaction signed by the owner in MetaMask.

async function act(button, label, send) {
  button.disabled = true;
  try {
    notify(`${label}: confirm it in MetaMask...`);
    const signer = await session.provider.getSigner();
    const tx = await send(shopContract(session.shop, signer), signer);
    notify(`${label}: waiting for Arc to confirm...`);
    await tx.wait();
    notify(`${label}: done.`, "success");
    await renderShop();
    return true;
  } catch (error) {
    notify(describeError(error), "error");
    return false;
  } finally {
    button.disabled = false;
  }
}

/**
 * Off: the service stops the agent's process first (no checks, no Claude), then the owner pauses
 * the contract so no payment can leave either. On: the contract first, then the process.
 */
async function switchAgent(button, on) {
  button.disabled = true;
  try {
    if (!on) {
      let stopped = true;
      try {
        await hub.setAgent(session.shop, false);
      } catch (error) {
        stopped = false;
        notify(`The agent's process was not stopped: ${describeError(error)}`, "error");
      }
      if (!session.agentPaused) {
        const paused = await act(button, "Pause payments in the contract", (shop) => shop.pause());
        if (!paused && stopped) notify("The agent is off. Payments are still allowed in the contract.", "error");
        if (paused && stopped) notify("The agent is off: no checks, no Claude, no payments.", "success");
      } else if (stopped) {
        notify("The agent is off: no checks, no Claude, no payments.", "success");
      }
      return;
    }
    if (session.agentPaused) {
      const resumed = await act(button, "Allow payments in the contract", (shop) => shop.unpause());
      if (!resumed) return;
    }
    try {
      await hub.setAgent(session.shop, true);
      notify("The agent is on. It checks your ERPNext now.", "success");
    } catch (error) {
      notify(`Payments are allowed, but the agent did not start: ${describeError(error)}`, "error");
    }
  } finally {
    button.disabled = false;
    await renderShop();
  }
}

function onSubmit(formId, handler) {
  const form = $(formId);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    handler(new FormData(form), button, form);
  });
}

function wireActions() {
  $("connect-button").addEventListener("click", async () => {
    try {
      await wallet.connect();
      await render();
    } catch (error) {
      notify(describeError(error), "error");
    }
  });

  $("switch-button").addEventListener("click", async () => {
    try {
      await wallet.switchToArc();
      await render();
    } catch (error) {
      notify(describeError(error), "error");
    }
  });

  onSubmit("create-form", async (data, button) => {
    button.disabled = true;
    try {
      const maxPerPayment = parseUsdc(data.get("maxPerPayment"));
      const weeklyCap = parseUsdc(data.get("weeklyCap"));
      if (maxPerPayment > weeklyCap) throw new Error("The per-payment limit cannot be above the weekly limit.");
      const agent = readAddress(data.get("agent"), { allowEmpty: true });
      if (agent.toLowerCase() === session.account.toLowerCase()) {
        throw new Error("The agent must be a different wallet from yours.");
      }
      notify("Create your shop contract: confirm it in MetaMask...");
      const signer = await session.provider.getSigner();
      const tx = await factory(signer).create(agent, maxPerPayment, weeklyCap);
      notify("Creating your shop contract on Arc...");
      await tx.wait();
      await render();
      notify("Your shop contract is ready. Next: let it use some USDC and approve your suppliers.", "success");
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

  onSubmit("allowance-form", (data, button, form) => {
    let amount;
    try {
      amount = parseUsdc(data.get("amount"));
    } catch (error) {
      notify(describeError(error), "error");
      return;
    }
    act(button, "Set the amount the agent may use", (_shop, signer) => usdc(signer).approve(session.shop, amount)).then(
      (ok) => ok && form.reset(),
    );
  });

  $("revoke-button").addEventListener("click", (event) =>
    act(event.currentTarget, "Revoke", (_shop, signer) => usdc(signer).approve(session.shop, 0n)),
  );

  onSubmit("limits-form", (data, button, form) => {
    let maxPerPayment;
    let weeklyCap;
    try {
      maxPerPayment = parseUsdc(data.get("maxPerPayment"));
      weeklyCap = parseUsdc(data.get("weeklyCap"));
      if (maxPerPayment > weeklyCap) throw new Error("The per-payment limit cannot be above the weekly limit.");
    } catch (error) {
      notify(describeError(error), "error");
      return;
    }
    act(button, "Change limits", (shop) => shop.setLimits(maxPerPayment, weeklyCap)).then((ok) => ok && form.reset());
  });

  $("chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("chat-input");
    const checked = chat.checkMessage(input.value);
    if (checked.error) {
      $("chat-status").textContent = checked.error;
      return;
    }
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await chat.sendMessage(session.shop, checked.text);
      input.value = "";
      terminal.chat = [...terminal.chat, { seq: Number.MAX_SAFE_INTEGER, from: "owner", text: checked.text, local: true }];
      paintChat();
      terminal.chat = terminal.chat.filter((e) => !e.local);  // the hub's copy replaces it on the next poll
      $("chat-status").textContent = "The agent is reading your message...";
    } catch (error) {
      $("chat-status").textContent = describeError(error);
    } finally {
      button.disabled = false;
    }
  });

  $("telegram-link").addEventListener("click", async () => {
    try {
      const body = await hub.telegramCode(session.shop);
      $("telegram-code").textContent = hub.linkInstruction(body);
      $("telegram-code").hidden = false;
    } catch (error) {
      notify(describeError(error), "error");
    }
  });
  $("telegram-unlink").addEventListener("click", async () => {
    try {
      await hub.telegramUnlink(session.shop);
      notify("Telegram unlinked. The bot no longer listens to that chat.", "success");
      await renderShop();
    } catch (error) {
      notify(describeError(error), "error");
    }
  });

  $("agent-off").addEventListener("click", (event) => switchAgent(event.currentTarget, false));
  $("agent-on").addEventListener("click", (event) => switchAgent(event.currentTarget, true));

  onSubmit("agent-form", (data, button, form) => {
    let agent;
    try {
      agent = readAddress(data.get("agent"));
    } catch (error) {
      notify(describeError(error), "error");
      return;
    }
    act(button, "Change the agent", (shop) => shop.setAgent(agent)).then((ok) => ok && form.reset());
  });

  $("erp-signin-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      notify("Sign in: confirm the signature in MetaMask (it is free)...");
      await hub.signIn(await session.provider.getSigner(), session.shop);
      notify("");
      await renderShop();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

  onSubmit("erp-form", async (data, button, form) => {
    let request;
    try {
      request = hub.connectRequest(Object.fromEntries(data));
    } catch (error) {
      notify(describeError(error), "error");
      return;
    }
    button.disabled = true;
    renderChecks(null);
    try {
      notify("Testing your ERPNext keys...");
      const result = await hub.connectErp(session.shop, request);
      form.reset();
      notify("Connected. Your shop's agent checks your ERPNext every 15 minutes.", "success");
      await renderShop();
      renderChecks(result.probe);
    } catch (error) {
      renderChecks(error.details?.probe ?? null);
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

  $("check-now").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await checkAndWait();
    } finally {
      button.disabled = false;
    }
  });

  $("erp-refresh").addEventListener("click", () => renderShop().catch((error) => notify(describeError(error), "error")));

  $("erp-disconnect").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await hub.disconnectErp(session.shop);
      notify("Disconnected. The agent stopped and your ERPNext keys were deleted.", "success");
      await renderShop();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

  $("erp-set-agent-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const shop = await hub.status(session.shop).catch(() => null);  // the address from the hub, not the page
    if (!shop?.agent_wallet) return;
    act(button, "Make it the agent", (contract) => contract.setAgent(shop.agent_wallet));
  });

  onSubmit("supplier-form", (data, button, form) => {
    let supplier;
    try {
      supplier = readAddress(data.get("supplier"));
    } catch (error) {
      notify(describeError(error), "error");
      return;
    }
    act(button, "Approve supplier", (shop) => shop.setPayee(supplier, true)).then((ok) => ok && form.reset());
  });
}

function start() {
  $("faucet-link").href = FAUCET_URL;
  $("explorer-link").href = ARC_TESTNET.explorer;
  wireActions();
  wallet.onWalletChange(() => render().catch((error) => notify(describeError(error), "error")));
  render().catch((error) => notify(describeError(error), "error"));
}

start();

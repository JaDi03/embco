// The owner's dashboard: connect MetaMask, run the shop, and create its contract from Settings.
// Every change is a transaction the owner signs in MetaMask; this page holds no keys.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { ARC_TESTNET, FAUCET_URL } from "./config.js";
import { describeError } from "./errors.js";
import { pendingApprovals } from "./approvals.js";
import * as activity from "./activity.js";
import * as feed from "./feed.js";
import * as chat from "./chat.js";
import * as hub from "./hub.js";
import * as ledger from "./ledger.js";
import { renderChart } from "./treasury-chart.js";
import * as console_ from "./terminal.js";
import * as nav from "./nav.js";
import { agentView, groupTasks, QUICK_ANSWERS, raisedLimits, taskFor } from "./needs.js";
import * as suppliers from "./suppliers.js";
import * as unified from "./unified.js";
import * as earn from "./earn.js";
import { factory, memoNames, read, shopContract, shopHistory, shopsOf, shopState, usdc } from "./shop.js";
import { AUTHORIZE_ALL, authorizationView, formatUsdc, parseUsdc, shortAddress, ZERO_ADDRESS } from "./units.js";
import { chooseWallet } from "./picker.js";
import "./theme.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
const VIEWS = ["view-welcome", "view-network", "view-shop"];

let session = { provider: null, account: null, shop: null };

function show(view) {
  for (const id of VIEWS) $(id).hidden = id !== view;
}

/** What is happening, in the open tool when there is one: a modal sits above the page's notice. */
function notify(text, kind = "info") {
  const open = document.querySelector("dialog[open] .modal-notice");
  for (const notice of [$("notice"), ...document.querySelectorAll(".modal-notice")]) {
    const here = notice === (open ?? $("notice"));
    notice.textContent = here ? text : "";
    notice.className = `${notice === $("notice") ? "notice" : "notice modal-notice"}${kind === "info" ? "" : ` ${kind}`}`;
    notice.hidden = !here || !text;
  }
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
  $("account-full").textContent = account ?? "";
  if (!account) accountMenu(false);
}

function accountMenu(open) {
  $("account-menu").hidden = !open;
  $("account-button").setAttribute("aria-expanded", String(open));
}

function readAddress(text) {
  const value = String(text ?? "").trim();
  if (!ethers.isAddress(value)) throw new Error("That is not a valid wallet address (0x followed by 40 characters).");
  return ethers.getAddress(value);
}

// Rendering

function showWelcome() {
  const hasWallet = wallet.wallets().length > 0;
  show("view-welcome");
  $("connect-button").hidden = !hasWallet;
  $("no-wallet").hidden = hasWallet;
}

async function render() {
  const account = await wallet.reconnect();
  notify("");
  setAccount(account);
  if (!account) {
    $("network-pill").hidden = true;
    showWelcome();
    return;
  }
  const provider = wallet.provider();
  session = { provider, account, shop: null };
  if (!(await wallet.onArc())) {
    $("network-pill").hidden = true;
    show("view-network");
    return;
  }
  $("network-pill").hidden = false;
  notify("Looking for your shop contract...");
  const shops = await shopsOf(account);
  notify("");
  session.shop = shops.length ? shops[shops.length - 1] : null;
  show("view-shop");
  setShopMode(Boolean(session.shop));
  if (session.shop) await renderShop();
  else await renderNoShop();
}

/** The console is the same with or without a contract; without one, Settings offers to create it. */
function setShopMode(hasShop) {
  $("no-shop").hidden = hasShop;
  $("create-section").hidden = hasShop;
  for (const el of document.querySelectorAll(".shop-only")) el.hidden = !hasShop;
  for (const id of ["tab-invoices", "tab-suppliers", "tab-payments"]) $(id).hidden = !hasShop;
  if (!hasShop) $("bill-numbers").hidden = true;
}

async function renderNoShop() {
  stopTerminal();
  signedOut(false);
  $("panes").dataset.mview = "work";
  for (const el of document.querySelectorAll(".owner-only")) el.hidden = true;
  $("not-owner").hidden = true;
  $("agent-note").textContent = "";
  const shopLink = $("shop-link");
  shopLink.textContent = "No contract yet";
  shopLink.removeAttribute("href");
  for (const id of ["allowance", "remaining", "max-per-payment"]) $(id).textContent = "-";
  const balance = await read((provider) => usdc(provider).balanceOf(session.account)).catch(() => null);
  $("balance").textContent = balance === null ? "-" : `${formatUsdc(balance)} USDC`;
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
  const authorization = authorizationView(state.allowance, state.weeklyCap);
  $("allowance").textContent = authorization.text;
  $("allowance").className = authorization.state === "on" ? "status-on" : "status-off";
  $("authorization-note").textContent = authorization.note;
  $("authorize-button").hidden = !authorization.canAuthorize;  // nothing to authorize or revoke twice
  $("revoke-button").hidden = !authorization.canRevoke;
  $("max-per-payment").textContent = `${formatUsdc(state.maxPerPayment)} USDC`;
  $("weekly-cap").textContent = `${formatUsdc(state.weeklyCap)} USDC`;
  session.limits = { maxPerMove: state.maxPerPayment, weeklyCap: state.weeklyCap };  // every chain uses them
  $("remaining").textContent = `${formatUsdc(state.remaining)} USDC`;

  const hasAgent = state.agent !== ZERO_ADDRESS;
  const service = isOwner ? await hub.status(session.shop).catch(() => null) : null;
  const view = hub.agentSwitchView(service?.connected ? service.agent_on : null, state.paused);
  session.agentPaused = state.paused;
  const status = $("agent-status");
  status.textContent = !hasAgent ? "No agent yet" : view.status;
  status.className = hasAgent && view.on ? "status-on" : "status-off";
  $("agent-note").textContent = hasAgent ? view.note : "";
  const mode = hub.draftModeView(service);
  const badge = $("draft-badge");
  badge.hidden = !isOwner || !mode;
  if (mode) {
    badge.textContent = mode.badge;
    badge.className = `${mode.badgeClass} owner-only`;
    badge.title = mode.note;
  }
  $("agent-address").replaceChildren(hasAgent ? link(shortAddress(state.agent), addressUrl(state.agent)) : "none");
  session.agent = hasAgent ? state.agent : null;
  const telegram = Boolean(service?.telegram_linked);
  $("telegram-state").textContent = !service ? "sign in to the embco service first" : telegram ? "linked: /stop and /pause work from your phone" : "not linked";
  $("telegram-link").hidden = !isOwner || !service?.connected || telegram;
  $("telegram-unlink").hidden = !isOwner || !telegram;
  if (telegram) $("telegram-code").hidden = true;
  $("agent-off").hidden = !isOwner || !view.on;
  $("agent-on").hidden = !isOwner || view.on;

  await Promise.all([renderHistory(isOwner), renderApprovals(isOwner),
    renderErp(isOwner, state).then(() => Promise.all([renderChains(isOwner), renderEarn(isOwner, service)]))]);
  renderTreasury(state, service);
  if (isOwner) renderChart($("treasury-chart"), await hub.balances(session.shop).catch(() => []));
}

// Treasury: where the owner's USDC is and what the planned payments leave in the wallet, from
// the chain (the wallet) and from what the agent read in its last check (Earn, other chains).

let gatewayOpen = false; // the Gateway rows stay folded until the owner opens them

function renderTreasury(state, service) {
  const lastRun = service?.last_run;
  const rows = ledger.moneyRows({ wallet: state.balance, earn: lastRun?.earn, unified: lastRun?.unified_balance });
  const totals = ledger.moneyTotals(rows);
  $("treasury-at").textContent = lastRun?.at ? `Balances as of ${new Date(lastRun.at).toLocaleString()}` : "";
  $("treasury-total").textContent = `${formatUsdc(totals.total)} USDC`;
  $("treasury-earn").textContent = `${formatUsdc(totals.inEarn)} USDC`;
  $("treasury-earn-note").textContent = totals.inEarn ? "Withdrawn to your wallet when a bill needs it." : "Nothing in Earn.";
  const moneyRow = (r, className = "") => el("tr", { className: `${className}${r.muted ? " muted" : ""}`.trim() },
    el("td", { textContent: r.name }),
    el("td", { className: "num", textContent: formatUsdc(r.units) }),
    el("td", { className: "why", textContent: r.note }));
  const gateway = rows.filter((r) => r.group === "gateway");
  const gatewayTotal = gateway.reduce((sum, r) => sum + r.units, 0n);
  const toggle = el("button", {
    type: "button", className: "row-toggle", textContent: `${gatewayOpen ? "▾" : "▸"} Circle Gateway (Unified Balance)`,
    ariaExpanded: String(gatewayOpen),
  });
  toggle.addEventListener("click", () => { gatewayOpen = !gatewayOpen; renderTreasury(state, service); });
  const gatewayHead = el("tr", { className: gatewayTotal === 0n ? "muted" : "" },
    el("td", {}, toggle),
    el("td", { className: "num", textContent: formatUsdc(gatewayTotal) }),
    el("td", { className: "why", textContent: "USDC you deposited to Circle Gateway, one balance across chains. The agent brings it to your wallet on Arc." }));
  $("money-rows").replaceChildren(
    ...rows.filter((r) => !r.group).map((r) => moneyRow(r)),
    ...(gateway.length ? [gatewayHead, ...(gatewayOpen ? gateway.map((r) => moneyRow(r, "sub-row")) : [])] : []));

  const plan = ledger.cashPlan(lastRun?.ok ? lastRun.decisions : [], state.balance, ledger.today());
  $("treasury-out").textContent = `${formatUsdc(plan.out)} USDC`;
  $("treasury-out-note").textContent = plan.held.count
    ? `Plus ${formatUsdc(plan.held.units)} USDC waiting for you or on hold.`
    : `${plan.rows.length} planned by the agent.`;
  const start = el("tr", {}, el("td", { textContent: "Today" }), el("td", { textContent: "Your wallet on Arc" }),
    el("td", { className: "num" }), el("td", { className: "num", textContent: formatUsdc(state.balance) }));
  $("plan-rows").replaceChildren(start, ...plan.rows.map((r) => el("tr", {},
    el("td", { textContent: shortDay(r.day) }),
    el("td", {}, r.supplier, el("span", { className: "ref", textContent: r.invoice })),
    el("td", { className: "num", textContent: `-${formatUsdc(r.units)}` }),
    el("td", { className: `num${r.left < 0n ? " short" : ""}`, textContent: formatUsdc(r.left) }))));
  $("plan-note").textContent = !plan.rows.length
    ? "The agent has no payments planned in the next two weeks."
    : plan.short
      ? `Your wallet is ${formatUsdc(plan.short)} USDC short for these. The agent covers it from Earn or your other chains, as your Earn settings say, or asks you to add funds.`
      : "Your wallet covers every planned payment.";
}

/** "2026-10-20" -> "Oct 20". */
function shortDay(day) {
  return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// The chains the agent can pay from, read on each chain. The owner turns them on or off and saves:
// one confirmation in their wallet for each chain that changes.

const chains = { open: false, rows: [] };

// Logos from the networks' own brand kits (brand.base.org, ethereum.org/assets).
const CHAIN_LOGOS = { Base_Sepolia: "brand/chains/base.svg", Ethereum_Sepolia: "brand/chains/ethereum.svg" };

function chainRow(row) {
  const toggle = Object.assign(document.createElement("input"), {
    type: "checkbox", name: row.chain, checked: row.status !== "off", className: "switch",
  });
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-label", `Let the agent pay from ${row.name}`);
  const note = row.status === "update" ? " · save to use your shop's limits" : "";
  const label = el("label", { className: "chain-row" },
    CHAIN_LOGOS[row.chain]
      ? el("img", { className: "chain-logo", src: CHAIN_LOGOS[row.chain], alt: "", width: 32, height: 32 })
      : el("span", { className: "chain-badge", textContent: row.name[0], ariaHidden: "true" }),
    el("span", { className: "chain-name" },
      el("b", { textContent: row.name }),
      el("span", { className: "hint", textContent: `${formatUsdc(row.inWallet)} USDC in your wallet${note}` })),
    toggle);
  return el("li", {}, label);
}

async function renderChains(isOwner) {
  const open = $("chains-open");
  $("chains-form").hidden = !chains.open;
  open.setAttribute("aria-expanded", String(chains.open));
  if (!isOwner) return;
  if (!session.signer || !session.agent) {
    $("chains-summary").textContent = "Sign in to see and choose them. It is a signature, and costs nothing.";
    open.hidden = true;
    chains.open = false;
    $("chains-form").hidden = true;
    return;
  }
  open.hidden = false;
  try {
    chains.rows = await unified.readChains({
      owner: session.account, agent: session.agent, signer: session.signer, ...session.limits,
    });
  } catch (error) {
    $("chains-summary").textContent = `Could not read the other chains right now (${describeError(error)}).`;
    return;
  }
  const on = chains.rows.filter((r) => r.status !== "off").map((r) => r.name);
  $("chains-summary").textContent = on.length ? `On: ${on.join(", ")}.` : "None yet.";
  $("chain-list").replaceChildren(...chains.rows.map(chainRow));
}

const earnState = { rows: [], maxPerPark: 0n, weeklyCap: 0n };

function earnVaultRow(row) {
  const toggle = Object.assign(document.createElement("input"), {
    type: "checkbox", name: row.key, checked: row.status !== "off", className: "switch",
  });
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-label", `Allow ${row.name}`);
  return el("li", {}, el("label", { className: "chain-row" },
    el("span", { className: "chain-badge", textContent: row.name[0], ariaHidden: "true" }),
    el("span", { className: "chain-name" },
      el("b", { textContent: `${row.name} (${row.apy})` }),
      el("span", { className: "hint", textContent: `${formatUsdc(row.assets)} USDC in your wallet` })),
    toggle));
}

async function renderEarn(isOwner, service) {
  if (!isOwner || !session.agent) return;
  try {
    const info = await earn.readEarn({ owner: session.account, agent: session.agent, ...session.limits });
    earnState.rows = info.vaults;
    earnState.maxPerPark = info.maxPerPark || session.limits.maxPerMove;
    earnState.weeklyCap = info.weeklyCap || session.limits.weeklyCap;
  } catch (error) {
    $("earn-summary").textContent = `Could not read Arc Earn right now (${describeError(error)}).`;
    return;
  }
  const active = earnState.rows.filter((r) => r.status !== "off");
  const totalAssets = earnState.rows.reduce((sum, r) => sum + r.assets, 0n);
  const master = $("earn-master-switch");
  master.checked = active.length > 0;
  $("earn-form").hidden = !master.checked;
  $("earn-withdraw-all").hidden = totalAssets === 0n;
  $("earn-summary").textContent = active.length
    ? `On (${active.map((r) => r.name).join(", ")}) · ${formatUsdc(totalAssets)} USDC earning yield.`
    : totalAssets > 0n
      ? `Off (agent will not park) · ${formatUsdc(totalAssets)} USDC still in vaults.`
      : "Off. The agent will never deposit into DeFi unless you turn this on.";
  $("earn-vault-list").replaceChildren(...earnState.rows.map(earnVaultRow));
  const form = $("earn-form");
  form.elements.maxPerPark.value = formatUsdc(earnState.maxPerPark);
  form.elements.weeklyCap.value = formatUsdc(earnState.weeklyCap);
  $("liquidity-strategy").value = service?.liquidity_strategy ?? "preserve_yield";
}

/** Signed out of the hub (never signed in, signed out, or the 12-hour sign-in ended): a bar on every page. */
function signedOut(on) {
  $("signin-bar").hidden = !on;
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

/** Without a connected ERP the console shows the ERP card instead of the agent. Called only when
 * that is the state, so a refresh of a connected shop never hides and redraws the console. */
function hideAgent() {
  $("work-setup").append($("erp-card"));
  work.groups = [];
  paintWork();
  $("suppliers-card").hidden = true;
  $("tasks-card").hidden = true;
  $("terminal-card").hidden = true;
}

async function renderErp(isOwner, state) {
  session.signer = null;  // known once the owner is signed in
  signedOut(false);
  if (!isOwner) {
    hideAgent();
    return;
  }
  erpError("");
  let shop;
  try {
    shop = await hub.status(session.shop);
  } catch (error) {
    hideAgent();
    showErp(null);
    erpError(`The embco service is not reachable right now (${error.message}).`);
    return;
  }
  signedOut(!shop);
  if (!shop) {
    hideAgent();
    renderChecks(null);
    showErp("erp-signin");
    return;
  }
  session.signer = hub.delegateOf(await hub.agentWallets(session.shop).catch(() => null), state.agent);
  if (!shop.connected) {
    hideAgent();
    showErp("erp-form");
    return;
  }
  renderChecks(null);
  showErp("erp-connected");
  $("shop-name").textContent = shop.company || "Your shop";
  $("drawer-erp").append($("erp-card"));  // connected: its details live in Settings
  $("erp-url").textContent = shop.erp_url;
  $("erp-company").textContent = shop.company;
  $("erp-agent-wallet").replaceChildren(
    shop.agent_wallet ? link(shortAddress(shop.agent_wallet), addressUrl(shop.agent_wallet)) : "not created",
  );
  $("erp-set-agent").hidden = !hub.needsSetAgent(shop.agent_wallet, state.agent);
  renderDraftMode(shop);
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

const terminal = { shop: null, events: [], interval: null, timer: null, ticker: null, loaded: false, chat: [], waiting: false, open: new Set() };

function startTerminal() {
  $("terminal-card").hidden = false;
  if (terminal.shop === session.shop && terminal.timer) return;  // already running for this shop
  stopTerminal();
  Object.assign(terminal, { shop: session.shop, events: [], interval: null, loaded: false, chat: [], waiting: false, open: new Set(), shown: null });
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
    if (error.status === 401) {  // the sign-in ended while the page was open: say so on every page
      stopTerminal();
      signedOut(true);
    }
    return;
  }
  terminal.interval = body.interval_minutes ?? terminal.interval;
  const fresh = body.events ?? [];
  const finished = terminal.loaded && fresh.some((e) => e.kind === "done");
  terminal.events = activity.merge(terminal.events, fresh);
  if (fresh.length || !terminal.loaded) paintActivity();
  if (fresh.some((e) => e.tx)) paintChat();  // a new receipt for the chat
  terminal.loaded = true;
  paintNextCheck();
  await pollChat();
  if (finished) renderShop().catch(() => null);  // the agent finished a check: refresh the cards
}

/** The agent's console: one line per thing it did, newest at the bottom. Lines that arrive while
 * the page is open are typed out, so the owner sees the agent working. */
function paintActivity() {
  const box = $("activity-log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const lines = console_.linesOf(terminal.events);
  if (!lines.length) {
    box.replaceChildren(el("div", { className: "t-line dim", textContent: "Waiting for the agent's first check..." }));
    return;
  }
  const first = !terminal.shown;
  terminal.shown ??= new Set();
  const nodes = [];
  let day = null;
  for (const line of lines) {
    const label = activity.dayOf(line.at);
    if (label && label !== day) {
      day = label;
      nodes.push(el("div", { className: "t-day", textContent: `── ${label} ──` }));
    }
    const text = el("span", { className: "t-text" });
    const node = el("div", { className: `t-line ${line.tone}` },
      el("span", { className: "t-time", textContent: console_.clock(line.at) }),
      el("span", { className: "t-tag", textContent: line.tag }), text);
    const fresh = !first && !terminal.shown.has(line.key);
    terminal.shown.add(line.key);
    if (fresh) typeOut(text, line);
    else fillLine(text, line);
    nodes.push(node);
  }
  box.replaceChildren(...nodes);
  if (atBottom || !box.dataset.painted) box.scrollTop = box.scrollHeight;
  box.dataset.painted = "1";
}

function fillLine(node, line) {
  node.replaceChildren(...withLinks(line.text));
  if (line.tx) node.append(" ", link(`tx ${shortAddress(line.tx)}`, txUrl(line.tx)));
}

/** A new line appears letter by letter, then gets its links. */
function typeOut(node, line) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    fillLine(node, line);
    return;
  }
  let shown = 0;
  node.classList.add("typing");
  const timer = setInterval(() => {
    shown = Math.min(line.text.length, shown + 3);
    node.textContent = line.text.slice(0, shown);
    if (shown >= line.text.length) {
      clearInterval(timer);
      node.classList.remove("typing");
      fillLine(node, line);
    }
  }, 16);
}

function feedItem(item) {
  const time = el("span", { className: "time", textContent: activity.timeOf(item.at).slice(0, 5) });
  if (item.type === "money") {
    const text = el("span", { className: "what" }, ...withLinks(item.event.text));
    if (item.event.tx) text.append(" ", link("tx", txUrl(item.event.tx)));
    return el("div", { className: "feed-money" }, el("span", { className: "ic", textContent: "✓", ariaHidden: "true" }), text, time);
  }
  if (item.type === "note") {
    return el("div", { className: `feed-note ${item.event.kind === "error" ? "bad" : ""}` },
      el("span", { className: "what" }, ...withLinks(activity.lineText(item.event))), time);
  }
  if (item.type === "work") return sessionCard(item.session);  // the agent's reasoning, in full
  // folded: a run of quiet checks; it stays open across repaints once opened
  const key = `quiet:${item.events[0].seq}`;
  const box = el("details", { className: "feed-fold quiet", open: terminal.open.has(key) },
    el("summary", {}, el("span", { className: "what", textContent: feed.quietText(item.events) }), time),
    ...item.events.map(timelineLine));
  box.addEventListener("toggle", () => (box.open ? terminal.open.add(key) : terminal.open.delete(key)));
  return box;
}

/** Text with its transaction hashes and addresses linked to the explorer. */
function withLinks(text) {
  return chat.linkParts(text).map((p) =>
    p.tx ? link(shortAddress(p.tx), txUrl(p.tx)) : p.address ? link(shortAddress(p.address), addressUrl(p.address)) : p.text);
}

/** The chat: what the owner and the agent said, as bubbles, with a receipt for each payment and
 * each move of funds where it happened, so the owner finds them in the conversation. */
function paintChat() {
  const box = $("chat-log");
  const items = feed.chatOf(terminal.chat, terminal.events);
  if (!items.length) {
    box.replaceChildren(el("div", { className: "empty", textContent: "Ask what is due, why something is on hold, or tell the agent what to do." }));
    return;
  }
  box.replaceChildren(...items.map((item) => (item.type === "money" ? feedItem(item) : bubble(item.entry))));
  box.scrollTop = box.scrollHeight;
}

/** A bill the page knows, by its invoice name: for supplier names in the chat. */
function billOf(invoice) {
  return work.groups.flatMap((g) => g.tasks).find((t) => t.decision.invoice === invoice)?.decision ?? null;
}

function bubble(entry) {
  const card = chat.questionCard(entry, billOf(entry.invoice), QUICK_ANSWERS);
  if (card) return questionBubble(card, entry);
  const b = chat.bubbleOf(entry);
  const quick = b.side === "me" ? feed.quickAnswer(b.text) : null;
  const node = el("div", { className: `bubble ${b.side}` }, ...withLinks(quick ? quick.text : b.text));
  if (quick) node.append(el("span", { className: "meta", textContent: `↳ ${billOf(quick.invoice)?.supplier ?? quick.invoice}` }));
  if (b.meta) node.append(el("span", { className: "meta", textContent: b.meta }));
  return node;
}

/** The agent's question: who and how much, the question, and two answers. Once the owner
 * answered it (a quick answer on the same bill after it), the buttons give way to a line. */
function questionBubble(card, entry) {
  const answered = terminal.chat.some((e) => e.from === "owner" && e.seq > entry.seq
    && feed.quickAnswer(e.text)?.invoice === entry.invoice);
  const node = el("div", { className: "bubble agent ask-card" },
    el("span", { className: "ask-title", textContent: card.title }),
    el("span", { textContent: card.text }));
  if (answered) {
    node.append(el("span", { className: "meta", textContent: "Answered" }));
    return node;
  }
  const row = el("div", { className: "row" });
  for (const [label, text, style] of [["Yes, pay it", card.yes, "btn-primary"], ["No, hold it", card.no, "btn-ghost"]]) {
    const button = el("button", { className: `btn btn-small ${style}`, type: "button", textContent: label });
    button.addEventListener("click", async () => {
      for (const b of row.querySelectorAll("button")) b.disabled = true;
      try {
        await chat.sendMessage(session.shop, text);
        pollChat();
      } catch (error) {
        $("chat-status").textContent = describeError(error);
        for (const b of row.querySelectorAll("button")) b.disabled = false;
      }
    });
    row.append(button);
  }
  node.append(row);
  return node;
}

const CHOICE_CLASS = { HOLD: "held", PAY_NOW: "paid", SCHEDULE: "paid", ASK: "ask" };
const CHOICE_ICON = { held: "■", paid: "✓", ask: "?", note: "•" };

/** One time Claude was woken: why, what it decided, what it said. */
function sessionCard(s) {
  const meta = [activity.timeOf(s.at).slice(0, 5), s.steps ? `${s.steps} steps` : ""]
    .filter(Boolean).join(" · ");
  const card = el("div", { className: "session" },
    el("div", { className: "session-head" },
      el("span", { className: "mark-c", textContent: "C", ariaHidden: "true" }),
      el("b", { textContent: "Agent session" }),
      el("span", { className: "meta", textContent: meta })),
    el("span", { className: "hint", textContent: activity.whyText(s.woke) }));
  for (const e of s.items) {
    const kind = CHOICE_CLASS[e.choice] ?? "note";
    const label = activity.choiceLabel(e);
    const words = el("span", {}, ...(label ? [el("b", { className: "choice", textContent: label }), " "] : []), e.text);
    card.append(el("div", { className: `item ${kind}` },
      el("span", { className: "ic", textContent: CHOICE_ICON[kind], ariaHidden: "true" }),
      words));
  }
  if (s.failed) card.append(el("div", { className: "item held" }, el("span", { className: "ic", textContent: "!" }), el("span", { textContent: s.failed })));
  if (s.said) card.append(el("span", { className: "said", textContent: `"${s.said}"` }));
  return card;
}

function timelineLine(e) {
  const who = activity.actorOf(e.kind);
  const text = el("span", { textContent: activity.lineText(e) });
  if (e.tx) text.append(" ", link("tx", txUrl(e.tx)));
  return el("div", { className: "line" },
    el("span", { className: "time", textContent: activity.timeOf(e.at).slice(0, 5) }),
    el("span", { className: `who who-${who}`, textContent: who }),
    text);
}

/** The working line under the chat: animated dots and the time since the owner wrote. */
function showWorking() {
  const status = $("chat-status");
  status.textContent = chat.workingText(terminal.waiting, terminal.chat);
  status.classList.toggle("working", terminal.waiting);
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
  showWorking();
  if (terminal.waiting && !terminal.fastChat) {  // waiting for an answer: look every second
    terminal.fastChat = setTimeout(() => {
      terminal.fastChat = null;
      pollTerminal();  // the activity first, then the chat: the agent's work shows before its answer
    }, 1000);
  }
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
  $("tasks-summary").textContent = tasks.length ? "" : "No unpaid bills. The agent checks your ERPNext every 15 minutes.";
  $("nav-needs").textContent = String(needs);
  $("nav-needs").hidden = !needs;
  renderNumbers(lastRun, state);
  work.context = { company: shop.company, site: list?.site, state };
  work.groups = groupTasks(tasks);
  paintWork();
}

/** Due this week, overdue, and what the owner can pay with (the wallet on Arc plus Earn). */
function renderNumbers(lastRun, state) {
  const { dueWeek, overdue } = ledger.billsSummary(lastRun.decisions, ledger.today());
  const inEarn = ledger.units(lastRun.earn?.total_in_earn);
  const count = (n) => (n ? `${n} bill${n > 1 ? "s" : ""}` : "None");
  $("due-week").textContent = `${formatUsdc(dueWeek.units)} USDC`;
  $("due-week-note").textContent = count(dueWeek.count);
  $("overdue").textContent = `${formatUsdc(overdue.units)} USDC`;
  $("overdue").className = overdue.count ? "status-off" : "";
  $("overdue-note").textContent = count(overdue.count);
  $("available").textContent = `${formatUsdc(state.balance + inEarn)} USDC`;
  $("available-note").textContent = inEarn
    ? `${formatUsdc(state.balance)} in your wallet · ${formatUsdc(inEarn)} in Earn`
    : `In your wallet · ${formatUsdc(state.remaining)} left of this week's limit`;
  $("bill-numbers").hidden = false;
}

// Bills: one tab per state, one row per bill with the agent's reason; a row opens its details.

const work = { groups: [], current: null, context: null, open: null };

function paintWork() {
  const tabs = work.groups.map((g) => ({ key: g.key, label: g.title, n: g.tasks.length, urgent: g.key === "needs" }));
  if (!tabs.some((t) => t.key === work.current)) work.current = tabs[0]?.key ?? null;
  $("work-tabs").replaceChildren(...tabs.map((t) => {
    const button = el("button", { className: `tab${t.urgent && t.n ? " urgent" : ""}`, type: "button", textContent: t.label });
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(t.key === work.current));
    button.append(el("span", { className: "n", textContent: String(t.n) }));
    button.addEventListener("click", () => { work.current = t.key; paintWork(); });
    return button;
  }));
  const group = work.groups.find((g) => g.key === work.current);
  $("tasks").replaceChildren(...(group ? [billTable(group)] : []));
  paintBill();
}

function billTable(group) {
  const head = el("tr", {}, ...["Supplier", "Due", "Amount", "Agent's reason", ""].map((text, i) =>
    el("th", { className: i === 2 ? "num" : "", textContent: text })));
  const rows = group.tasks.map((task) => {
    const d = task.decision;
    const late = d.due_date && d.due_date < ledger.today() && !task.payment?.tx;
    const row = el("tr", { className: work.open === d.invoice ? "selected" : "", tabIndex: 0 },
      el("td", {}, d.supplier, el("span", { className: "ref", textContent: d.invoice })),
      el("td", { className: late ? "status-off" : "", textContent: d.due_date ? shortDay(d.due_date) : "-" }),
      el("td", { className: "num", textContent: suppliers.formatAmount(d.amount) }),
      el("td", { className: "why", textContent: ledger.reasonOf(task) }),
      el("td", { className: "num" }, el("span", { className: group.key === "needs" ? "btn btn-primary btn-small" : "open-link",
        textContent: group.key === "needs" ? "Review" : "Details" })));
    const open = () => { work.open = d.invoice; paintWork(); };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    return row;
  });
  return el("div", { className: "table-wrap" },
    el("p", { className: "hint group-hint", textContent: group.hint }),
    el("table", { className: "grid-table bills-table" }, el("thead", {}, head), el("tbody", {}, ...rows)));
}

/** The open bill's details: what the agent decided and why, each step with its button. */
function paintBill() {
  const task = work.groups.flatMap((g) => g.tasks).find((t) => t.decision.invoice === work.open);
  $("bill-drawer").hidden = !task;
  if (!task) {
    work.open = null;
    return;
  }
  const d = task.decision;
  $("bill-title").textContent = d.supplier;
  $("bill-ref").textContent = `${d.invoice} · ${suppliers.formatAmount(d.amount)} USDC${d.due_date ? ` · due ${shortDay(d.due_date)}` : ""}`;
  $("bill-body").replaceChildren(taskCard(task, work.context));
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
  const says = agentView(d);
  if (says) {
    const box = el("div", { className: "agent-says" },
      el("span", { className: "who", textContent: `AGENT · ${says.said.toUpperCase()}` }),
      el("span", { textContent: says.reason }));
    if (says.recommendation) box.append(el("span", { className: "rec", textContent: `Recommends: ${says.recommendation}` }));
    card.append(box);
  }
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
  if (task.weighed.length) {
    card.append(el("div", { className: "weighed" },
      el("span", { className: "who", textContent: "WHAT THE AGENT WEIGHS" }),
      el("ul", {}, ...task.weighed.map((text) => el("li", { textContent: text })))));
  }
  if (task.reply) card.append(replyBox(task.reply));
  if (task.decide) card.append(decideBox(task));
  return card;
}

/** Quick answers to the agent's question: each one is a message to the agent in the chat. */
function replyBox(reply) {
  const row = el("div", { className: "row" });
  for (const option of reply.options) {
    const button = el("button", { className: "btn btn-ghost", type: "button", textContent: option.label });
    button.addEventListener("click", async () => {
      for (const b of row.querySelectorAll("button")) b.disabled = true;
      try {
        await chat.sendMessage(session.shop, option.text);
        notify("Sent to the agent. Its answer appears in the chat.", "success");
        pollChat();
      } catch (error) {
        notify(describeError(error), "error");
        for (const b of row.querySelectorAll("button")) b.disabled = false;
      }
    });
    row.append(button);
  }
  row.append(el("span", { className: "hint", textContent: " or answer in the chat." }));
  return el("div", { className: "reply" }, row);
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
  if (!isOwner) {
    card.hidden = true;
    return;
  }
  let pending;
  try {
    const isApproved = (address) => read((provider) => shopContract(session.shop, provider).approvedPayee(address));
    pending = await pendingApprovals(session.shop, isApproved);
  } catch (error) {
    notify(describeError(error), "error");
    return;
  }
  card.hidden = !pending.length;
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
        act(approve, "Approve supplier", (shop) => shop.setPayee(address, true))
          .then((ok) => ok && checkAndWait()),  // the approval may answer a question: check now
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
  if (!suppliers.childElementCount) suppliers.replaceChildren(loading());  // a refresh keeps what is shown
  if (!payments.childElementCount) payments.replaceChildren(loading());

  let history;
  let memos;
  try {
    [history, memos] = await Promise.all([shopHistory(session.shop), memoNames(session.shop)]);
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
          const name = memos.get(p.invoiceRef.toLowerCase());
          detail.append(`to ${shortAddress(p.payee)}, paid by ${who}, `);
          if (name) {
            detail.append("invoice ", el("b", { textContent: name }), " ",
              el("span", { className: "memo-tag", textContent: "memo on Arc", title: "The invoice name is written on chain with the payment, through Arc's Memo contract: anyone can check it on the explorer." }), " ");
          } else {
            detail.append(`invoice reference ${p.invoiceRef.slice(0, 10)}... `);
          }
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

function renderDraftMode(shop) {
  const mode = hub.draftModeView(shop);
  $("draft-mode").hidden = !mode;
  if (!mode) return;
  $("draft-mode-state").textContent = mode.draft ? "drafts (draft mode)" : "submitted";
  $("draft-mode-state").className = mode.draft ? "status-on" : "status-off";
  $("draft-mode-note").textContent = mode.note;
  const toggle = $("draft-mode-toggle");
  toggle.textContent = mode.action;
  toggle.dataset.draft = String(mode.draft);
  toggle.hidden = false;
  $("draft-mode-confirm").hidden = true;
}

/** Leaving draft mode is two clicks: the second one says what it does to the owner's books. */
function askDraftModeOff(show) {
  $("draft-mode-confirm").hidden = !show;
  $("draft-mode-toggle").hidden = show;
  if (show) $("draft-mode-yes").focus();
}

async function setDraftMode(button, draft) {
  button.disabled = true;
  try {
    await hub.setDraftMode(session.shop, draft, !draft);
    notify(draft ? "Draft mode is on: each payment stays as a draft in ERPNext."
      : "Draft mode is off: each payment is submitted in ERPNext.", "success");
  } catch (error) {
    notify(`Draft mode was not changed: ${describeError(error)}`, "error");
  } finally {
    button.disabled = false;
    await renderShop();
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

/** Settings is a page now: opening it goes there, closing it goes back to the bills. */
function settings(open) {
  nav.go(open ? "settings" : "bills");
}

function chatPanel(open) {
  $("chat-panel").hidden = !open;
  $("chat-launch").setAttribute("aria-expanded", String(open));
  $("chat-launch").hidden = open;
  if (open) {
    $("chat-log").scrollTop = $("chat-log").scrollHeight;
    $("chat-input").focus();
  }
}

function wireActions() {
  $("connect-button").addEventListener("click", async () => {
    try {
      await wallet.connect(await chooseWallet($("wallet-list"), wallet.wallets()));
      await render();
    } catch (error) {
      notify(describeError(error), "error");
    }
  });

  $("account-button").addEventListener("click", () => accountMenu($("account-menu").hidden));
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".topbar-right")) accountMenu(false);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") accountMenu(false); });
  $("disconnect-button").addEventListener("click", async () => {
    accountMenu(false);
    stopTerminal();
    if (session.shop) await hub.signOut(session.shop).catch(() => null);  // the hub forgets this browser too
    session = { provider: null, account: null, shop: null };
    await wallet.disconnect();
    await render();
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
      const signer = await session.provider.getSigner();
      notify("Preparing your shop's agent (sign in if MetaMask asks: it is free)...");
      const wallets = await hub.newAgentWallets(signer);  // the contract is created with its agent
      notify("Create your shop contract: confirm it in MetaMask...");
      const tx = await factory(signer).create(wallets.agent, maxPerPayment, weeklyCap);
      notify("Creating your shop contract on Arc...");
      await tx.wait();
      settings(false);
      await render();
      notify("Your shop contract is ready. Next: authorize payments in Settings and approve your suppliers.", "success");
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

  onSubmit("allowance-form", (_data, button) => {
    act(button, "Authorize payments", (_shop, signer) => usdc(signer).approve(session.shop, AUTHORIZE_ALL));
  });

  $("revoke-button").addEventListener("click", (event) =>
    act(event.currentTarget, "Revoke", (_shop, signer) => usdc(signer).approve(session.shop, 0n)),
  );

  $("chains-open").addEventListener("click", () => {
    chains.open = !chains.open;
    $("chains-form").hidden = !chains.open;
    $("chains-open").setAttribute("aria-expanded", String(chains.open));
  });

  onSubmit("chains-form", async (data, button) => {
    const changes = chains.rows
      .map((row) => ({ row, on: data.get(row.chain) === "on" }))
      .filter(({ row, on }) => (on ? row.status !== "on" : row.status !== "off"));
    if (!changes.length) {
      notify("Nothing changed.");
      return;
    }
    button.disabled = true;
    try {
      for (const { row, on } of changes) {
        notify(`${row.name}: confirm it in your wallet (it switches to that chain)...`);
        const provider = wallet.eip1193();
        if (on) await unified.setUpChain(provider, row.chain, { agent: session.agent, signer: session.signer, ...session.limits });
        else await unified.turnOff(provider, row.chain, { signer: session.signer });
      }
      notify("Saved. The agent uses these chains from its next check.", "success");
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
      await wallet.switchToArc().catch(() => null);  // the rest of the page works on Arc
      await render();
    }
  });

  $("earn-master-switch").addEventListener("change", async (event) => {
    const master = event.currentTarget;
    if (master.checked) {
      $("earn-form").hidden = false;
      return;
    }
    const active = earnState.rows.filter((r) => r.status !== "off");
    if (!active.length) {
      $("earn-form").hidden = true;
      return;
    }
    master.disabled = true;
    try {
      const provider = wallet.eip1193();
      for (let i = 0; i < active.length; i++) {
        notify(`Turning off ${active[i].name}: confirm it in MetaMask...`);
        await earn.turnOffVault(provider, active[i].address, { otherVaultsOn: i < active.length - 1, onStep: notify });
      }
      notify("Earn is off. The agent will not park or unpark USDC.", "success");
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      master.disabled = false;
      await renderShop();
    }
  });

  $("earn-withdraw-all").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      notify("Withdraw all from Arc Earn to your wallet: confirm in MetaMask...");
      await earn.withdrawAllFromEarn(wallet.eip1193(), earnState.rows, notify);
      notify("All your Arc Earn funds are back in your wallet.", "success");
      await renderShop();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

  onSubmit("earn-form", async (data, button) => {
    button.disabled = true;
    try {
      const maxPerMove = parseUsdc(data.get("maxPerPark"));
      const weeklyCap = parseUsdc(data.get("weeklyCap"));
      if (maxPerMove > weeklyCap) throw new Error("The per-deposit limit cannot be above the weekly limit.");
      const chosen = earnState.rows.map((row) => ({ row, on: data.get(row.key) === "on" }));
      if (!chosen.some((c) => c.on)) throw new Error("Choose at least one vault, or turn Earn off with the switch above.");
      const provider = wallet.eip1193();
      let remainingOn = chosen.filter((c) => c.on).length;
      for (const { row, on } of chosen) {
        if (on) {
          notify(`${row.name}: confirm in MetaMask...`);
          await earn.setUpVault(provider, row.address, { agent: session.agent, maxPerMove, weeklyCap, onStep: notify });
        } else if (row.status !== "off") {
          notify(`Turning off ${row.name}: confirm in MetaMask...`);
          await earn.turnOffVault(provider, row.address, { otherVaultsOn: remainingOn > 0, onStep: notify });
          remainingOn = Math.max(0, remainingOn);
        }
      }
      await hub.setLiquidityStrategy(session.shop, String(data.get("liquidityStrategy") || "preserve_yield")).catch(() => null);
      notify("Saved. Your Earn settings and liquidity strategy are active.", "success");
      await renderShop();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  });

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

  $("open-create").addEventListener("click", () => {
    settings(true);
    $("create-form").elements.maxPerPayment.focus();
  });
  $("chat-launch").addEventListener("click", () => chatPanel(true));
  $("chat-close").addEventListener("click", () => chatPanel(false));
  $("bill-close").addEventListener("click", () => { work.open = null; paintWork(); });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("bill-drawer").hidden) { work.open = null; paintWork(); } else if (!$("chat-panel").hidden) chatPanel(false);
  });
  nav.onPage(() => { if (work.open) { work.open = null; paintWork(); } });

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
      terminal.chat = [...terminal.chat, { seq: Number.MAX_SAFE_INTEGER, at: new Date().toISOString(), from: "owner", text: checked.text, local: true }];
      paintChat();
      terminal.chat = terminal.chat.filter((e) => !e.local);  // the hub's copy replaces it on the next poll
      terminal.waiting = true;
      showWorking();
      pollChat();
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
  $("draft-mode-toggle").addEventListener("click", (event) => {
    if (event.currentTarget.dataset.draft === "true") askDraftModeOff(true);
    else setDraftMode(event.currentTarget, true);
  });
  $("draft-mode-cancel").addEventListener("click", () => askDraftModeOff(false));
  $("draft-mode-yes").addEventListener("click", (event) => setDraftMode(event.currentTarget, false));

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

  const signInAgain = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      notify("Sign in: confirm the signature in MetaMask (it is free)...");
      await hub.signIn(await session.provider.getSigner());
      notify("");
      await renderShop();
    } catch (error) {
      notify(describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  };
  $("erp-signin-button").addEventListener("click", signInAgain);
  $("signin-again").addEventListener("click", signInAgain);

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
    act(button, "Approve supplier", (shop) => shop.setPayee(supplier, true)).then((ok) => {
      if (!ok) return;
      form.reset();
      checkAndWait();  // the approval may answer a question: check now
    });
  });
}

function start() {
  $("faucet-link").href = FAUCET_URL;
  $("explorer-link").href = ARC_TESTNET.explorer;
  wireActions();
  nav.start();
  wallet.onWalletChange(() => render().catch((error) => notify(describeError(error), "error")));
  wallet.onWallets(() => { if (!$("view-welcome").hidden) showWelcome(); });  // a wallet announced late
  // Reconnecting to the wallet used last time: the start page shows meanwhile, never an empty page.
  const last = wallet.rememberedWallet();
  if (last) {
    show("view-welcome");
    notify(`Reconnecting to ${last.info.name}...`);
  }
  render().catch((error) => notify(describeError(error), "error"));
}

start();

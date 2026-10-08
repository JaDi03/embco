// The owner's dashboard: connect MetaMask, create the shop contract, and run it.
// Every change is a transaction the owner signs in MetaMask; this page holds no keys.

import { ethers } from "./vendor/ethers-6.17.0.min.js";
import { ARC_TESTNET, FAUCET_URL } from "./config.js";
import { describeError } from "./errors.js";
import { pendingApprovals } from "./approvals.js";
import * as hub from "./hub.js";
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
  const status = $("agent-status");
  status.textContent = !hasAgent ? "No agent yet" : state.paused ? "Paused" : "Active";
  status.className = hasAgent && !state.paused ? "status-on" : "status-off";
  $("agent-address").replaceChildren(hasAgent ? link(shortAddress(state.agent), addressUrl(state.agent)) : "none");
  $("pause-button").hidden = !isOwner || state.paused;
  $("resume-button").hidden = !isOwner || !state.paused;

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
  $("erp-decisions").replaceChildren(
    ...(view.decisions ?? []).map((d) => {
      const li = document.createElement("li");
      const badge = Object.assign(document.createElement("span"), {
        className: `badge badge-${d.action.toLowerCase()}`,
        textContent: d.action,
      });
      const what = Object.assign(document.createElement("div"), { className: "decision" });
      const title = document.createElement("span");
      title.append(badge, `${d.invoice} · ${d.supplier} · ${d.amount}`);
      const reasons = Object.assign(document.createElement("span"), {
        className: "reasons",
        textContent: d.reasons.join("; "),
      });
      what.append(title, reasons);
      li.append(what);
      return li;
    }),
  );
  await renderSuppliers(shop);
}

async function renderSuppliers(shop) {
  let list;
  try {
    list = await suppliers.listSuppliers(session.shop);
  } catch {
    return;  // the card stays hidden; the ERP card already reports a service problem
  }
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

  $("pause-button").addEventListener("click", (event) =>
    act(event.currentTarget, "Pause the agent", (shop) => shop.pause()),
  );
  $("resume-button").addEventListener("click", (event) =>
    act(event.currentTarget, "Resume the agent", (shop) => shop.unpause()),
  );

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

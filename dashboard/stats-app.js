// The stats page: what embco agents paid for real businesses, from Arc's explorer.
// No sign-in. Nothing here is our own count: every payment links to the explorer to check it.

import { ARC_TESTNET, FACTORY_ADDRESS, REAL_SHOPS } from "./config.js";
import { describeError } from "./errors.js";
import { explorerLogs } from "./shop.js";
import { dailyChart } from "./stats-chart.js";
import { dailyTotals, shopsFromFactory, summarize } from "./stats.js";
import "./theme.js";
import { formatUsdc, shortAddress } from "./units.js";

const $ = (id) => document.getElementById(id);
const day = (iso) => new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== ""));
  return node;
}

const explorer = (path, text) =>
  el("a", { href: `${ARC_TESTNET.explorer}/${path}`, target: "_blank", rel: "noopener", textContent: text });

function render({ shops, payments, usdc }) {
  $("real-shops").textContent = String(shops);
  $("real-payments").textContent = String(payments.length);
  $("real-usdc").textContent = `${formatUsdc(usdc)} USDC`;
  const days = dailyTotals(payments);
  $("stats-chart").replaceChildren(days.length
    ? dailyChart(days)
    : el("p", { className: "hint chart-empty", textContent: "No payments yet." }));
  $("payment-rows").replaceChildren(...payments.map((p) => el("tr", {},
    el("td", {}, explorer(`tx/${p.tx}`, day(p.at))),
    el("td", {}, explorer(`address/${p.shop}?tab=logs`, shortAddress(p.shop))),
    el("td", { className: "num", textContent: `${formatUsdc(p.amount)} USDC` }))));
  $("stats-body").hidden = false;
}

async function load() {
  $("factory-link").href = `${ARC_TESTNET.explorer}/address/${FACTORY_ADDRESS}?tab=logs`;
  try {
    // the explorer takes seconds per answer: ask for the factory and the real shops at once
    const real = REAL_SHOPS.map((a) => a.toLowerCase());
    const [factoryLogs, ...shopLogs] = await Promise.all([FACTORY_ADDRESS, ...real].map((a) => explorerLogs(a)));
    const histories = new Map(real.map((shop, i) => [shop, shopLogs[i]]));
    render(summarize(shopsFromFactory(factoryLogs), histories, REAL_SHOPS));
    $("stats-status").className = "journal-status";
    $("stats-status").textContent = `Read from Arc on ${new Date().toLocaleString()}.`;
  } catch (error) {
    $("stats-status").className = "journal-status bad";
    $("stats-status").textContent = describeError(error);
  }
}

load();

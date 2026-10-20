// The owner's money on Arc over time: Earn at the bottom, the wallet on Arc above it, from the
// points the hub keeps after each check. The other chains stay in the table: thousands parked
// there would flatten what Earn earns. Balances change in steps, so the areas are steps.

import { formatUsdc } from "./units.js";
import { units } from "./ledger.js";

export const SERIES = [
  { key: "earn", label: "Earn" },
  { key: "wallet", label: "Wallet on Arc" },
];

const W = 720;
const H = 220;
const PAD = { top: 12, right: 12, bottom: 24, left: 56 };
const SVG_NS = "http://www.w3.org/2000/svg";

/** Readable points, oldest first, with each place in USDC units (bigint). */
export function readPoints(points) {
  return (points ?? [])
    .map((p) => ({ at: Date.parse(p.at), wallet: units(p.wallet), earn: units(p.earn), other_chains: units(p.other_chains) }))
    .filter((p) => Number.isFinite(p.at))
    .sort((a, b) => a.at - b.at);
}

/** A round top for the y axis and its ticks: 0, then 1, 2 or 5 times a power of ten. */
export function niceTicks(max) {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v < max + step; v += step) ticks.push(v);
  return ticks;
}

/** Geometry of the chart: the stacked step areas, the y ticks and where each point sits. */
export function chartModel(points, now = Date.now()) {
  const data = readPoints(points);
  if (data.length < 2) return null;
  const usdc = (u) => Number(u) / 1e6;
  const t0 = data[0].at;
  const t1 = Math.max(now, data.at(-1).at);
  const totals = data.map((p) => usdc(p.earn + p.wallet));
  const ticks = niceTicks(Math.max(...totals));
  const top = ticks.at(-1);
  const x = (t) => PAD.left + ((t - t0) / Math.max(t1 - t0, 1)) * (W - PAD.left - PAD.right);
  const y = (v) => H - PAD.bottom - (v / top) * (H - PAD.top - PAD.bottom);
  const stacked = data.map((p) => {
    let base = 0;
    return SERIES.map((s) => {
      const low = base;
      base += usdc(p[s.key]);
      return [low, base];
    });
  });
  const areas = SERIES.map((s, i) => {
    const upper = [];
    const lower = [];
    data.forEach((p, j) => {
      const next = j + 1 < data.length ? data[j + 1].at : t1;
      const [low, high] = stacked[j][i];
      upper.push(`${x(p.at).toFixed(1)},${y(high).toFixed(1)}`, `${x(next).toFixed(1)},${y(high).toFixed(1)}`);
      lower.unshift(`${x(next).toFixed(1)},${y(low).toFixed(1)}`, `${x(p.at).toFixed(1)},${y(low).toFixed(1)}`);
    });
    return { ...s, d: `M${upper.join("L")}L${lower.join("L")}Z`, top: stacked.at(-1)[i] };
  });
  return {
    areas, data, t0, t1,
    ticks: ticks.map((v) => ({ v, y: y(v) })),
    xs: data.map((p) => x(p.at)),
    x, y,
  };
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function axisLabel(v) {
  return v >= 1000 ? `${(v / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })}k` : v.toLocaleString("en-US");
}

function dayLabel(t) {
  return new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Draw the chart into `box` (an element), or say why there is nothing to draw yet. */
export function renderChart(box, points) {
  const model = chartModel(points);
  box.replaceChildren();
  if (!model) {
    const first = readPoints(points)[0];
    const p = document.createElement("p");
    p.className = "hint chart-empty";
    p.textContent = first
      ? `First reading ${dayLabel(first.at)}. The chart fills in as the agent checks your balances.`
      : "The chart fills in as the agent checks your balances, every 15 minutes.";
    box.append(p);
    return;
  }
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
    "aria-label": "Your USDC on Arc over time: Earn and your wallet" });
  for (const t of model.ticks) {
    chart.append(svg("line", { x1: PAD.left, x2: W - PAD.right, y1: t.y, y2: t.y, class: "chart-grid" }));
    const label = svg("text", { x: PAD.left - 8, y: t.y + 4, class: "chart-axis", "text-anchor": "end" });
    label.textContent = axisLabel(t.v);
    chart.append(label);
  }
  for (const [t, anchor] of [[model.t0, "start"], [model.t1, "end"]]) {
    const label = svg("text", { x: model.x(t), y: H - 6, class: "chart-axis", "text-anchor": anchor });
    label.textContent = dayLabel(t);
    chart.append(label);
  }
  for (const a of model.areas) chart.append(svg("path", { d: a.d, class: `chart-area chart-${a.key}` }));
  const cross = svg("line", { y1: PAD.top, y2: H - PAD.bottom, class: "chart-cross", visibility: "hidden" });
  chart.append(cross);

  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  const legend = document.createElement("ul");
  legend.className = "chart-legend";
  for (const s of [...SERIES].reverse()) {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = `chart-swatch chart-${s.key}`;
    item.append(swatch, s.label);
    legend.append(item);
  }

  chart.addEventListener("pointermove", (event) => {
    const box_ = chart.getBoundingClientRect();
    const px = ((event.clientX - box_.left) / box_.width) * W;
    let i = 0;
    while (i + 1 < model.xs.length && model.xs[i + 1] <= px) i++;
    const p = model.data[i];
    cross.setAttribute("x1", model.xs[i]);
    cross.setAttribute("x2", model.xs[i]);
    cross.setAttribute("visibility", "visible");
    const total = p.earn + p.wallet;
    tip.replaceChildren(
      Object.assign(document.createElement("strong"), { textContent: dayLabel(p.at) }),
      ...[...SERIES].reverse().map((s) => Object.assign(document.createElement("span"), { textContent: `${s.label}: ${formatUsdc(p[s.key])}` })),
      Object.assign(document.createElement("span"), { textContent: `On Arc: ${formatUsdc(total)} USDC` }));
    tip.hidden = false;
    const left = (model.xs[i] / W) * box_.width;
    tip.style.left = `${Math.min(Math.max(left, 80), box_.width - 80)}px`;
  });
  chart.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });

  const plot = document.createElement("div");
  plot.className = "chart-plot";
  plot.append(chart, tip);
  box.append(legend, plot);
}

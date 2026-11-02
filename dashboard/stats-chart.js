// The stats page's bar chart: USDC the agent paid per day for real businesses, one bar per day
// from the first payment to today, so the days without payments show too.

import { niceTicks } from "./treasury-chart.js";
import { formatUsdc } from "./units.js";

const W = 720;
const H = 220;
const PAD = { top: 12, right: 56, bottom: 26, left: 8 };
const GAP = 2;
const RADIUS = 4;
const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

const dayLabel = (day) => new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const axisValue = (v) => v.toLocaleString("en-US", { maximumFractionDigits: v >= 100 ? 0 : 2 });

/** A bar with a rounded top and a square foot on the baseline. */
function barPath(x, y, w, base) {
  const r = Math.min(RADIUS, w / 2, base - y);
  return `M${x},${base}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${base}Z`;
}

/** Draw the chart for `days` (from dailyTotals) and return its element. */
export function dailyChart(days) {
  const top = Math.max(...days.map((d) => Number(d.usdc) / 1e6), 1);
  const ticks = niceTicks(0, top);
  const hi = Math.max(top, ticks.at(-1));
  const base = H - PAD.bottom;
  const slot = (W - PAD.left - PAD.right) / days.length;
  const y = (v) => base - (v / hi) * (base - PAD.top);

  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
    "aria-label": "USDC the agent paid each day for real businesses" });
  for (const v of ticks) {
    chart.append(svg("line", { x1: PAD.left, x2: W - PAD.right, y1: y(v), y2: y(v), class: "chart-grid" }));
    const label = svg("text", { x: W - PAD.right + 8, y: y(v) + 4, class: "chart-axis" });
    label.textContent = axisValue(v);
    chart.append(label);
  }
  // first and last day, and every seventh day between when there is room for it
  const labelled = new Set([0, days.length - 1]);
  for (let i = 7; i < days.length - 3; i += 7) labelled.add(i);
  for (const i of labelled) {
    const label = svg("text", { x: PAD.left + slot * (i + 0.5), y: H - 6, class: "chart-axis", "text-anchor": "middle" });
    label.textContent = dayLabel(days[i].day);
    chart.append(label);
  }

  const tip = el("div", { className: "chart-tip", hidden: true });
  days.forEach((d, i) => {
    const x = PAD.left + slot * i;
    const v = Number(d.usdc) / 1e6;
    if (d.count) {
      // the accent of style.css, also in the markup so a stale cached stylesheet never draws it black
      chart.append(svg("path", { d: barPath(x + GAP / 2, y(v), Math.max(slot - GAP, 1), base), class: "stats-bar", fill: "#2563eb" }));
    }
    // the hover target is the whole day column, larger than the bar
    // filled in the markup, not only in style.css: an SVG shape with no fill is drawn black
    const hit = svg("rect", { x, y: PAD.top, width: slot, height: base - PAD.top, class: "stats-hit", fill: "transparent" });
    hit.addEventListener("pointerenter", () => {
      tip.replaceChildren(el("strong", { textContent: dayLabel(d.day) }),
        el("span", { textContent: d.count
          ? `${formatUsdc(d.usdc)} USDC in ${d.count} payment${d.count === 1 ? "" : "s"}`
          : "No payments" }));
      tip.hidden = false;
      const box = chart.getBoundingClientRect();
      tip.style.left = `${Math.min(Math.max(((x + slot / 2) / W) * box.width, 80), box.width - 80)}px`;
    });
    chart.append(hit);
  });
  chart.addEventListener("pointerleave", () => { tip.hidden = true; });
  chart.append(svg("line", { x1: PAD.left, x2: W - PAD.right, y1: base, y2: base, class: "stats-baseline" }));
  return el("div", { className: "chart-plot" }, chart, tip);
}

// The Earn card in Treasury: what the owner's USDC in Earn is worth over time, when money went in
// and out, and the result. The line comes from the readings the hub keeps after each check; the
// moves from the ShopEarn contract's own events. Result = value now + what came out - what went in.

import { formatUsdc } from "./units.js";
import { units } from "./ledger.js";

const W = 720;
const H = 200;
const PAD = { top: 12, right: 64, bottom: 24, left: 8 };
const SVG_NS = "http://www.w3.org/2000/svg";
const HOUR = 3600 * 1000;

/** Readings of Earn, oldest first: { at (ms), units (bigint) }. */
export function earnPoints(points) {
  return (points ?? [])
    .map((p) => ({ at: Date.parse(p.at), units: units(p.earn) }))
    .filter((p) => Number.isFinite(p.at))
    .sort((a, b) => a.at - b.at);
}

/** Moves in and out of Earn, oldest first: { at (ms), kind, units, vault, by, tx }. */
export function earnMoves(moves) {
  return (moves ?? [])
    .map((m) => ({ at: Date.parse(m.at), kind: m.kind, units: units(m.usdc), vault: m.vault, by: m.by, tx: m.tx }))
    .filter((m) => Number.isFinite(m.at) && (m.kind === "in" || m.kind === "out"))
    .sort((a, b) => a.at - b.at);
}

/** What went in, what came out, the value now, the result and its share of what went in, and
 * how long money was inside (first entry to now, or to the last exit once all is out). */
export function earnResult(points, moves, now = Date.now()) {
  const read = earnPoints(points);
  const list = earnMoves(moves);
  const sum = (kind) => list.filter((m) => m.kind === kind).reduce((s, m) => s + m.units, 0n);
  const putIn = sum("in");
  const tookOut = sum("out");
  const value = read.length ? read.at(-1).units : 0n;
  const gain = value + tookOut - putIn;
  const first = list.find((m) => m.kind === "in");
  const end = value > 0n || !list.length ? now : list.at(-1).at;
  return {
    putIn, tookOut, value, gain,
    percent: putIn > 0n ? (Number(gain) / Number(putIn)) * 100 : null,
    hours: first ? Math.max(0, (end - first.at) / HOUR) : 0,
  };
}

/** "+0.0123%" with the sign, enough decimals to show a small result. */
export function percentText(percent) {
  if (percent === null) return "";
  const digits = Math.abs(percent) >= 1 ? 2 : 4;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(digits)}%`;
}

/** "9 h", "3 days". */
export function timeInside(hours) {
  if (hours < 48) return `${Math.max(1, Math.round(hours))} h`;
  return `${Math.round(hours / 24)} days`;
}

/** Round values for the y axis between lo and hi: steps of 1, 2 or 5 times a power of ten. */
export function niceTicks(lo, hi) {
  const raw = (hi - lo) / 4;
  if (!(raw > 0)) return [lo];
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

/** Geometry: the line, the area under it and where each move sits on it. */
export function chartModel(points, moves, now = Date.now()) {
  const read = earnPoints(points);
  if (read.length < 2) return null;
  const usdc = (u) => Number(u) / 1e6;
  const t0 = read[0].at;
  const t1 = Math.max(now, read.at(-1).at);
  const values = read.map((p) => usdc(p.units));
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = (hi - lo) * 0.15 || Math.max(hi * 0.01, 0.01);
  lo = Math.max(0, lo - pad);
  hi += pad;
  const x = (t) => PAD.left + ((t - t0) / Math.max(t1 - t0, 1)) * (W - PAD.left - PAD.right);
  const y = (v) => H - PAD.bottom - ((v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom);
  const valueAt = (t) => {
    let v = values[0];
    read.forEach((p, i) => { if (p.at <= t) v = values[i]; });
    return v;
  };
  const steps = read.flatMap((p, i) => {
    const next = i + 1 < read.length ? read[i + 1].at : t1;
    return [[x(p.at), y(values[i])], [x(next), y(values[i])]];
  });
  const line = `M${steps.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join("L")}`;
  const base = (H - PAD.bottom).toFixed(1);
  const area = `${line}L${steps.at(-1)[0].toFixed(1)},${base}L${steps[0][0].toFixed(1)},${base}Z`;
  const marks = earnMoves(moves).filter((m) => m.at >= t0 && m.at <= t1)
    .map((m) => ({ ...m, x: x(m.at), y: y(valueAt(m.at)) }));
  return { line, area, marks, lo, hi, t0, t1, x, y, read, values, ticks: niceTicks(lo, hi) };
}

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

function when(t) {
  return new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function axisValue(v) {
  return v.toLocaleString("en-US", { maximumFractionDigits: v >= 100 ? 0 : 2 });
}

/** Draw the Earn card into `box`: the result, the line with its moves, and the list of moves. */
export function renderEarnCard(box, { points, earn_moves: moves } = {}) {
  const result = earnResult(points, moves);
  const list = earnMoves(moves);
  box.replaceChildren();
  if (!list.length) {
    box.append(el("p", { className: "hint chart-empty", textContent: "Nothing has gone into Earn yet. When it does, you see here what it earned." }));
    return;
  }
  const sign = result.gain > 0n ? "+" : "";
  box.append(el("dl", { className: "earn-head" },
    el("div", {}, el("dt", { textContent: "Value now" }), el("dd", { textContent: `${formatUsdc(result.value)} USDC` })),
    el("div", {}, el("dt", { textContent: "Result" }),
      el("dd", { className: result.gain > 0n ? "up" : "" , textContent: `${sign}${formatUsdc(result.gain)} USDC ${percentText(result.percent)}` })),
    el("div", {}, el("dt", { textContent: "Time inside" }), el("dd", { textContent: timeInside(result.hours) }))));

  const model = chartModel(points, moves);
  if (model) box.append(drawChart(model));
  else box.append(el("p", { className: "hint chart-empty", textContent: "The line fills in as the agent checks your balances." }));

  box.append(el("table", { className: "grid-table earn-moves" },
    el("thead", {}, el("tr", {}, ...["Date", "Move", "Vault", "Amount", "By"].map((h, i) =>
      el("th", { className: i === 3 ? "num" : "", textContent: h })))),
    el("tbody", {}, ...[...list].reverse().map((m) => el("tr", {},
      el("td", { textContent: when(m.at) }),
      el("td", { textContent: m.kind === "in" ? "▲ In" : "▼ Out" }),
      el("td", { textContent: m.vault }),
      el("td", { className: "num", textContent: `${m.kind === "in" ? "+" : "-"}${formatUsdc(m.units)}` }),
      el("td", { className: "why", textContent: m.by === "agent" ? "The agent" : "You" }))))));
}

function drawChart(model) {
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img", "aria-label": "Your USDC in Earn over time, with each move in and out" });
  const defs = svg("defs");
  const fade = svg("linearGradient", { id: "earn-fade", x1: "0", y1: "0", x2: "0", y2: "1" });
  fade.append(svg("stop", { offset: "0", class: "earn-fade-top" }), svg("stop", { offset: "1", class: "earn-fade-bottom" }));
  defs.append(fade);
  chart.append(defs);
  for (const v of model.ticks) {
    const yy = model.y(v);
    chart.append(svg("line", { x1: PAD.left, x2: W - PAD.right, y1: yy, y2: yy, class: "chart-grid" }));
    const label = svg("text", { x: W - PAD.right + 8, y: yy + 4, class: "chart-axis" });
    label.textContent = axisValue(v);
    chart.append(label);
  }
  for (const [t, anchor] of [[model.t0, "start"], [model.t1, "end"]]) {
    const label = svg("text", { x: model.x(t), y: H - 6, class: "chart-axis", "text-anchor": anchor });
    label.textContent = when(t);
    chart.append(label);
  }
  chart.append(svg("path", { d: model.area, class: "earn-area" }), svg("path", { d: model.line, class: "earn-line" }));
  for (const m of model.marks) {
    const up = m.kind === "in";
    const d = up ? `M${m.x},${m.y - 9}l5,8h-10Z` : `M${m.x},${m.y + 9}l5,-8h-10Z`;
    const mark = svg("path", { d, class: "earn-mark" });
    const title = svg("title");
    title.textContent = `${up ? "In" : "Out"} ${formatUsdc(m.units)} USDC, ${when(m.at)}`;
    mark.append(title);
    chart.append(mark);
  }
  const cross = svg("line", { y1: PAD.top, y2: H - PAD.bottom, class: "chart-cross", visibility: "hidden" });
  chart.append(cross);
  const tip = el("div", { className: "chart-tip", hidden: true });
  chart.addEventListener("pointermove", (event) => {
    const box = chart.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * W;
    let i = 0;
    while (i + 1 < model.read.length && model.x(model.read[i + 1].at) <= px) i++;
    const xx = Math.min(Math.max(px, PAD.left), W - PAD.right);
    cross.setAttribute("x1", xx);
    cross.setAttribute("x2", xx);
    cross.setAttribute("visibility", "visible");
    tip.replaceChildren(el("strong", { textContent: when(model.read[i].at) }),
      el("span", { textContent: `${formatUsdc(model.read[i].units)} USDC in Earn` }));
    tip.hidden = false;
    tip.style.left = `${Math.min(Math.max((xx / W) * box.width, 80), box.width - 80)}px`;
  });
  chart.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });
  return el("div", { className: "chart-plot" }, chart, tip);
}

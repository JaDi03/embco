// One decision of the agent in plain words, read from the entries of its proof: what it did, why,
// what it checked, the limits it worked within and what it read. Only what the entries say.

function bodyOf(entry) {
  try {
    const body = JSON.parse(entry.body);
    return body && typeof body === "object" ? body : {};
  } catch {
    return {};
  }
}

function parsed(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

const DID = { PAY_NOW: "paid", SCHEDULE: "scheduled", HOLD: "held", ASK: "asked the owner about" };

const LIMITS = {
  max_per_payment: ["Most it may pay at once", (v) => `${v} USDC`],
  weekly_budget: ["Most it may pay in a week", (v) => `${v} USDC`],
  max_price_increase: ["Largest price rise it accepts", (v) => `${Math.round(Number(v) * 100)}%`],
  rate_tolerance: ["Price difference allowed against the order", (v) => `${Math.round(Number(v) * 100)}%`],
};

/** "three_way_match" -> "Three way match". */
export const plain = (name) => {
  const text = String(name ?? "").replaceAll("_", " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
};

/** Everything a person needs to read one decision, from a decision proof. */
export function decisionStory(bundle) {
  const of = (kind) => bundle.proofs.filter((p) => p.entry.kind === kind)
    .map((p) => ({ entry: p.entry, body: bodyOf(p.entry) }));
  const [agent] = of("AGENT");
  const [check] = of("DECISION");
  const [policy] = of("POLICY");
  const [session] = of("SESSION");
  const payments = of("PAYMENT").map((p) => p.body);
  const last = payments.at(-1);
  const choice = agent?.body.choice ?? "";
  return {
    invoice: bundle.invoice,
    shop: bundle.shop,
    supplier: check?.body.supplier ?? last?.supplier ?? "",
    amount: check?.body.amount ?? last?.amount ?? "",
    dueDate: check?.body.due_date ?? null,
    choice,
    did: DID[choice] ?? plain(choice).toLowerCase(),
    at: agent?.entry.at ?? null,
    reason: agent?.body.reason ?? "",
    payOn: agent?.body.pay_on ?? null,
    question: agent?.body.question ?? "",
    checks: (check?.body.findings ?? []).map(([control, outcome, why]) => ({
      control: plain(control), outcome, passed: outcome === "PASS", why })),
    limits: Object.entries(policy?.body.values ?? {}).map(([key, value]) => {
      const [name, show] = LIMITS[key] ?? [plain(key), (v) => v];
      return { name, value: show(value) };
    }),
    answers: of("ANSWER").map((a) => ({ verdict: a.body.verdict, note: a.body.note ?? "", at: a.entry.at })),
    payment: last ? {
      status: last.status,
      wallet: last.payee || "",
      tx: payments.map((p) => p.tx_hash).filter(Boolean).at(-1) ?? null,
      erpEntry: payments.map((p) => p.erp_entry).filter(Boolean).at(-1) ?? null,
      reason: last.reason ?? "",
    } : null,
    woke: (session?.body.wakes ?? []).map((w) => w.text),
    steps: (session?.body.trace ?? []).map((s) => ({
      tool: plain(s.tool), args: s.args ?? {}, ok: s.ok !== false, refused: s.refused ?? "",
      read: s.saw === undefined ? null : parsed(s.saw) })),
    summary: session?.body.summary ?? "",
    model: session?.body.model ?? "",
  };
}

/** The one sentence on top: "The agent paid 5 USDC to Eko Beverages for invoice ACC-PINV-1." */
export function headline(story) {
  const money = story.amount ? `${Number(story.amount)} USDC` : "";
  const to = story.supplier ? ` to ${story.supplier}` : "";
  if (story.choice === "PAY_NOW") return `The agent paid ${money}${to} for invoice ${story.invoice}.`;
  if (story.choice === "SCHEDULE") {
    return `The agent scheduled invoice ${story.invoice} (${money}${to}) to be paid on ${story.payOn}.`;
  }
  return `The agent ${story.did} invoice ${story.invoice} (${money}${story.supplier ? ` from ${story.supplier}` : ""}).`;
}

/** The checklist under a decision, from checkDecisionProof: four plain questions. */
export function checklist(result) {
  const anchoredAt = result.proofs.map((p) => p.anchoredAt).filter(Boolean).sort().at(-1) ?? null;
  return {
    anchoredAt,
    items: [
      { ok: result.proofs.every((p) => p.contentMatchesHash), text: "The records are exactly as the agent wrote them." },
      { ok: result.proofs.every((p) => p.included && p.anchored),
        text: "They are in the summary of the journal that the agent wrote on Arc." },
      { ok: result.unlinked === null, text: "They all belong to this one decision." },
      { ok: Boolean(result.listed) && !result.missing,
        text: result.listed ? "None of the records it rests on is missing."
          : "It was written before decisions listed their records, so nothing shows none is missing." },
    ],
  };
}

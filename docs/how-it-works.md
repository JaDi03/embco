# How it works

embco runs one cycle every 15 minutes (or once, on demand). Each cycle reads the shop's
purchase documents from ERPNext, checks every open invoice and remembers the result. When there
is something to decide, the AI model is woken: it decides, pays what it chose to pay (if
payments are on), sees what happened and acts on it.

## One cycle

<figure class="diagram" markdown>
![One cycle: the reflex reads ERPNext, the controls check each invoice and the rules write the hard stops and notes to the journal; payments already sent are followed and recorded in ERPNext; then the agent gets the findings, asks payments to pay, and payments re-read the ERP, simulate on Arc, send through Circle, record the Payment Entry and tell the agent the result](assets/diagrams/one-cycle.svg#only-light "One cycle")
![One cycle: the reflex reads ERPNext, the controls check each invoice and the rules write the hard stops and notes to the journal; payments already sent are followed and recorded in ERPNext; then the agent gets the findings, asks payments to pay, and payments re-read the ERP, simulate on Arc, send through Circle, record the Payment Entry and tell the agent the result](assets/diagrams/one-cycle-dark.svg#only-dark "One cycle")
</figure>

## The fixed rule

Each control returns a finding. Only two kinds stop an invoice, and no model can change them:
HOLD (the supplier's wallet is missing, invalid or not confirmed by the supplier's signature,
or the bill repeats one already entered) and ASK (a first payment to a new wallet, answered by
the owner). Everything else a control finds (a missing receipt, less received than billed, a
price jump, an amount over the per-payment limit, a disabled supplier) is a NOTE: evidence the
AI agent weighs before it decides to pay, hold or ask the owner.

<figure class="diagram" markdown>
![The fixed rule: a duplicate bill or a wallet that is missing, invalid or not signed means HOLD; a new or changed wallet means ASK the owner; otherwise the invoice is open to pay and the agent weighs the notes. The owner approving an ASK opens it, rejecting it means HOLD](assets/diagrams/the-fixed-rule.svg#only-light "The fixed rule")
![The fixed rule: a duplicate bill or a wallet that is missing, invalid or not signed means HOLD; a new or changed wallet means ASK the owner; otherwise the invoice is open to pay and the agent weighs the notes. The owner approving an ASK opens it, rejecting it means HOLD](assets/diagrams/the-fixed-rule-dark.svg#only-dark "The fixed rule")
</figure>

Checks are small: one control per file under `agent/guardrails/controls/`. Each one returns a
finding with its evidence, so the reason on screen is the reason the rule used.

## Who decides what

| Question | Decided by | Notes |
|---|---|---|
| Can the invoice be paid at all? | The rules | Only a wallet not yet proven or approved, or a duplicate bill, stops it. Fixed, testable, no model involved |
| Does what was billed make sense? | The AI model | It weighs the notes (receipt, quantities, price history, limits) and pays, holds or asks the owner, with its reason |
| Is this supplier's wallet safe? | The wallet control, then the owner | A new wallet waits for the supplier's signature and the owner's approval |
| What is paid now, and what waits for a date? | The AI model | It pays itself and reads the result; the gate refuses anything the rules forbid before it is sent |
| The owner's wallet on Arc is short: bring money from which chains? | The AI model | It reads the owner's Unified Balance on other chains and the fees of each split, and brings what it chooses. It only ever lands in the owner's own wallet on Arc, from chains where the owner authorized it |
| A payment did not go out: why, and who fixes it? | The AI model | It looks up the reason with its tools and tells the owner what only they can fix |
| Is the payment allowed on chain? | The shop contract | Limits, approved payee, once per invoice, pause |

Nothing leaves the owner's wallet until the owner authorizes payments in the dashboard and makes
the shop's agent wallet the agent of the contract.

## When the wallet on Arc is short

The owner may keep USDC on other chains too (Base Sepolia, Ethereum Sepolia). In the dashboard,
under **Which chains can the agent pay from?**, the owner turns on the chains they want to use.

When an invoice needs more than the owner's wallet on Arc holds, the agent:

1. looks at the owner's USDC on each chain, and at the fee of each way to move it,
2. chooses where to take it from and how much,
3. moves it into the owner's Unified Balance (Circle Gateway) and brings it to the owner's
   wallet on Arc,
4. pays the invoice.

The money only goes to the owner's own wallet, and only from chains the owner turned on. On each
of those chains a contract of the owner sets how much the agent can move at once and per week.
The agent pays no gas: Circle covers its network fees.

<figure class="diagram" markdown>
![Unified Balance: the owner turns a chain on and authorizes the agent; when an invoice needs more than the wallet on Arc holds, the agent deposits the owner's USDC from the funding contract into the owner's Unified Balance, waits until it is final, brings it to the owner's wallet on Arc, and pays from the shop contract](assets/diagrams/unified-balance.svg#only-light "Unified Balance")
![Unified Balance: the owner turns a chain on and authorizes the agent; when an invoice needs more than the wallet on Arc holds, the agent deposits the owner's USDC from the funding contract into the owner's Unified Balance, waits until it is final, brings it to the owner's wallet on Arc, and pays from the shop contract](assets/diagrams/unified-balance-dark.svg#only-dark "Unified Balance")
</figure>

## Memory

Every decision, owner answer, wallet challenge and payment step is an event in an append-only
journal. Each time the agent works, the journal keeps every tool it called and, for the tools
that read balances, plans and invoices, the exact answer it read, so a decision can be checked
against what the agent saw. Each event stores the hash of the one before it. After money moves, and at least once a
day, the agent writes the Merkle root of the whole journal (RFC 9162) to the JournalAnchor
contract on Arc. A copy of the journal that was changed, cut or reordered gives a different
root, even if every hash in the chain was recomputed. `embco verify` checks the chain and every
root written on Arc.

<figure class="diagram" markdown>
![Memory: each journal event stores the hash of the one before it, so the events form a chain](assets/diagrams/memory.svg#only-light "Memory")
![Memory: each journal event stores the hash of the one before it, so the events form a chain](assets/diagrams/memory-dark.svg#only-dark "Memory")
</figure>

## Owner answers

When the agent asks, the owner answers in one of three places. Each answer counts once and
only for the question the agent last showed:

- the dashboard: approving a new wallet in the shop contract is also the answer,
- the ERP, on the invoice, from an account listed in `EMBCO_OWNER_USERS`,
- the command line, with `embco answer`.

On Telegram the owner can talk to the agent, pause payments or stop it, but an answer to a
question still goes through one of the three places above.

See [Connect your ERPNext](connect.md) for the ERP side.

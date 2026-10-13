# How it works

embco runs one cycle every 15 minutes (or once, on demand). Each cycle reads the shop's
purchase documents from ERPNext, checks every open invoice and remembers the result. When there
is something to decide, the AI model is woken: it decides, pays what it chose to pay (if
payments are on), sees what happened and acts on it.

## One cycle

<figure class="diagram" markdown>
![One cycle: the reflex reads ERPNext, the controls check each invoice, the rules decide PAY, HOLD or ASK and write it to the journal, the model plans the payments with each choice checked by the gate, and payments re-read the ERP, simulate on Arc, send through Circle and record the Payment Entry](assets/diagrams/one-cycle.svg#only-light "One cycle")
![One cycle: the reflex reads ERPNext, the controls check each invoice, the rules decide PAY, HOLD or ASK and write it to the journal, the model plans the payments with each choice checked by the gate, and payments re-read the ERP, simulate on Arc, send through Circle and record the Payment Entry](assets/diagrams/one-cycle-dark.svg#only-dark "One cycle")
</figure>

## The fixed rule

Each control returns a finding. Only two kinds stop an invoice, and no model can change them:
HOLD (the supplier's wallet is missing, invalid or not confirmed by the supplier's signature,
or the bill repeats one already entered) and ASK (a first payment to a new wallet, answered by
the owner). Everything else a control finds (a missing receipt, less received than billed, a
price jump, an amount over the per-payment limit, a disabled supplier) is a NOTE: evidence the
AI agent weighs before it decides to pay, hold or ask the owner.

<figure class="diagram" markdown>
![The fixed rule: any hard stop means HOLD; otherwise anything that needs a person means ASK the owner; otherwise PAY. The owner approving turns ASK into PAY, rejecting turns it into HOLD](assets/diagrams/the-fixed-rule.svg#only-light "The fixed rule")
![The fixed rule: any hard stop means HOLD; otherwise anything that needs a person means ASK the owner; otherwise PAY. The owner approving turns ASK into PAY, rejecting turns it into HOLD](assets/diagrams/the-fixed-rule-dark.svg#only-dark "The fixed rule")
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
| A payment did not go out: why, and who fixes it? | The AI model | It looks up the reason with its tools and tells the owner what only they can fix |
| Is the payment allowed on chain? | The shop contract | Limits, approved payee, once per invoice, pause |

Nothing leaves the owner's wallet until the owner authorizes payments in the dashboard and makes
the shop's agent wallet the agent of the contract.

## Memory

Every decision, owner answer, wallet challenge and payment step is an event in an append-only
journal. Each event stores the hash of the one before it, so changing an old event breaks the
chain. `embco verify` checks it.

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

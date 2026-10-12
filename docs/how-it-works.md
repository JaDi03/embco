# How it works

embco runs one cycle every 15 minutes (or once, on demand). Each cycle reads the shop's
purchase documents from ERPNext, checks every open invoice, decides, remembers the decision and,
if payments are on, pays what was approved.

## One cycle

<figure class="diagram" markdown>
![One cycle: the reflex reads ERPNext, the controls check each invoice, the rules decide PAY, HOLD or ASK and write it to the journal, the model plans the payments with each choice checked by the gate, and payments re-read the ERP, simulate on Arc, send through Circle and submit the Payment Entry](assets/diagrams/one-cycle.svg#only-light "One cycle")
![One cycle: the reflex reads ERPNext, the controls check each invoice, the rules decide PAY, HOLD or ASK and write it to the journal, the model plans the payments with each choice checked by the gate, and payments re-read the ERP, simulate on Arc, send through Circle and submit the Payment Entry](assets/diagrams/one-cycle-dark.svg#only-dark "One cycle")
</figure>

## The fixed rule

Each invoice gets one verdict from the controls. The rule combines them in a fixed order, and
no model can change it:

<figure class="diagram" markdown>
![The fixed rule: any hard stop means HOLD; otherwise anything that needs a person means ASK the owner; otherwise PAY. The owner approving turns ASK into PAY, rejecting turns it into HOLD](assets/diagrams/the-fixed-rule.svg#only-light "The fixed rule")
![The fixed rule: any hard stop means HOLD; otherwise anything that needs a person means ASK the owner; otherwise PAY. The owner approving turns ASK into PAY, rejecting turns it into HOLD](assets/diagrams/the-fixed-rule-dark.svg#only-dark "The fixed rule")
</figure>

Checks are small: one control per file under `agent/guardrails/controls/`. Each one returns a
finding with its evidence, so the reason on screen is the reason the rule used.

## Who decides what

| Question | Decided by | Notes |
|---|---|---|
| Is the invoice allowed to be paid at all? | The rules | Fixed, testable, no model involved |
| Is this supplier's wallet safe? | The wallet control, then the owner | A new wallet waits for the supplier's signature and the owner's approval |
| What is paid now, and what waits for a date? | The AI model, when `EMBCO_BRAIN` is on (default) | It stages choices; the gate refuses anything the rules forbid |
| Is the payment allowed on chain? | The shop contract | Limits, approved payee, once per invoice, pause |
| Plain-language explanation | The AI model, when `EMBCO_EXPLAIN=on` | Display only; it never changes a decision |

With `EMBCO_AUTONOMY=observe` the agent decides and explains, but pays nothing. With
`EMBCO_PAY` off (the default), nothing leaves the owner's wallet.

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

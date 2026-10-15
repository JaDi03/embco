# embco

**A payables agent for the ERPNext you already run.** It reads purchase orders, receipts and
invoices, decides what to pay now, what to pay later, what to hold and what to ask the owner,
with a written reason, and pays in USDC on Arc through Circle. The AI agent pays and reads the
result; a smart contract sets how far it can go.

## Why it exists

embco checks each invoice before it pays:

- it compares the invoice with the order and the receipt,
- it pays only a wallet that the supplier signed and the owner approved,
- a contract the agent cannot change limits each payment and each week,
- every decision goes into a journal: if someone changes an old entry, it shows.

<div class="grid cards" markdown>

-   :material-sitemap: **[How it works](how-it-works.md)**

    One cycle from ERPNext to the chain, and the fixed rule that decides PAY, HOLD or ASK.

-   :material-layers-triple: **[Architecture](architecture.md)**

    The layers, the trust boundaries, and where each piece of code lives.

-   :material-file-code: **[Smart contracts](contracts.md)**

    The shop contract, who can call what, and every check before a payment.

-   :material-database-arrow-right: **[Connect your ERPNext](connect.md)**

    A step-by-step guide: four small changes in ERPNext, then two keys in the dashboard.

-   :material-server: **[Run your own server](server.md)**

    The same dashboard and agents on your server: one `.env` and one install script.

</div>

## At a glance

<figure class="diagram" markdown>
![embco at a glance: ERPNext sends orders, receipts and invoices to the agent; the agent tells the owner PAY, HOLD or ASK and pays through the shop contract within limits; the contract sends USDC to the supplier; the agent writes the Payment Entry back to ERPNext](assets/diagrams/at-a-glance.svg#only-light "At a glance")
![embco at a glance: ERPNext sends orders, receipts and invoices to the agent; the agent tells the owner PAY, HOLD or ASK and pays through the shop contract within limits; the contract sends USDC to the supplier; the agent writes the Payment Entry back to ERPNext](assets/diagrams/at-a-glance-dark.svg#only-dark "At a glance")
</figure>

## Built on

- **Arc and USDC.** Suppliers are paid in USDC, straight from the shop owner's wallet.
- **Circle** developer-controlled wallets send the agent's transactions with idempotency keys. A new shop's agent is a smart contract account whose network fees Circle Gas Station pays, so the owner never sends it gas.
- **An AI model** decides which payments to make and when, sends them, reads what happened and
  tells the owner what only they can fix. Its payments pass through the same gate and the same
  contract as everything else. Today it is Claude, by Anthropic
  ([how to use another model](architecture.md#model-independence)).
- **ERPNext** stays the system of record. embco adds a Payment Entry for every payment it makes.

## Status

The code is in [the repository](https://github.com/JaDi03/embco). The contracts and their
verification are described on the [contracts page](contracts.md).

# embco

**A payables agent for the ERPNext you already run.** It reads purchase orders, receipts and
invoices, decides **PAY, HOLD or ASK the owner** with a written reason, and settles in USDC on
Arc through Circle. The model proposes. A smart contract releases the money.

!!! warning "Early, testnet only"
    The agent reads, checks, decides, remembers and explains against a live ERPNext. With
    payments turned on, it pays the invoices it approved through the shop contract on Arc
    Testnet. There is no mainnet deployment.

## Why it exists

Double entry only proves that debits equal credits, and almost every mistake an agent can make
with money passes that check. embco puts the controls that catch those mistakes between the
model and the money:

- a match against the order and the receipt,
- change control on the payee wallet, with a signature from the supplier,
- a hard limit per payment and per week, in a contract the agent cannot change,
- a journal that nobody can rewrite without the chain breaking.

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
- **Circle** developer-controlled wallets send the agent's transactions with idempotency keys.
- **An AI model** plans which payments to make and when, and writes the explanations. Its
  decisions pass through the same gate as everything else. Today it is Claude, by Anthropic
  ([how to use another model](architecture.md#model-independence)).
- **ERPNext** stays the system of record. embco adds a Payment Entry for every payment it makes.

## Status

The code is in [the repository](https://github.com/JaDi03/embco). The contracts and their
verification are described on the [contracts page](contracts.md).

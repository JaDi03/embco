# Architecture

## Flow

```
ERPNext  --REST (limited user)-->  ledger/     domain models, LedgerAdapter, erpnext/
                                      |
                                      v
                                   controls/   one small check per file
                                      |
                                      v
                                   decision/   deterministic policy: PAY / HOLD / ASK,
                                      |        plus the owner's answers
                                      v
                                   journal/    memory: append-only, hash-chained
                                      |
                                      v
                                   runner/     one cycle, or every N minutes on its own
                                      |
                                      +---->   llm/   plain-language explanations (display only)

Arc Testnet:  ShopPayablesFactory --creates--> ShopPayables (one per shop, owned by the shop)
              pays from the owner's wallet, within limits only the owner can change
                     ^
                     |  owner signs every change in MetaMask
              dashboard/ (app.embco.xyz)
```

## Principles

1. Money is `Decimal`, never `float`.
2. The core talks only to `LedgerAdapter`, never to a specific ERP.
3. The model's output is an input to the policy, never the condition that releases money.
4. The limits live in a contract the agent cannot change.

The agent lives in `agent/src/embco/`, one package per concern. The contracts live in
`contracts/`.

## Module map

| Package | Responsibility |
|---|---|
| `ledger` | Domain models, `LedgerAdapter` protocol, `erpnext/` adapter (client, mappers, adapter). Reads only |
| `controls` | Context builder and one small module per control: three-way match, payee wallet, duplicates, price anomaly, payment limit, supplier status. A wallet never paid before is held until the supplier signs a challenge with it (proof the address is right and theirs), then the owner is asked to approve, since only a person knows whether the supplier really asked for the change |
| `signing` | EIP-712 wallet ownership message bound to embco and Arc testnet, and signer recovery (ordinary wallets; smart contract wallets need EIP-1271 on chain) |
| `decision` | Fixed combining rule (HOLD, then ASK, then PAY), policy config, the weekly-budget planner and owner answers: an approved ASK becomes PAY, a rejected one HOLD, only while the decision is unchanged. HOLD is never overridden |
| `journal` | Memory between runs, stored as changes: every run, each new or changed decision, the policy when it changes, invoices that leave the unpaid list, owner answers, wallet challenges and the signatures that answer them, and AI explanations. One append-only, hash-chained sequence; it grows with events, not with runs. `DecisionJournal` protocol, SQLite store |
| `runner` | One cycle (verify memory, decide, apply owner answers, remember, issue wallet challenges, explain, plan), the interval loop that survives an ERP outage and stops on a tampered memory, and the text report |
| `settings`, `cli` | Settings from the environment or `.env`, checked once, secrets never shown; the `embco` command: `run`, `watch`, `answer`, `challenge`, `sign-wallet`, `history`, `verify` |
| `llm` | A plain-language summary and next step for each HOLD or ASK decision, through Claude with structured output (default Claude Haiku 4.5; newer models also get low effort and the server-side refusal fallback). Explained once per decision and stored; a failure is logged and retried next cycle; never changes a decision. Off unless `EMBCO_EXPLAIN=on` |
| `dashboard/` | Static owner page (ethers 6.17.0 vendored, no build). Connects MetaMask, adds Arc Testnet, creates the shop from the factory, and lets the owner set the allowance and limits, approve suppliers, pause or resume the agent, and read suppliers and payments from the contract's events. Money is parsed and shown as exact 6-decimal integers. Chain reads go through the site's own `/rpc` proxy, which adds the node credential on the server, so the page never holds one. Served with a strict Content Security Policy |
| `contracts/` (Solidity) | `ShopPayables`, one per shop, created by its owner from `ShopPayablesFactory`. The money stays in the owner's wallet; the agent can only pay approved suppliers, once per invoice, within a per-payment and a weekly cap; owner or agent can pause, only the owner resumes or changes limits. Unit and fuzz tests, plus a fork test against Arc Testnet's real USDC |

## ERP independence

The core never imports ERPNext. Adding Odoo or SolidInvoice means a new adapter under
`ledger/` that satisfies the same protocol.

# Architecture

## Flow

```
ERPNext  --REST (read-only user)-->  ledger/      domain models, LedgerAdapter, erpnext/
                                       |
                                       v
                                    controls/     one small check per file
                                       |
                                       v
                                    decision/     deterministic policy: PAY / HOLD / ASK
                                       |
                          llm/ (input only)  -->  payments/   Circle wallet, simulation, idempotency
                                       |                |
                                       v                v
                                    evidence/       Arc contract (budget, approvals)
                                    hash-chained log
                                       |
                                       v
                                    api/            approvals, webhooks, public feed
```

## Principles

1. Money is `Decimal`, never `float`.
2. The core talks only to `LedgerAdapter`, never to a specific ERP.
3. The model's output is an input to the policy, never the condition that releases money.

The code lives in `agent/src/embco/`, one package per concern.

## Module map

| Package | Responsibility | Status |
|---|---|---|
| `ledger` | Domain models, `LedgerAdapter` protocol, `erpnext/` adapter (client, mappers, adapter) | Done (read-only) |
| `controls` | Context builder and one small module per control: three-way match, payee wallet, duplicates, price anomaly, payment limit, supplier status | Done |
| `decision` | Fixed combining rule (HOLD, then ASK, then PAY), policy config and the weekly-budget planner | Done |
| `llm` | Document extraction and suggestions, provider interface | Planned |
| `payments` | Circle wallets, simulation, idempotency, timeout recovery | Planned |
| `evidence` | Hash-chained decision log and public feed | Planned |
| `api` | Approvals, pause switch, webhooks | Planned |

## ERP independence

The core never imports ERPNext. Adding Odoo or SolidInvoice means a new adapter under
`ledger/` that satisfies the same protocol.

# embco

A payables agent you plug into the ERPNext you already run. It reads purchase orders, receipts
and invoices, decides **PAY / HOLD / ASK THE OWNER** with a written reason, and settles in
USDC on Arc through Circle. The model proposes. A smart contract releases the money.

Built for the Tameion Agents Hackathon (Canteen x Circle, on Arc).

> **Status: early, built in public.** Testnet only. See the module map in
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for what exists today.

## Why it exists

Double entry only proves that debits equal credits, and almost every mistake an LLM can make
with money passes that check. The controls that catch those mistakes live outside the ledger:
a match against the order and the receipt, change control on the payee, a witness such as the
chain, and a hard limit that a prompt cannot move. embco puts those controls between the model
and the money. Background: [Agents and Ledgers in 2026](https://thecanteenapp.com/analysis/2026/09/12/agents-and-ledgers.html).

## How it works

```
ERPNext (system of record)  ->  embco agent  ->  Circle wallet  ->  Arc (USDC)
 orders, receipts, invoices     controls +        policy contract     settlement
                                decision          releases funds
```

1. **Read** the shop's documents from ERPNext through its REST API (read-only user).
2. **Check** them: three-way match, payee wallet change, duplicates, limits, price anomaly.
3. **Decide** with a deterministic policy. The model's output is an input, never the release.
4. **Pay** through a Circle wallet. A contract on Arc enforces budgets and approval limits.
5. **Record** the payment back in ERPNext and publish a hash-chained decision log.

## Plug it into your ERPNext

No new system to deploy. The connection contract is four small changes to your instance:
an API user with a limited role, a wallet field on Supplier, a webhook, and the two
purchasing settings that enforce the three-way match. See [docs/connect.md](docs/connect.md).

## Repo map

| Path | What is there |
|---|---|
| `agent/` | The agent: reads ERPNext, runs the controls and decides. Code in `agent/src/embco/`, tests in `agent/tests/` |
| `connector/` | Prepares an ERPNext instance for the agent |
| `docs/` | Architecture, how to connect, and which Circle tools are used |

## Run it

```bash
cd agent
uv sync
uv run ruff check . && uv run pytest -q
```

Copy `.env.example` to `.env`, fill it in, then:

```bash
uv run embco --env-file ../.env run      # one cycle, full report
uv run embco --env-file ../.env watch    # a cycle every 15 minutes, on its own
uv run embco --env-file ../.env answer ACC-PINV-0001 approve --by owner
uv run embco --env-file ../.env verify   # check the memory was not altered
```

Nothing is paid yet: the agent decides, remembers and plans. With `EMBCO_EXPLAIN=on` and an
`ANTHROPIC_API_KEY`, Claude adds a plain-language explanation and a next step to every invoice
that is held or needs the owner. It is called only for decisions it has not explained yet, and
its words never change a decision.

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

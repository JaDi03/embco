# embco

A payables agent you plug into the ERPNext you already run. It reads purchase orders, receipts
and invoices, decides **PAY / HOLD / ASK THE OWNER** with a written reason, and settles in
USDC on Arc through Circle. The model proposes. A smart contract releases the money.

Built for the Tameion Agents Hackathon (Canteen x Circle, on Arc).

> **Status: early, built in public, testnet only.** Today the agent reads, checks, decides,
> remembers and explains, on its own, against a live ERPNext. The shop contract that holds the
> limits is written and tested against Arc Testnet's USDC. Payments are not live yet.

## Why it exists

Double entry only proves that debits equal credits, and almost every mistake an LLM can make
with money passes that check. The controls that catch those mistakes live outside the ledger:
a match against the order and the receipt, change control on the payee, a witness such as the
chain, and a hard limit that a prompt cannot move. embco puts those controls between the model
and the money. Background: [Agents and Ledgers in 2026](https://thecanteenapp.com/analysis/2026/09/12/agents-and-ledgers.html).

## How it works

```
ERPNext (system of record)  ->  embco agent                    ->  owner
 orders, receipts, invoices     checks, decides, remembers,        approves what the agent
                                explains, every 15 minutes          asks about
```

1. **Read** the shop's documents from ERPNext through its REST API, with a limited user.
2. **Check** them: three-way match, duplicates, price anomaly, payment limit, supplier status,
   and the supplier's wallet. A new wallet must be signed for by the supplier, then approved by
   the owner.
3. **Decide** PAY, HOLD or ASK with a fixed rule, and write the reason. A model never decides.
4. **Remember** every decision and every owner answer in an append-only, hash-chained journal,
   so the agent knows what changed and nobody can rewrite the past unnoticed.
5. **Explain**, optionally, each held or asked invoice in plain words, with Claude.

## Built on Arc and Circle

- **USDC on Arc.** Suppliers are paid in USDC, straight from the shop owner's wallet.
- **A contract per shop on Arc** (`contracts/`). The owner creates it from their own wallet.
  The agent can only pay suppliers the owner approved, once per invoice, within a per-payment
  and a weekly cap. Only the owner can change those limits, and revoking the contract's
  allowance stops everything.

## Plug it into your ERPNext

No new system to deploy. The connection is three small changes to your instance: an API user
with a limited role, a wallet field on Supplier, and the two purchasing settings that enforce
the three-way match. See [docs/connect.md](docs/connect.md).

## Repo map

| Path | What is there |
|---|---|
| `agent/` | The agent: reads ERPNext, runs the controls and decides. Code in `agent/src/embco/`, tests in `agent/tests/` |
| `contracts/` | The shop contract and its factory (Solidity, Arc Foundry): the limits the agent cannot move |
| `connector/` | Prepares an ERPNext instance for the agent |
| `docs/` | Architecture and how to connect your ERPNext |

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

The contracts use [Arc Foundry](https://github.com/circlefin/arc-foundry) (Linux and macOS; on
Windows, WSL):

```bash
cd contracts
arc-forge test                                                    # unit and fuzz tests
ARC_TESTNET_RPC_URL=https://rpc.testnet.arc.io arc-forge test --network arc   # plus the Arc fork test
```

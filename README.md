# embco

A payables agent you plug into the ERPNext you already run. It reads purchase orders, receipts
and invoices, decides **PAY / HOLD / ASK THE OWNER** with a written reason, and settles in
USDC on Arc through Circle. The model proposes. A smart contract releases the money.

Built for the Tameion Agents Hackathon (Canteen x Circle, on Arc).

> **Status: early, built in public, testnet only.** The agent reads, checks, decides,
> remembers and explains, on its own, against a live ERPNext. With payments turned on, it pays
> the invoices it approved through the shop contract on Arc Testnet, from its Circle wallet.

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
                                explains, pays, every 15 minutes    asks about
                                     |
                                     v
                                Arc: shop contract pays the supplier in USDC, within limits
```

1. **Read** the shop's documents from ERPNext through its REST API, with a limited user.
2. **Check** them: three-way match, duplicates, price anomaly, payment limit, supplier status,
   and the supplier's wallet. A new wallet must be signed for by the supplier, then approved by
   the owner.
3. **Decide** PAY, HOLD or ASK with a fixed rule, and write the reason. A model never decides.
4. **Remember** every decision and every owner answer in an append-only, hash-chained journal,
   so the agent knows what changed and nobody can rewrite the past unnoticed.
5. **Explain**, optionally, each held or asked invoice in plain words, with Claude.
6. **Pay** what it approved, optionally: it simulates the exact call first, sends it through
   Circle with an idempotency key, follows it to a final state and records it. The contract
   refuses anything outside the owner's limits, and each invoice can be paid only once.

## Built on Arc and Circle

- **USDC on Arc.** Suppliers are paid in USDC, straight from the shop owner's wallet.
- **A contract per shop on Arc** (`contracts/`). The owner creates it from their own wallet.
  The agent can only pay suppliers the owner approved, once per invoice, within a per-payment
  and a weekly cap. Only the owner can change those limits, and revoking the contract's
  allowance stops everything.
- **A Circle wallet for the agent.** A developer-controlled EOA wallet signs and sends the
  agent's payments through Circle. It holds only a little USDC for gas; the money it pays
  comes from the owner's wallet, through the contract.
- **Arc's Memo contract.** Every payment carries the ERPNext invoice number on chain, so a
  payment can be matched to its invoice without asking anyone.
- **An owner dashboard** at [app.embco.xyz](https://app.embco.xyz) (`dashboard/`). With
  MetaMask, the owner creates their shop contract, sets the limits and the allowance, approves
  supplier wallets, pauses the agent and sees every payment. The page holds no keys.

| Arc Testnet | Address |
|---|---|
| `ShopPayablesFactory` | [`0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8`](https://explorer.testnet.arc.io/address/0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8) (source verified on [Sourcify](https://repo.sourcify.dev/5042002/0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8)) |
| USDC (ERC-20 interface) | `0x3600000000000000000000000000000000000000` |
| Memo (Arc, predeployed) | `0x5294E9927c3306DcBaDb03fe70b92e01cCede505` |
| Agent wallet (Circle, EOA) | [`0x2309ed4a43e3b1ab4222f6938538a6c88bd27dd8`](https://explorer.testnet.arc.io/address/0x2309ed4a43e3b1ab4222f6938538a6c88bd27dd8) |

## Plug it into your ERPNext

No new system to deploy. The connection is three small changes to your instance: an API user
with a limited role, a wallet field on Supplier, and the two purchasing settings that enforce
the three-way match. See [docs/connect.md](docs/connect.md).

## Repo map

| Path | What is there |
|---|---|
| `agent/` | The agent: reads ERPNext, runs the controls and decides. Code in `agent/src/embco/`, tests in `agent/tests/` |
| `contracts/` | The shop contract and its factory (Solidity, Arc Foundry): the limits the agent cannot move |
| `dashboard/` | The owner's page: a static site with MetaMask, no build step and no server code |
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
uv run embco --env-file ../.env create-wallet   # once: the agent's wallet, with Circle
```

Payments are off by default: the agent decides, remembers and plans. With `EMBCO_PAY=on`, the
shop contract's address in `EMBCO_SHOP_ADDRESS`, the Circle settings and an Arc Testnet node,
it pays the invoices planned for now, in USD only (the contract pays USDC). With `EMBCO_EXPLAIN=on` and an
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

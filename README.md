<div align="center">

<img src="dashboard/brand/logo.svg" width="88" alt="embco logo" />

# embco

**The payables agent for the ERPNext you already run.**

It reads your purchases, decides what to pay and when, pays suppliers in USDC on Arc itself,
and acts on what happened. A smart contract sets the limits it cannot cross.

[**Documentation**](https://jadi03.github.io/embco/) ·
[**Dashboard**](https://app.embco.xyz) ·
[How it works](https://jadi03.github.io/embco/how-it-works/) ·
[Connect your ERPNext](https://jadi03.github.io/embco/connect/)

[![CI](https://github.com/JaDi03/embco/actions/workflows/ci.yml/badge.svg)](https://github.com/JaDi03/embco/actions/workflows/ci.yml)
[![Docs](https://github.com/JaDi03/embco/actions/workflows/docs.yml/badge.svg)](https://jadi03.github.io/embco/)
![Status](https://img.shields.io/badge/status-testnet%20only-orange)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

> Built for the Tameion Agents Hackathon (Canteen x Circle, on Arc). Early and built in public:
> the agent runs against a live ERPNext and pays on Arc Testnet.

## What it does

- **Checks** every invoice against its order and receipt, and looks for duplicates, price jumps
  and unsigned supplier wallets.
- **Decides**, with an AI model: pay now, pay on a date, hold, or ask the owner, each with a
  written reason, among the invoices the checks allow.
- **Pays** itself in USDC, through a shop contract on Arc that holds the owner's limits, then
  reads the result: the transaction, or the contract's or Circle's own error. When something
  stops a payment, it looks up why and tells the owner what only they can fix. Every payment is
  recorded back in ERPNext.
- **Talks** with the owner on the dashboard and on Telegram: questions, instructions, notices.
- **Remembers** every decision and answer in a hash-chained journal that nobody can rewrite
  unnoticed.

Why it exists: [Agents and Ledgers in 2026](https://thecanteenapp.com/analysis/2026/09/12/agents-and-ledgers.html).
How each part works, with diagrams: [the documentation](https://jadi03.github.io/embco/).

## Built with

| Layer | Technology |
|---|---|
| Agent | ![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-journal-003B57?logo=sqlite&logoColor=white) |
| Chain | ![Arc](https://img.shields.io/badge/Arc-Testnet-0f172a) ![USDC](https://img.shields.io/badge/USDC-payments-2775CA) ![Circle](https://img.shields.io/badge/Circle-wallets-1d4ed8) ![Solidity](https://img.shields.io/badge/Solidity-0.8.37-363636?logo=solidity&logoColor=white) |
| ERP | ![ERPNext](https://img.shields.io/badge/ERPNext-v16-0089FF?logo=erpnext&logoColor=white) ![Frappe](https://img.shields.io/badge/Frappe%20Cloud-tested-0089FF?logo=frappe&logoColor=white) |
| Owner apps | ![JavaScript](https://img.shields.io/badge/JavaScript-no%20build-F7DF1E?logo=javascript&logoColor=black) ![ethers](https://img.shields.io/badge/ethers-6.17.0-2535A0?logo=ethers&logoColor=white) ![MetaMask](https://img.shields.io/badge/MetaMask-sign--in-F6851B) ![Telegram](https://img.shields.io/badge/Telegram-bot-26A5E4?logo=telegram&logoColor=white) |
| Tooling | ![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white) ![Ruff](https://img.shields.io/badge/Ruff-D7FF64?logo=ruff&logoColor=black) ![pytest](https://img.shields.io/badge/pytest-0A9EDC?logo=pytest&logoColor=white) ![MkDocs](https://img.shields.io/badge/Material%20for%20MkDocs-526CFE?logo=materialformkdocs&logoColor=white) ![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF?logo=githubactions&logoColor=white) |

## Project structure

```
embco/
├── agent/               the agent: its brain and the checks it cannot skip
│   ├── agent.py         the brain: the AI model decides, pays and acts on the result
│   ├── tools/           what the agent can read and do
│   ├── reflexes/        what wakes it, and the cycle every 15 minutes
│   ├── guardrails/      the controls, the fixed PAY / HOLD / ASK rule and the gate
│   ├── memory/          the hash-chained journal
│   └── explain/         plain-language explanations for the owner
├── services/            what the agent stands on
│   ├── erp/             the ERPNext adapter
│   ├── circle/          Circle wallets and contract calls
│   ├── payments/        simulate on Arc, send, follow, record
│   ├── hub/  shops/     the hosted service behind the dashboard
│   ├── signing/         supplier wallet signatures
│   ├── telegram/        the Telegram bot
│   └── cli.py           the embco command
├── contracts/           ShopPayables and its factory (Solidity, Arc Foundry)
├── dashboard/           the owner dashboard and the supplier page (static, MetaMask)
├── connector/           prepares an ERPNext instance
├── deploy/              install.sh and the server files it sets up
├── docs/                the documentation site (MkDocs)
└── tests/               tests for the agent and the services
```

## Getting started

### Use ours

No installation. Open the [dashboard](https://app.embco.xyz), connect your wallet, create your
shop contract, and follow [Connect your ERPNext](https://jadi03.github.io/embco/connect/).

### Run your own

The same dashboard and agents on your own server (Ubuntu or Debian, with a domain):

```bash
git clone https://github.com/JaDi03/embco.git
cd embco
cp .env.example .env
nano .env                       # your domain, an Arc node URL, Circle and Anthropic keys
sudo bash deploy/install.sh
```

Then shops connect through your dashboard, as they do on ours. Details:
[Run your own server](https://jadi03.github.io/embco/server/).

<details>
<summary>Tests: agent, contracts, dashboard and docs</summary>

```bash
uv run ruff check . && uv run pytest -q
```

The contracts use [Arc Foundry](https://github.com/circlefin/arc-foundry) (Linux and macOS; on
Windows, WSL):

```bash
cd contracts
arc-forge test                                                                 # unit and fuzz tests
ARC_TESTNET_RPC_URL=https://rpc.testnet.arc.io arc-forge test --network arc    # plus the Arc fork test
```

The dashboard has no build step:

```bash
cd dashboard && node --test
```

The documentation site, locally:

```bash
uv run --no-project --with-requirements requirements-docs.txt mkdocs serve
```

</details>

## License

[MIT](LICENSE)

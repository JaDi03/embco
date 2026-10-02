# Embco

[![CI](https://github.com/JaDi03/embco/actions/workflows/ci.yml/badge.svg)](https://github.com/JaDi03/embco/actions/workflows/ci.yml)

Embco is a micro-task marketplace where **an AI agent pays the workers**. Businesses post repetitive work, such as transcribing receipts and invoices, classifying items, moderating content, spotting duplicates, writing short texts or translating. Workers do it from their phones and get paid in USDC on [Arc](https://arc.network).

The agent reviews each submission, decides whether to pay, wait for more answers, reject or escalate to the business, and explains why. It never controls the money on its own:

> **The model's output is an input, never the release condition for money.**
> Releasing funds requires deterministic checks in code plus limits enforced by the contract, and the business's signature above those limits. Every decision is recorded in a signed, hash-chained log, and its hash travels onchain with the payment.

Built for the Tameion Agents Hackathon (Canteen × Circle × Arc).

## Status

Work in progress. What exists today:

| Piece | State |
|---|---|
| Campaign contract (`EmbcoCampaigns`) | Done, tested, deployed on Arc Testnet |
| Agent core: USDC amounts, decision journal, ledger | Done, tested |
| Web app: landing, worker and agency shells, PWA | Done; screens are being filled in |
| Agent decision pipeline, database, wallets, campaign report | In progress |

## How payments are protected

Each campaign is funded by a business and enforces these limits onchain:

1. **Own budget**: a campaign only spends what was deposited into it.
2. **Fixed reward** per accepted submission, set by the business.
3. **Period cap** for the whole campaign.
4. **Per-worker cap** per period.
5. **One submission, one payment**, and one submission per worker per task.
6. **Pause**: the business or the agent can pause, and only the business can resume.
7. The agent can **only tighten** caps 3 and 4.
8. **Escalation**: payments beyond the caps need the business's approval.
9. **Withdrawals** only go back to the business.

Workers register their own submissions onchain, so a payment can only ever go to the person who did the work. The agent decides *whether* and *when* to pay, never *who* gets paid or *how much*.

## Repository

| Folder | Contents |
|---|---|
| [`contracts/`](contracts) | Solidity contract and tests (Foundry) |
| [`frontend/`](frontend) | Next.js app: landing, worker app (`/worker`), agency app (`/agency`), and the agent's server code |

## Deployment

| Network | Contract | Address |
|---|---|---|
| Arc Testnet (chain id `5042002`) | `EmbcoCampaigns` | [`0x73aF5bF164eB25E0c740Ed33cA561d5d6f90F41e`](https://testnet.arcscan.app/address/0x73aF5bF164eB25E0c740Ed33cA561d5d6f90F41e) |

USDC on Arc Testnet: `0x3600000000000000000000000000000000000000` (ERC-20, 6 decimals).

## Development

### Web app

Requires Node.js 24.

```bash
cd frontend
cp .env.example .env.local   # fill in the values
npm install
npm run dev                  # http://localhost:3000
```

| Script | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload (service worker disabled) |
| `npm run build` | Production build |
| `npm start` | Serves the last build |
| `npm run typecheck` | Generates Next route types and runs `tsc` |
| `npm test` | Agent core tests (Node's built-in test runner) |
| `npm run lint` | ESLint |
| `npm run icons` | Regenerates app and PWA icons from `public/brand/*.svg` |

### Contracts

Requires [Foundry](https://getfoundry.sh).

```bash
git submodule update --init   # forge-std
cd contracts
forge test
```

To deploy, copy `.env.example` to `.env` and set `RPC` and a deployer key, then:

```bash
forge script script/Deploy.s.sol --rpc-url arc_testnet --broadcast
```

`RPC` defaults to the public endpoint `https://rpc.testnet.arc.network`. If you use the arc-canteen CLI, it exports `RPC` with your personal endpoint, which takes precedence over `.env`.

## License

[MIT](LICENSE)

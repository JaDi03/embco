# Smart contracts

The shop's limits live on chain, in a contract the agent cannot change. These are the contracts
in `contracts/` (Solidity 0.8.37, Arc Foundry).

## Deployed on Arc Testnet

| Contract | Address |
|---|---|
| `ShopPayablesFactory` | [`0x5D455dF984C85abe160E4585E0c1139ded5d2c3b`](https://explorer.testnet.arc.io/address/0x5D455dF984C85abe160E4585E0c1139ded5d2c3b) (source verified on [Sourcify](https://repo.sourcify.dev/5042002/0x5D455dF984C85abe160E4585E0c1139ded5d2c3b)) |
| USDC (ERC-20 interface) | `0x3600000000000000000000000000000000000000` |
| Memo (predeployed) | `0x5294E9927c3306DcBaDb03fe70b92e01cCede505` |
| Agent wallet (Circle EOA) | [`0x2309ed4a43e3b1ab4222f6938538a6c88bd27dd8`](https://explorer.testnet.arc.io/address/0x2309ed4a43e3b1ab4222f6938538a6c88bd27dd8) |

## Who can do what

<figure class="diagram" markdown>
![Who can call what on the shop contract: only the owner sets limits, approves payees, changes the agent and unpauses; the agent pays within the limits; either can pause](assets/diagrams/who-can-do-what.svg#only-light "Who can do what")
![Who can call what on the shop contract: only the owner sets limits, approves payees, changes the agent and unpauses; the agent pays within the limits; either can pause](assets/diagrams/who-can-do-what-dark.svg#only-dark "Who can do what")
</figure>

The owner can also call `pay`, and the limits do not apply to the owner. Every other caller is
refused with `NotAuthorized`.

## How a payment is accepted

<figure class="diagram" markdown>
![How the contract accepts a payment: it checks the inputs, that the invoice was not paid, and who is calling; for the agent it also checks the pause, the approved payee, the per-payment limit and the weekly cap; then it marks the invoice paid and moves USDC from the owner](assets/diagrams/how-a-payment-is-accepted.svg#only-light "How a payment is accepted")
![How the contract accepts a payment: it checks the inputs, that the invoice was not paid, and who is calling; for the agent it also checks the pause, the approved payee, the per-payment limit and the weekly cap; then it marks the invoice paid and moves USDC from the owner](assets/diagrams/how-a-payment-is-accepted-dark.svg#only-dark "How a payment is accepted")
</figure>

Each `invoiceRef` is a unique reference for the invoice (the contract documents a hash of its ERP
name as one example), so it can be paid once and matched to its invoice. The transfer uses `transferFrom`: the money stays in the owner's wallet until the call
succeeds, and revoking the USDC allowance stops every payment.

## Limits

- `maxPerPayment` is greater than zero and no more than `weeklyCap`.
- The weekly window starts when the shop is created and runs in seven-day blocks.
- Only the owner changes limits. Every change emits `LimitsChanged`.

## Events

| Event | Emitted when |
|---|---|
| `Paid` | An invoice is paid, with its reference, payee, amount and caller |
| `LimitsChanged` | The owner changes the per-payment or weekly limit |
| `AgentChanged` | The owner sets the agent's address |
| `PayeeSet` | The owner approves or removes a supplier wallet |
| `Paused` / `Unpaused` | The agent or owner pauses; only the owner resumes |

The dashboard reads these events from the chain to show suppliers and payments.

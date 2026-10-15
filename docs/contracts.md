# Smart contracts

The shop's limits live on chain, in a contract the agent cannot change. These are the contracts
in `contracts/` (Solidity 0.8.37, Arc Foundry).

## Deployed on Arc Testnet

| Contract | Address |
|---|---|
| `ShopPayablesFactory` | [`0x5D455dF984C85abe160E4585E0c1139ded5d2c3b`](https://explorer.testnet.arc.io/address/0x5D455dF984C85abe160E4585E0c1139ded5d2c3b) (source verified on [Sourcify](https://repo.sourcify.dev/5042002/0x5D455dF984C85abe160E4585E0c1139ded5d2c3b)) |
| USDC (ERC-20 interface) | `0x3600000000000000000000000000000000000000` |
| Memo (predeployed) | `0x5294E9927c3306DcBaDb03fe70b92e01cCede505` |
| Pilot shop's agent wallet (Circle EOA, from before shops got their own gasless agent) | [`0x2309ed4a43e3b1ab4222f6938538a6c88bd27dd8`](https://explorer.testnet.arc.io/address/0x2309ed4a43e3b1ab4222f6938538a6c88bd27dd8) |

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

## ShopFunding: the owner's USDC on other chains

| Chain | `ShopFundingFactory` |
|---|---|
| Base Sepolia | [`0x69e31Af302Ee222204AA78099D39a2956F5C95d6`](https://sepolia.basescan.org/address/0x69e31Af302Ee222204AA78099D39a2956F5C95d6) |
| Ethereum Sepolia | [`0x69e31Af302Ee222204AA78099D39a2956F5C95d6`](https://sepolia.etherscan.io/address/0x69e31Af302Ee222204AA78099D39a2956F5C95d6) |

`ShopFunding` is the same idea on a source chain of the owner's Unified Balance (Circle
Gateway). Each owner creates one from `ShopFundingFactory` on that chain and approves it, the way
they approve the shop contract on Arc.

| Who | Can do | Cannot do |
|---|---|---|
| Agent | `fund(amount)`: take that much of the owner's USDC and deposit it into the owner's own Gateway balance, within a per-move and a weekly cap; pause | Send it anywhere else, change a limit, resume after a pause |
| Owner | Everything the agent can, with no limit; set the limits and the agent; resume | Transfer ownership |

The money never stays in the contract: `fund` takes it from the owner's wallet and calls
Gateway's `depositFor(usdc, owner, amount)` in the same transaction, so the deposit always belongs
to the owner. From there the agent, which the owner authorizes as a delegate in Gateway, brings it
to the owner's wallet on Arc. Unit and fuzz tests, plus a fork test against Base Sepolia's real
GatewayWallet and USDC.

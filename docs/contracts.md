# Smart contracts

The shop's limits live on chain, in a contract the agent cannot change. These are the contracts
in `contracts/` (Solidity 0.8.37, Arc Foundry).

## How the contracts fit together

<figure class="diagram" markdown>
![How the contracts fit together: factories deploy one ShopPayables, ShopEarn and ShopFunding per owner; solid arrows move USDC from the owner to a supplier, a vault or Gateway; dashed arrows only deploy or read; JournalAnchor and DecisionVerifier never touch USDC](assets/diagrams/contracts-overview.svg#only-light "How the contracts fit together")
![How the contracts fit together: factories deploy one ShopPayables, ShopEarn and ShopFunding per owner; solid arrows move USDC from the owner to a supplier, a vault or Gateway; dashed arrows only deploy or read; JournalAnchor and DecisionVerifier never touch USDC](assets/diagrams/contracts-overview-dark.svg#only-dark "How the contracts fit together")
</figure>

Solid arrows move USDC. Dashed arrows only deploy a contract or read data.

No contract keeps a balance. Each one that moves USDC sends it on in the same call:

| Contract | Moves USDC | Where it ends |
|---|---|---|
| `ShopPayables` | `pay`: `transferFrom(owner, payee, amount)` | The supplier's wallet |
| `ShopEarn` | `park`: takes it from the owner and deposits it in the vault | Vault shares in the owner's wallet |
| `ShopEarn` | `unpark`, `unparkAll`: the vault pays out | The owner's wallet |
| `ShopFunding` | `fund`: takes it from the owner and calls Gateway's `depositFor` | The owner's Gateway balance |
| Factories, `JournalAnchor`, `DecisionVerifier` | Never | |

Each of them takes USDC only through the allowance the owner gave it. Revoking that allowance
stops every `pay`, `park` and `fund` at once.

## Deployed on Arc Testnet

The server installed with `deploy/install.sh` automatically discovers `ShopPayables` and
`ShopEarn` contracts on Arc, and `ShopFunding` on Base Sepolia and Ethereum Sepolia. It submits
their sources to [Sourcify](https://repo.sourcify.dev) and requires an exact creation and runtime
match. A contract counts as verified once Sourcify confirms that match. The worker then asks each
chain's Blockscout explorer once to import it; an explorer error never keeps a contract pending.
No extra signature, fee or action is required from the owner.

Verification happens in the background after 12 blocks. The first run also scans older
creations of the configured factories in bounded ranges; catching up can take several rounds.
The worker uses the original verified factory compilation, not today's source files, and
keeps its compilation, discovery cursor and pending jobs across restarts. Errors are retried
with backoff. Verification delays do not stop the dashboard or the agent.

Old event ranges can use the explorer's paginated index when public nodes have pruned history;
the last 1,000 blocks use the node to avoid advancing past events not indexed yet. If neither
source can serve a range, its cursor stays pending. Provider range limits are reduced rather
than skipping blocks.

The explorers asked to import are Arc's explorer, Base Sepolia Blockscout and Ethereum Sepolia
Blockscout. Sourcify may also forward results to other explorers; an explorer, Etherscan or
Basescan listing is not guaranteed by this worker. Third-party vaults, USDC and contracts from previous
factories are outside its discovery scope. See [server operations](server.md#contract-verification)
for status and troubleshooting.

| Contract | Address |
|---|---|
| `ShopPayablesFactory` | [`0x5D455dF984C85abe160E4585E0c1139ded5d2c3b`](https://explorer.testnet.arc.io/address/0x5D455dF984C85abe160E4585E0c1139ded5d2c3b) (source verified on [Sourcify](https://repo.sourcify.dev/5042002/0x5D455dF984C85abe160E4585E0c1139ded5d2c3b)) |
| `ShopEarnFactory` | [`0xEDF589982F10CCC3c8489726FaB39124b1F683Aa`](https://explorer.testnet.arc.io/address/0xEDF589982F10CCC3c8489726FaB39124b1F683Aa) |
| `JournalAnchor` | [`0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582`](https://explorer.testnet.arc.io/address/0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582) |
| `DecisionVerifier` | [`0xE9ca18fa99251555DAbed0cE1414b7157B9aD516`](https://explorer.testnet.arc.io/address/0xE9ca18fa99251555DAbed0cE1414b7157B9aD516?tab=read_contract) (source verified on [Sourcify](https://repo.sourcify.dev/5042002/0xE9ca18fa99251555DAbed0cE1414b7157B9aD516)) |
| Morpho USDC Vault (ERC-4626, not embco's) | [`0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc`](https://explorer.testnet.arc.io/address/0x8f2d33b5d4b9b5f02df635ae308f7b4c9da8d2dc) |
| EarnKit USDC Vault (ERC-4626, not embco's; deposits closed) | [`0xaabbef1d3971c710276ed41ec791bbe14cdb8e88`](https://explorer.testnet.arc.io/address/0xaabbef1d3971c710276ed41ec791bbe14cdb8e88) |
| USDC (ERC-20 interface) | `0x3600000000000000000000000000000000000000` |
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
- The blocks are fixed, not rolling: the agent can use a full `weeklyCap` at the end of one block
  and another at the start of the next, so up to twice `weeklyCap` can leave within a few minutes.
  Choose `weeklyCap` with that in mind.
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

## ShopEarn: idle USDC in vaults

Each owner creates one ShopEarn from `ShopEarnFactory`, approves the vaults the agent may use and
sets a per-park limit and a weekly cap.

| Who | Can do | Cannot do |
|---|---|---|
| Agent | `park`: move the owner's USDC into an approved vault, within the per-park limit and the weekly cap; `unpark` / `unparkAll`: bring it back to the owner's wallet at any time; pause | Use a vault the owner did not approve, change a limit, send USDC or shares anywhere but the owner |
| Owner | Everything the agent can, with no limit; approve vaults, set the limits and the agent; resume | Transfer ownership |

Neither USDC nor vault shares stay in ShopEarn: `park` deposits with the owner as the receiver, so
the shares go to the owner's wallet, and `unpark` redeems them back to the owner. Taking money back
does not use the weekly cap, so the agent can always recall funds to pay a bill. Every move emits
`Parked` or `Unparked`, which the dashboard reads for its Earn card.

## JournalAnchor: the journal, checkable by anyone

After money moves, and at least once a day, the agent writes the Merkle root (RFC 9162) of its
whole journal and how many entries it covers.

| Who | Can do | Cannot do |
|---|---|---|
| The agent the shop contract names | `anchor(shop, root, size)`, with a size larger than the last one | Change or remove an anchor, anchor a smaller journal |
| Anyone | Read `latest`, `count` and `anchorAt` for any shop | Anchor for a shop that does not name them as its agent |

The contract has no owner. `embco verify` recomputes the root from a copy of the journal and
compares it with every anchor: a changed, removed or reordered entry gives a different root.

## DecisionVerifier: one decision, checked by Arc

| Who | Can do | Cannot do |
|---|---|---|
| Anyone | `verify(proof)`: a decision's `MANIFEST` entry, its audit path and its anchor, as one value | Change anything: it has no owner, no state and no write function |

`verify` hashes the entry as the journal does, follows the RFC 9162 audit path and compares the
root with the anchor the shop's agent wrote in `JournalAnchor`. Only if it matches does it read the
decision out of the entry and return it, with the shop's `owner()`. The entry is read at fixed
places, never as general JSON, and free text arrives as hex, so a record of any other shape is
refused. See [Audit the agent](audit.md#4-prove-one-decision).

## ShopFunding: the owner's USDC on other chains

| Chain | `ShopFundingFactory` |
|---|---|
| Base Sepolia | [`0x69e31Af302Ee222204AA78099D39a2956F5C95d6`](https://sepolia.basescan.org/address/0x69e31Af302Ee222204AA78099D39a2956F5C95d6) |
| Ethereum Sepolia | [`0x69e31Af302Ee222204AA78099D39a2956F5C95d6`](https://sepolia.etherscan.io/address/0x69e31Af302Ee222204AA78099D39a2956F5C95d6) |

Both factories are verified on Sourcify for each chain
([Base Sepolia](https://repo.sourcify.dev/84532/0x69e31Af302Ee222204AA78099D39a2956F5C95d6),
[Ethereum Sepolia](https://repo.sourcify.dev/11155111/0x69e31Af302Ee222204AA78099D39a2956F5C95d6)).

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

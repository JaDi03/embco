# Circle and Arc tools

What is used, where, and why. Status is honest: **Planned** means scheduled before 2026-10-17,
**Roadmap** means a later stage, after the hackathon.

| Tool | Use in embco | Where | Status |
|---|---|---|---|
| Arc (USDC settlement) | Payments to suppliers and contractors | `agent/src/embco/payments/` | Planned |
| Circle Wallets | One wallet for the agent, one per payee registered by signed message | `agent/src/embco/payments/` | Planned |
| Contracts | Budget, per-payment and daily caps, approval threshold, pause, separation of roles | `contracts/` | Planned |
| Paymaster | Pay gas in USDC so payees need no native token | `agent/src/embco/payments/` | Planned if time allows |
| Arc CLI and Circle CLI | Wallet setup, traction and product updates | n/a | In use |
| Gateway and x402 | The agent pays for data services per call | Stage 1 | Roadmap |
| USYC | Idle cash with low caps and approval. Real access is limited to eligible institutions outside the US with a minimum investment | Stage 2 | Roadmap, may stay out of scope |
| StableFX | Permissioned (KYB and AML), USDC and EURC only today. The naira is not on its list, so it is not used | n/a | Not used |
| EURC and swaps | Suppliers that invoice in euros | Stage 2 | Roadmap |
| CCTP and Unified Balance | Paying a supplier on another chain from one balance | Stage 2 | Roadmap |

# Connect embco to your ERPNext

embco runs as its own service next to your ERPNext. You do not deploy a new system and you do
not give it admin rights. The connection contract is four changes:

1. **An API user with a limited role.** It can read suppliers, purchase orders, purchase
   receipts and purchase invoices, and create payment entries. Nothing else.
2. **A wallet field on Supplier** (`custom_wallet_address`) and a transaction hash field on
   Payment Entry. Created through Customize Form; no code.
3. **A webhook** from ERPNext to the agent when a purchase invoice is submitted, or a periodic
   poll if you prefer not to open a route.
4. **Two settings in Buying Settings**: purchase order required and purchase receipt required.
   They default to No in ERPNext. Turning them on is what makes the three-way match enforceable.

`connector/prereqs.py` applies the custom fields and the two settings, and can be run twice
safely. The adapter in `agent/src/embco/ledger/erpnext/` currently only reads.

Target: ERPNext v16 (developed against v16.37.0). The adapter is unit-tested and has been run
against a live instance with a limited API user (roles: Purchase User, Stock User, Accounts
User). Compatibility with v15 is planned.

Credentials go in a local `.env` (see `.env.example`), never in the repository.

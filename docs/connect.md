# Connect embco to your ERPNext

embco runs as its own service next to your ERPNext. You do not deploy a new system and you do
not give it admin rights. The connection is three changes:

1. **An API user with a limited role** (roles: Purchase User, Stock User, Accounts User). The
   agent reads suppliers, purchase orders, purchase receipts, purchase invoices and
   payment entries. Its one write: when it pays, it submits the Payment Entry for that invoice,
   with the Arc transaction hash. `EMBCO_ERPNEXT_PAID_FROM` names the account the money leaves
   from (for example a "USDC Wallet" bank account); without it, ERPNext's default is used.
2. **A wallet field on Supplier** (`custom_wallet_address`) and payee and transaction fields on
   Payment Entry. Created through Customize Form; no code.
3. **Two settings in Buying Settings**: purchase order required and purchase receipt required.
   They default to No in ERPNext. Turning them on is what makes the three-way match enforceable.

`connector/prereqs.py` applies the custom fields and the two settings, and can be run twice
safely. The agent checks ERPNext on its own every `EMBCO_INTERVAL_MINUTES` (15 by default);
nothing needs to be opened on your side.

Tested with ERPNext v16 (v16.37.0): unit-tested, and run on its own against a live instance
with a limited API user.

Supplier wallets are entered once, in ERPNext. Only roles that can edit Supplier (Purchase
Manager, Purchase Master Manager) can change them; give everyday staff Purchase User. A new or
changed wallet is held until the supplier signs for it and the owner approves it, and the shop
contract still pays only wallets the owner approved with their own signature: the dashboard
lists the ones the agent is waiting to pay. The agent requires an `https://` ERPNext URL
(plain `http://` only to the same machine).

Credentials go in a local `.env` (see `.env.example`), never in the repository.

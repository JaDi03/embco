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
4. **An owner answer on Purchase Invoice** (`custom_owner_answer`: Approve or Reject, plus a
   note), at a permission level only System Manager can write and Accounts User can read. When
   the agent asks about an invoice, the owner answers there. The answer counts only when the
   ERP's change history shows it was written by an account in `EMBCO_OWNER_USERS`, for the
   question the agent last showed, and once: a new question needs the answer written again.

`connector/prereqs.py` applies the custom fields, the permissions and the two settings, and can
be run twice safely. Adding the permissions turns the doctype's standard rules into custom
ones (same rules, plus the new level). The agent checks ERPNext on its own every `EMBCO_INTERVAL_MINUTES` (15 by default);
nothing needs to be opened on your side.

Tested with ERPNext v16 (v16.37.0): unit-tested, and run on its own against a live instance
with a limited API user.

Supplier wallets are entered once, in ERPNext. Only roles that can edit Supplier (Purchase
Manager, Purchase Master Manager) can change them; give everyday staff Purchase User. A new or
changed wallet is held until the supplier signs for it and the owner approves it, and the shop
contract still pays only wallets the owner approved with their own signature: the dashboard
lists the ones the agent is waiting to pay, and approving one there also answers the agent's
question about it, so the first payment needs one approval. The agent requires an `https://`
ERPNext URL (plain `http://` only to the same machine).

## Hosted ERPNext, without custom fields

On a hosted ERPNext (for example Frappe Cloud) the shop may not want custom fields. Set
`EMBCO_ERPNEXT_WALLET_BANK` and the agent uses standard records only:

- **Wallets**: create a Bank with that name (for example `USDC on Arc`) and, for each supplier,
  one Bank Account at that bank with the wallet as Bank Account No. The supplier's real bank
  account and its default stay as they are. Bank Account No holds 30 characters by default and
  a wallet has 42: raise its Length to 64 once in Customize Form (Bank Account). Two active
  accounts with different wallets hold the invoice: the agent does not choose between them.
  Who set a wallet comes from the account's change history, or from who created it.
- **Payments**: the transaction hash goes in Reference No, and the wallet paid in the remarks,
  which ERPNext keeps unchanged once the entry is submitted.
- **Required fields**: if your ERP requires extra fields on a Payment Entry (for example a
  local payment form), set them in `EMBCO_ERPNEXT_PAYMENT_EXTRA` as JSON. They cannot change
  the amount, the party, the invoice or the hash.

The limited API user and the Buying Settings are the same as above. The owner answers the
agent's questions with `embco answer` or, for a new wallet, in the dashboard.

Tested on Frappe Cloud with ERPNext v16.50.0: the agent read a shop's purchases and submitted a
Payment Entry with a limited API user. A site with an app that blocks supplier payments (seen
with a Mexican e-invoicing app) cannot submit them by hand either; fix that on the ERP first.

Credentials go in a local `.env` (see `.env.example`), never in the repository.

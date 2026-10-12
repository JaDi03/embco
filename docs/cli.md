# Command line

Every command reads its settings from a `.env` file (or `--env-file PATH`). Secrets are never
printed. Install with `uv sync`, then run `uv run embco <command>`.

```bash
uv run embco --env-file .env <command>
```

## Run the agent

| Command | What it does |
|---|---|
| `run` | One cycle, then the full report |
| `watch [--cycles N]` | A cycle every `EMBCO_INTERVAL_MINUTES` (15 by default), until stopped or N cycles |
| `verify` | Checks that the journal was not altered |
| `history INVOICE` | Every decision taken on one invoice, in order |

## Owner and suppliers

| Command | What it does |
|---|---|
| `answer INVOICE approve\|reject [--by NAME] [--note TEXT]` | Answers a question the agent asked. Counts once, for the question shown last |
| `challenge SUPPLIER` | Prints the message the supplier must sign with their wallet |
| `sign-wallet SUPPLIER SIGNATURE` | Submits the supplier's signature for a new wallet |

## Setup and services

| Command | What it does |
|---|---|
| `create-wallet` | Creates the agent's paying wallet with Circle. Run once |
| `hub` | Serves the API the dashboard uses to connect shops |
| `shop --dir ROOT/ADDRESS [--key-file PATH] [--cycles N]` | Runs the hosted agent of one shop from its folder |
| `telegram` | Runs the Telegram bot: `/status`, `/pause`, `/stop`, and messages to the agent |

## Example

```bash
uv run embco --env-file .env run
uv run embco --env-file .env answer ACC-PINV-0001 approve --by owner --note "checked the delivery"
uv run embco --env-file .env history ACC-PINV-0001
```

`/stop` in Telegram also pauses the shop contract from the agent's wallet. Only the dashboard
turns the agent back on.

## Run it yourself: the ERPNext side

Shops that connect through the dashboard follow [Connect your ERPNext](connect.md). Running
embco from the command line against your own ERPNext needs the same API user and Buying
Settings, plus these settings in `.env` (see `.env.example`; never commit it):

| Setting | What it does |
|---|---|
| `EMBCO_ERPNEXT_URL`, `EMBCO_ERPNEXT_API_KEY`, `EMBCO_ERPNEXT_API_SECRET` | The ERP and the agent user's keys. The URL must be `https://` (plain `http://` only to the same machine) |
| `EMBCO_ERPNEXT_PAID_FROM` | The account the money leaves from (for example a "USDC Wallet" bank account); without it, ERPNext's default is used |
| `EMBCO_ERPNEXT_WALLET_BANK` | Read supplier wallets from Bank Accounts at this Bank (standard records, as in the dashboard flow) |
| `EMBCO_ERPNEXT_PAYMENT_EXTRA` | Extra Payment Entry fields your ERP requires, as JSON. They cannot change the amount, the party, the invoice or the hash |
| `EMBCO_OWNER_USERS` | ERP accounts whose answer on the invoice counts as the owner's |

Without `EMBCO_ERPNEXT_WALLET_BANK`, the agent uses custom fields instead:

- **A wallet field on Supplier** (`custom_wallet_address`) and payee and transaction fields on
  Payment Entry.
- **An owner answer on Purchase Invoice** (`custom_owner_answer`: Approve or Reject, plus a
  note), at a permission level only System Manager can write and Accounts User can read. The
  answer counts only when the ERP's change history shows it was written by an account in
  `EMBCO_OWNER_USERS`, for the question the agent last showed, and once.

`connector/prereqs.py` applies the custom fields, the permissions and the two Buying Settings,
and can be run twice safely. Adding the permissions turns the doctype's standard rules into
custom ones (same rules, plus the new level).

Tested with ERPNext v16.37.0 (custom fields) and on Frappe Cloud with v16.50.0 (standard
records), with a limited API user.

# Connect your ERPNext

This guide is for the person who runs the shop's ERPNext. You will make four small changes in
ERPNext and then paste two keys into the embco dashboard. Nothing is installed on your ERPNext,
and the agent never gets admin rights.

!!! info "Before you start"
    - Your shop is already created in the [dashboard](https://app.embco.xyz): your wallet is
      connected and your shop contract exists (create it in **Settings**). Creating it asks for
      one free signature and one transaction, and the shop comes with its own agent: you never
      set it up or send it gas.
    - You can sign in to ERPNext as an administrator (System Manager) to make the changes below.
    - Your ERPNext opens with an `https://` address. embco is tested with ERPNext v16, including
      Frappe Cloud.

## Step 1: Create a user for the agent

The agent signs in to ERPNext as its own user, with only the roles it needs.

1. In ERPNext, create a new **User** for the agent (for example `agent@your-shop.com`).
2. Give it these three roles, and no others:
    - **Accounts User**
    - **Purchase User**
    - **Stock User**
3. Open that user and, under **API Access**, generate its keys. ERPNext shows an **API Key** and
   an **API Secret**. Copy both now: the secret is shown only once.

With these roles the agent can read suppliers, purchase orders, receipts and invoices. The only
thing it writes is the Payment Entry for an invoice it paid.

## Step 2: Require an order and a receipt for every invoice

This is what lets the agent check that what you were billed is what you ordered and received.

1. Open **Buying Settings**.
2. Set the option that requires a **Purchase Order** to **Yes**.
3. Set the option that requires a **Purchase Receipt** to **Yes**.
4. Save.

Both are **No** in a new ERPNext.

## Step 3: Make room for wallet addresses

A wallet address is 42 characters long, and ERPNext's bank account number field holds 30. You
change this once:

1. Open **Customize Form** and choose **Bank Account**.
2. Find the field **Bank Account No** and set its **Length** to **64**.
3. Save.

## Step 4: Add your suppliers' wallets

Each supplier's wallet is saved as a bank account at a bank called **USDC on Arc**. The
supplier's real bank account stays as it is.

1. Create a **Bank** named `USDC on Arc` (once).
2. For each supplier you will pay with embco, create a **Bank Account**:
    - **Bank**: `USDC on Arc`
    - **Party Type**: Supplier
    - **Party**: the supplier
    - **Bank Account No**: the supplier's wallet address (starts with `0x`)

!!! warning "One wallet per supplier"
    If a supplier has two active accounts at `USDC on Arc` with different wallets, the agent
    holds its invoices and does not choose between them.

A new or changed wallet is never paid right away. The supplier first confirms it is theirs, and
then you approve it in the dashboard (see [What happens next](#what-happens-next)).

## Step 5: Connect in the dashboard

1. Open [app.embco.xyz](https://app.embco.xyz) and **Sign in** with your wallet, if you did
   not when you created the shop. It is a signature, not a transaction: it costs nothing.
2. In **Your ERPNext**, fill in:

    | Field | What to write |
    |---|---|
    | ERPNext address | The address you open ERPNext with, for example `https://your-shop.frappe.cloud` |
    | Company | Your company's name exactly as it is written in ERPNext |
    | API key | The key from Step 1 |
    | API secret | The secret from Step 1 |
    | Bank that holds supplier wallets | `USDC on Arc` (already filled in) |
    | Extra payment fields | Leave it empty, unless your ERPNext asks for extra fields when you submit a payment by hand (some local tax setups do). Then ask whoever set up your ERPNext which fields and values to write |

3. Click **Test and connect**. The dashboard tries the keys without changing anything in
   ERPNext, and shows each check:

    | Check | What it means |
    |---|---|
    | Keys | ERPNext accepted the agent's key (required) |
    | Invoices | The agent can read your unpaid purchase invoices (required) |
    | Company | The company name was found |
    | Payments | ERPNext can draft a payment for an invoice (nothing is saved) |
    | Wallets | How many supplier wallets were found at `USDC on Arc` |

4. A shop created before its agent came with it shows **Make it the agent**: click it once. This
   lets your shop's agent wallet pay approved suppliers, within the limits you set.

## What happens next

- **The agent checks ERPNext on its own** every 15 minutes. You do not need to open anything.
- **A new supplier wallet** is confirmed by the supplier at `app.embco.xyz/supplier`, with a
  signature from that wallet. Then it appears in the dashboard under **Waiting for your
  approval**. Before approving, check in ERPNext that the wallet belongs to the supplier you
  expect.
- **When the agent pays**, it records a Payment Entry in ERPNext for that invoice. The
  transaction hash is in **Reference No**, and the wallet paid is in the remarks.
- **USDC on other chains (optional):** in **Settings > USDC on other chains**, pick a chain (Base
  Sepolia or Ethereum Sepolia), set how much the agent may move at once and per week, and confirm
  in your wallet: your funding contract on that chain, its approval, and the agent's authorization
  in your Unified Balance (Circle Gateway). A wallet that batches calls (MetaMask with its smart
  account, EIP-5792) asks once; otherwise once per step. Your USDC stays in your wallet there.
  When an invoice needs more than your wallet on Arc holds, the agent can move it into your
  Unified Balance and bring it to your wallet on Arc, choosing which chains it comes from. It
  never sends it anywhere else, and your limits still apply. Your wallet needs that chain's gas
  token for this setup (test ETH on Base Sepolia and Ethereum Sepolia). The agent needs none: its
  network fees are paid by Circle Gas Station.
- **Telegram (optional):** in the dashboard, **Connect Telegram** gives a code to send to the
  embco bot. Then `/status` says how the agent is, `/pause` pauses payments while the agent keeps
  watching, and `/stop` turns the agent off and pauses the shop contract. Only the dashboard
  turns it back on. Any other message goes to your agent.

## Draft mode

While embco runs on testnet, payments are made in test USDC. So that test USDC never closes a
real invoice, each Payment Entry stays as a **draft** whose remarks start with "TESTNET". This
is **draft mode**, and it is on for every new shop.

- The dashboard shows the mode next to the agent's status: **Draft mode** or **Submitting to
  ERPNext**.
- To change it, open **Settings > ERPNext**. Turning draft mode off asks you to confirm a second
  time: from then on each payment is submitted and closes its invoice, even when it was paid in
  test USDC, and your books show those invoices as paid.
- Turning it back on takes one click. Each change is written in **Agent activity**, and if the
  agent is on, it restarts with the new mode.

## If something does not work

| Problem | What to do |
|---|---|
| Keys check fails | Check the address starts with `https://`, and generate the keys again (Step 1) |
| Invoices check fails | Check the agent's user has the three roles from Step 1 |
| Payments check fails | Try to submit a payment to a supplier by hand in ERPNext. If you cannot either, an app or a required field is blocking it (seen with a Mexican e-invoicing app). Fix that in ERPNext first, or use **Extra payment fields** |
| Wallets check finds 0 | Check the Bank is named exactly `USDC on Arc` and each Bank Account has Party Type Supplier |

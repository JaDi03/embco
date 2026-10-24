"""The tools the agent can call, as the Claude API receives them. Strict: inputs always match.

Order and text are fixed so the request prefix stays the same and is served from the cache.
"""

from typing import Any

MAX_STRICT_TOOLS = 20  # the Claude API refuses a request with more strict tools than this


def _tool(name: str, description: str, **properties: dict[str, Any]) -> dict[str, Any]:
    """Strict when it takes more than one input: strict checks the inputs, and the API allows at
    most MAX_STRICT_TOOLS strict tools per request."""
    return {
        "name": name,
        "description": description,
        "strict": len(properties) > 1,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _text(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


INVOICE = _text("The invoice name exactly as listed, e.g. ACC-PINV-2026-00012.")
REASON = _text("Why, in one or two plain sentences the owner will read.")
ALLOCATIONS = _text("How much USDC from each chain, as chain:amount separated by commas, e.g. "
                    "Base_Sepolia:120, Arbitrum_Sepolia:30.5. Chain names as cash_position "
                    "lists them.")

TOOLS: list[dict[str, Any]] = [
    _tool("list_open_invoices",
          "Every unpaid invoice with what the checks say, its problems and your current "
          "decision on it."),
    _tool("search_invoices",
          "Find open invoices by supplier, due date or what the checks say, without reading them "
          "all. Leave a filter empty to not filter by it. Returns at most 50, with their count and "
          "total.",
          supplier=_text("Part of the supplier name, any case, or empty."),
          due_from=_text("Earliest due date, YYYY-MM-DD, or empty."),
          due_to=_text("Latest due date, YYYY-MM-DD, or empty."),
          checks_say=_text("PAY, HOLD or ASK, or empty for all.")),
    _tool("get_invoice",
          "One invoice in full: lines, linked order and receipt, every check, the owner's "
          "answer, the payment state and the history of decisions on it.",
          invoice=INVOICE),
    _tool("supplier_profile",
          "A supplier: its wallet, whether it was proven by signature and whether the owner "
          "approved it in the contract, whether the ERP has an email for it and the shop sends "
          "email, when you last sent it the wallet notice, its recent invoices, its recent "
          "payments, and your notes about it. Check it before you offer to write to a supplier.",
          supplier=_text("The supplier name exactly as listed.")),
    _tool("price_history",
          "Rates a supplier charged for one item over time, from its invoices, oldest first.",
          supplier=_text("The supplier name exactly as listed."),
          item_code=_text("The item code exactly as on the invoice line.")),
    _tool("cash_position",
          "The shop contract's limits, how much it still lets the shop pay this week, what you "
          "chose to pay now in this session, the payments scheduled or due soon, and the USDC "
          "the owner keeps on other chains: in their Unified Balance (with where you may spend "
          "it) and in their own wallets (with how much you may deposit into it)."),
    _tool("pay_now",
          "Pay this invoice now, for real: the payment goes out right away from the owner's "
          "wallet through the shop contract (simulated first, then sent with your Circle "
          "wallet) and is written in the ERP once final. Only invoices the checks allow (PAY). "
          "Returns what happened: paid with its transaction, sent and not confirmed yet, or an "
          "error with the real reason (the contract's refusal, Circle's own message). After an "
          "error, find out why (agent_wallet, cash_position, get_invoice) and tell the owner "
          "what to do if only they can fix it.",
          invoice=INVOICE, reason=REASON),
    _tool("payment_status",
          "The current state of your payment of an invoice: sent, paid on Arc, recorded in the "
          "ERP or failed, with its transaction and the reason when it failed.",
          invoice=INVOICE),
    _tool("agent_wallet",
          "Your own wallet: its address, its balance for Arc network fees (paid in USDC), "
          "whether the shop contract has it as its agent, and whether the contract is paused."),
    _tool("deposit_to_gateway",
          "Move the owner's USDC from their own wallet on another chain into their Unified "
          "Balance (Circle Gateway), for real, through the owner's funding contract there: it "
          "only ever deposits for the owner, within the limits the owner set. cash_position "
          "says how much you may move on each chain. On Base Sepolia and Ethereum Sepolia the "
          "deposit is spendable only once the chain is final (15 minutes or more): set an "
          "alarm, then bring_funds. Returns the transaction, or the real error.",
          chain=_text("The chain as cash_position lists it, e.g. Base_Sepolia."),
          amount=_text("USDC to move, e.g. 120 or 30.5."), reason=REASON),
    _tool("estimate_bring_funds",
          "What it would cost to bring USDC from the owner's Unified Balance on other chains to "
          "the owner's wallet on Arc, with the split you give: the fees, what each chain's "
          "balance gives up (the amount plus that chain's gas and protocol fee, which come out "
          "of the same balance: it must fit in what is available there) and what would arrive. "
          "Nothing moves.",
          allocations=ALLOCATIONS),
    _tool("bring_funds",
          "Bring USDC from the owner's Unified Balance on other chains to the owner's own wallet "
          "on Arc, for real, so the shop contract can pay from it. You choose how much comes "
          "from each chain; it always arrives at the owner's wallet, never anywhere else. Only "
          "chains where the owner authorized you. Each chain's gas and protocol fee come out of "
          "its balance on top of the amount, so leave room for them (estimate first). Returns "
          "what arrived, the fees and the transaction, or the real error.",
          allocations=ALLOCATIONS, reason=REASON),
    _tool("earn_plan",
          "Everything to decide whether idle USDC should go to Earn, worked out with real "
          "numbers: each approved vault's APY (from Circle), what is kept for bills due within "
          "7 days and for Arc fees, what you may park this week, and for each place idle money "
          "sits (the owner's wallet on Arc, their Gateway balance and wallets on other chains): "
          "how much can go to Earn now, the real fee to bring it (Gateway's estimate), what it "
          "earns per day, how many days until it pays the fee back, and the steps. Read only: "
          "nothing moves. Call it before any decision about Earn, and never quote a fee or a "
          "yield it did not give you."),
    _tool("earn_position",
          "The owner's USDC in Arc Earn vaults (Morpho and EarnKit) and their ShopEarn contract "
          "limits: how much is in each vault, what you may withdraw back to the owner's wallet "
          "on Arc now, and how much idle USDC you may park into an approved vault now."),
    _tool("park_in_earn",
          "Park idle USDC from the owner's wallet on Arc into an approved Arc Earn vault through "
          "the owner's ShopEarn contract: shares go straight to the owner's wallet, within the "
          "per-park and weekly limits the owner set, leaving 1 USDC in the wallet for Arc fees.",
          vault=_text("The vault key: morpho or earnkit."),
          amount=_text("USDC to park, e.g. 200 or 50.5."), reason=REASON),
    _tool("withdraw_from_earn",
          "Withdraw the owner's USDC from an Arc Earn vault back to the owner's wallet on Arc "
          "through ShopEarn, for real and with no cross-chain fee, so the shop contract can pay "
          "invoices from it. Use 'all' to redeem the full vault balance.",
          vault=_text("The vault key: morpho or earnkit."),
          amount=_text("USDC to withdraw, e.g. 150, or 'all' for the whole vault balance."),
          reason=REASON),
    _tool("schedule_payment",
          "Plan to pay this invoice on a later date you choose (shop's local date). Only "
          "invoices the checks allow. On that day you are woken to pay it with pay_now, or to "
          "decide again with what you see then.",
          invoice=INVOICE, pay_on=_text("The date to pay, YYYY-MM-DD."), reason=REASON),
    _tool("hold",
          "Do not pay this invoice for now. Use it for anything you are not sure about. An "
          "open question of yours to the owner already keeps an invoice unpaid: leave it, so "
          "the owner can still answer it.",
          invoice=INVOICE, reason=REASON),
    _tool("ask_owner",
          "Ask the owner a question about this invoice; it is not paid until the owner answers. "
          "Ask only what the owner really has to decide, so that yes means pay it and no means "
          "hold it, and say what you recommend. On an invoice the checks hold, a yes cannot pay "
          "it: ask about the one step you propose (for example, sending the supplier the wallet "
          "notice), so yes means do it. It reaches the owner as you write it (with "
          "yes/no buttons when only their yes is missing), so make it stand on its own. When "
          "the owner must first do something in the dashboard (approve a wallet, raise a limit, "
          "add money), their yes cannot do it: ask what they should check before doing it.",
          invoice=INVOICE,
          question=_text("The question alone, as the owner will read it on a card with yes/no "
                         "buttons, at most 300 characters: the supplier, the amount and what to "
                         "check. Plain words, no invoice number, no recommendation here."),
          recommendation=_text("What you recommend and why, in one sentence.")),
    _tool("set_alarm",
          "Wake yourself at a given time to look at something again.",
          at=_text("Shop's local date and time, YYYY-MM-DDTHH:MM."),
          why=_text("What to look at then."),
          invoice=_text("The invoice it is about, exactly as listed, or empty. An alarm about "
                        "an invoice does not ring once that invoice is paid.")),
    _tool("note",
          "Keep a short note for your future sessions (about a supplier, an invoice or the shop).",
          about=_text("A supplier name, an invoice name, or 'shop'."),
          text=_text("The note, at most a few sentences.")),
    _tool("reply_owner",
          "Answer the owner's message: only what they wrote about, what you did about it. Call "
          "it once per session that has owner messages, before finish.",
          text=_text("Your answer in one or two short lines (at most 300 characters): the answer "
                     "to what they asked, and what they must do and where, if anything. The "
                     "steps and numbers behind it are in their activity; do not repeat them.")),
    _tool("notify_owner",
          "Tell the owner something that changed for them, without waiting for them to write: "
          "what you paid on your own, a problem only they can fix, money nothing you can reach "
          "will cover. One topic per notice. It reaches their dashboard and phone. Never for "
          "money you moved (it shows as a receipt), never to repeat an ask_owner question. At "
          "most 3 per session.",
          text=_text("The notice in one or two short lines (at most 300 characters): what "
                     "happened, and what they must do and where, if anything.")),
    _tool("email_supplier",
          "Write to a supplier from the shop's own ERPNext, at the email on its record: for "
          "example that a payment will be late and when it will go out. Only after the owner "
          "agreed in this conversation. Short and plain; never a link, never about wallets or "
          "payment details.",
          supplier=_text("The supplier name exactly as listed."),
          subject=_text("A short subject."),
          body=_text("The message, a few plain sentences; blank lines between paragraphs.")),
    _tool("ask_supplier_to_confirm_wallet",
          "Send the supplier the standard notice to confirm the wallet the ERP holds for it: it "
          "tells the supplier to type the shop's supplier page itself and sign in with that "
          "wallet, with no link. Use it for a supplier whose wallet is waiting for its "
          "signature, only after the owner agreed in this conversation. It needs an email for "
          "the supplier in the ERP (email_on_file in supplier_profile); without one, tell the "
          "owner to add it to the supplier's contact in ERPNext. The text is fixed; you do not "
          "write it.",
          supplier=_text("The supplier name exactly as listed.")),
    _tool("finish",
          "End the session once every open invoice has your decision. The summary is what the "
          "owner reads first.",
          summary=_text("One to three plain sentences for the owner: what you did and what you "
                        "need from them, if anything.")),
]

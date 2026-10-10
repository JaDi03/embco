"""The tools the agent can call, as the Claude API receives them. Strict: inputs always match.

Order and text are fixed so the request prefix stays the same and is served from the cache.
"""

from typing import Any


def _tool(name: str, description: str, **properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
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
          "A supplier: its wallet and whether it was proven by signature, its recent invoices, "
          "its recent payments, and your notes about it.",
          supplier=_text("The supplier name exactly as listed.")),
    _tool("price_history",
          "Rates a supplier charged for one item over time, from its invoices, oldest first.",
          supplier=_text("The supplier name exactly as listed."),
          item_code=_text("The item code exactly as on the invoice line.")),
    _tool("cash_position",
          "The shop contract's limits, how much it still lets the shop pay this week, what you "
          "chose to pay now in this session, and the payments scheduled or due soon."),
    _tool("pay_now",
          "Pay this invoice now. Only invoices the checks allow (PAY) can be paid. The payment "
          "is sent at the end of the session through the shop contract, which checks it again.",
          invoice=INVOICE, reason=REASON),
    _tool("schedule_payment",
          "Pay this invoice on a later date you choose (shop's local date). Only invoices the "
          "checks allow. It is paid on that date without waking you.",
          invoice=INVOICE, pay_on=_text("The date to pay, YYYY-MM-DD."), reason=REASON),
    _tool("hold",
          "Do not pay this invoice for now. Use it for anything you are not sure about.",
          invoice=INVOICE, reason=REASON),
    _tool("ask_owner",
          "Ask the owner a question about this invoice; it is not paid until the owner answers. "
          "Ask only what the owner really has to decide, and say what you recommend.",
          invoice=INVOICE,
          question=_text("The question, short and concrete."),
          recommendation=_text("What you recommend and why, in one sentence.")),
    _tool("set_alarm",
          "Wake yourself at a given time to look at something again.",
          at=_text("Shop's local date and time, YYYY-MM-DDTHH:MM."),
          why=_text("What to look at then.")),
    _tool("note",
          "Keep a short note for your future sessions (about a supplier, an invoice or the shop).",
          about=_text("A supplier name, an invoice name, or 'shop'."),
          text=_text("The note, at most a few sentences.")),
    _tool("reply_owner",
          "Answer the owner's message. Plain words, the facts and numbers that matter, what you "
          "did about it. Call it once per session that has owner messages, before finish.",
          text=_text("Your answer to the owner, a few sentences at most.")),
    _tool("notify_owner",
          "Tell the owner something they should act on, without waiting for them to write: money "
          "that will not reach a coming payment, a deadline, a problem only they can fix. It "
          "reaches their dashboard and phone. Say the date, the numbers and what to do. At most "
          "3 per session; never for routine news (the summary is for that).",
          text=_text("The notice, a few plain sentences with the numbers that matter.")),
    _tool("email_supplier",
          "Write to a supplier from the shop's own ERPNext, at the email on its record: for "
          "example that a payment will be late and when it will go out. Only after the owner "
          "agreed in this conversation. Short and plain; never a link, never about wallets or "
          "payment details.",
          supplier=_text("The supplier name exactly as listed."),
          subject=_text("A short subject."),
          body=_text("The message, a few plain sentences; blank lines between paragraphs.")),
    _tool("finish",
          "End the session once every open invoice has your decision. The summary is what the "
          "owner reads first.",
          summary=_text("One to three plain sentences for the owner: what you did and what you "
                        "need from them, if anything.")),
]

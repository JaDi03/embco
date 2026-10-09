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
    _tool("finish",
          "End the session once every open invoice has your decision. The summary is what the "
          "owner reads first.",
          summary=_text("One to three plain sentences for the owner: what you did and what you "
                        "need from them, if anything.")),
]

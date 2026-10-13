"""The agent's questions about invoices, sent to the owner's chat so they reach the phone too.

When the agent asks the owner about an invoice, the question is written once into the chat (and
from there to Telegram) with two quick answers. An answer is only a message to the agent, the
same words the dashboard's buttons send: the agent reads it and decides, and the contract still
sets the limits. Nothing that needs the owner's wallet (approving a wallet, changing limits,
turning the agent on) can be answered this way.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from agent.guardrails.rules import Action, Decision, fingerprint
from agent.models import AgentDecision, Choice
from services.shops import chat

YES, NO = "yes", "no"


def answer_text(invoice: str, answer: str) -> str:
    """The message a quick answer sends to the agent (the dashboard sends the same words)."""
    if answer == YES:
        return f"About {invoice}: yes, go ahead and pay it."
    return f"About {invoice}: no, hold it for now."


def question_key(agent: AgentDecision) -> str:
    """One question: the same choice on the same decision is not sent twice."""
    return f"{agent.invoice}:{agent.fingerprint}"


def new_questions(folder, decisions: Iterable[Decision], agent: Mapping[str, AgentDecision],
                  at: datetime) -> list[dict[str, Any]]:
    """Chat entries for the agent's questions that stand now and were not sent yet."""
    sent = {e.get("asks") for e in chat.read_chat(folder, limit=chat.MAX_ENTRIES)}
    entries = []
    for decision in decisions:
        choice = agent.get(decision.invoice)
        if (choice is None or choice.choice is not Choice.ASK or decision.action is not Action.PAY
                or choice.fingerprint != fingerprint(decision)
                or question_key(choice) in sent):
            continue
        text = (f"Question about {decision.invoice} ({decision.supplier}, {decision.amount} "
                f"USDC): {choice.question or choice.reason}")
        if choice.recommendation:
            text += f"\nI recommend: {choice.recommendation}"
        entries.append({"from": chat.AGENT, "at": at.isoformat(), "text": text,
                        "asks": question_key(choice), "invoice": decision.invoice})
    return entries

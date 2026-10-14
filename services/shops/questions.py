"""The agent's questions about invoices, sent to the owner's chat so they reach the phone too.

Each question is written into the chat (and from there to Telegram) once, in one of two forms:
- only the owner's yes is missing: the question with two quick answers (yes, pay it / no, hold
  it). An answer is only a message to the agent, the same words the dashboard's buttons send:
  the agent decides, and the contract still sets the limits.
- something needs the owner's wallet (approve the supplier's wallet, raise a limit, add money,
  authorize or resume payments): a short reminder to do it in the dashboard, then the agent's
  question, with no buttons.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from agent.guardrails.controls.base import Outcome, fmt
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.models import AgentDecision, Choice
from services.shops import chat

YES, NO = "yes", "no"


def answer_text(invoice: str, answer: str, question: str = "") -> str:
    """The message a quick answer sends to the agent (the dashboard sends the same words). The
    question goes with it, so the agent knows what the yes answers."""
    asked = f" (you asked: {question})" if question else ""
    if answer == YES:
        return f"About {invoice}{asked}: yes, go ahead and pay it."
    return f"About {invoice}{asked}: no, hold it for now."


def question_key(agent: AgentDecision, kind: str) -> str:
    """One question in one form: the same choice on the same decision is not sent twice."""
    return f"{agent.invoice}:{agent.fingerprint}:{kind}"


def wallet_steps(decision: Decision, policy: PolicyConfig, funds: Mapping[str, str] | None,
                 room: Decimal | None, paused: bool) -> list[str]:
    """What the owner has to do with their wallet in the dashboard before this can be paid."""
    steps = []
    if any(f.control == "payee_wallet" and f.outcome is Outcome.ASK for f in decision.findings):
        steps.append("approve its wallet")
    if decision.amount > policy.max_per_payment:
        steps.append(f"raise the per-payment limit (now {fmt(policy.max_per_payment)} USDC)")
    elif room is not None and decision.amount > room:
        steps.append(f"raise the weekly limit or wait for it to renew ({_cents(room)} USDC left)")
    balance, available = _usdc(funds, "owner_balance"), _usdc(funds, "available_to_pay")
    if balance is not None and decision.amount > balance:
        steps.append(f"add USDC to your wallet ({_cents(balance)} there)")
    elif available is not None and decision.amount > available:
        steps.append("authorize payments again")
    if paused:
        steps.append("resume payments")
    return steps


def new_questions(folder, decisions: Iterable[Decision], agent: Mapping[str, AgentDecision],
                  at: datetime, *, policy: PolicyConfig,
                  funds: Callable[[], dict[str, str] | None] | None = None,
                  room: Callable[[], Decimal | None] | None = None,
                  paused: bool = False) -> list[dict[str, Any]]:
    """Chat entries for the agent's questions that stand now and were not sent in this form.
    The money is read from the chain only while a question stands: adding money turns a
    reminder into a question with buttons."""
    sent = {e.get("asks") for e in chat.read_chat(folder, limit=chat.MAX_ENTRIES)}
    asked = [(d, agent[d.invoice]) for d in decisions
             if d.invoice in agent and agent[d.invoice].choice is Choice.ASK
             and d.action in (Action.PAY, Action.ASK)
             and agent[d.invoice].fingerprint == fingerprint(d)]
    if not asked:
        return []
    money = funds() if funds else None
    left = room() if room else None
    entries = []
    for decision, choice in asked:
        steps = wallet_steps(decision, policy, money, left, paused)
        kind = "wallet" if steps else "yes"
        if question_key(choice, kind) in sent:
            continue
        amount = f"{fmt(decision.amount)} USDC"
        if steps:
            text = (f"{decision.supplier}, {amount} ({decision.invoice}): needs you in the "
                    f"dashboard: {', '.join(steps)}.\n{choice.question or choice.reason}")
        else:
            text = (f"Pay {amount} to {decision.supplier} ({decision.invoice})?\n"
                    f"{choice.question or choice.reason}")
        entries.append({"from": chat.AGENT, "at": at.isoformat(), "text": text,
                        "asks": question_key(choice, kind), "invoice": decision.invoice,
                        "question": choice.question, "buttons": not steps})
    return entries


def _cents(value: Decimal) -> str:
    """The chain gives six decimals; the owner reads cents, rounded down so it never shows more
    than there is."""
    return fmt(value.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _usdc(funds: Mapping[str, str] | None, key: str) -> Decimal | None:
    try:
        return Decimal(funds[key]) if funds else None
    except (KeyError, InvalidOperation):
        return None  # unknown: the payment itself still checks it

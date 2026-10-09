"""How the agent's sessions and decisions are written into the journal and read back.

SESSION: one entry per time the agent was woken (why, summary, alarms, notes, cost).
AGENT: one entry per invoice the agent decided on in a finished session.
"""

from datetime import date, datetime
from typing import Any

from agent.models import AgentDecision, Alarm, Choice, Note, Session, Usage, Wake


def session_body(session: Session) -> dict[str, Any]:
    return {
        "finished": session.finished,
        "summary": session.summary,
        "wakes": [{"kind": w.kind, "text": w.text, "invoice": w.invoice, "key": w.key}
                  for w in session.wakes],
        "decided": [d.invoice for d in session.decisions],
        "alarms": [{"at": a.at.isoformat(), "why": a.why} for a in session.alarms],
        "notes": [{"about": n.about, "text": n.text} for n in session.notes],
        "steps": session.steps,
        "model": session.model,
        "usage": {"input": session.usage.input_tokens, "output": session.usage.output_tokens,
                  "cache_read": session.usage.cache_read_tokens,
                  "cache_write": session.usage.cache_write_tokens},
        "error": session.error,
    }


def decision_body(decision: AgentDecision) -> dict[str, Any]:
    return {
        "fingerprint": decision.fingerprint,
        "choice": decision.choice.value,
        "reason": decision.reason,
        "pay_on": decision.pay_on.isoformat() if decision.pay_on else None,
        "question": decision.question,
        "recommendation": decision.recommendation,
    }


def session_from(at: datetime, body: dict[str, Any],
                 decisions: dict[str, AgentDecision] | None = None) -> Session:
    usage = body.get("usage") or {}
    return Session(
        at=at,
        wakes=tuple(Wake(w["kind"], w["text"], w.get("invoice"), w.get("key", ""))
                    for w in body.get("wakes", [])),
        finished=bool(body.get("finished")),
        summary=body.get("summary", ""),
        decisions=tuple(decisions[i] for i in body.get("decided", [])
                        if decisions and i in decisions),
        alarms=tuple(Alarm(datetime.fromisoformat(a["at"]), a["why"], set_at=at)
                     for a in body.get("alarms", [])),
        notes=tuple(Note(n["about"], n["text"], at=at) for n in body.get("notes", [])),
        steps=int(body.get("steps", 0)),
        model=body.get("model", ""),
        usage=Usage(int(usage.get("input", 0)), int(usage.get("output", 0)),
                    int(usage.get("cache_read", 0)), int(usage.get("cache_write", 0))),
        error=body.get("error", ""),
    )


def decision_from(invoice: str, at: datetime, body: dict[str, Any]) -> AgentDecision:
    pay_on = body.get("pay_on")
    return AgentDecision(
        invoice=invoice,
        fingerprint=body["fingerprint"],
        choice=Choice(body["choice"]),
        reason=body["reason"],
        pay_on=date.fromisoformat(pay_on) if pay_on else None,
        question=body.get("question", ""),
        recommendation=body.get("recommendation", ""),
        decided_at=at,
    )

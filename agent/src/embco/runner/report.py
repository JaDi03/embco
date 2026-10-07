"""Plain-text summary of a cycle for the terminal and the service log."""

from decimal import Decimal

from embco.controls.base import fmt
from embco.journal import ChangeKind
from embco.llm import Explanation
from embco.runner.cycle import CycleReport


def format_report(report: CycleReport, *, verbose: bool = False) -> str:
    counts = {kind: 0 for kind in ChangeKind}
    for change in report.changes:
        counts[change.kind] += 1
    lines = [
        f"run {report.run_id} at {report.at:%Y-%m-%d %H:%M %Z}: {len(report.decisions)} unpaid "
        f"({counts[ChangeKind.NEW]} new, {counts[ChangeKind.CHANGED]} changed, "
        f"{counts[ChangeKind.SAME]} same, {len(report.closed)} closed)"
    ]
    explained = {e.invoice: e for e in report.explanations}
    if verbose:
        for d in report.decisions:
            lines.append(f"  {d.action.value:<4} {d.invoice}  {fmt(d.amount):>12}  {d.supplier}")
            lines.extend(f"         - {reason}" for reason in d.reasons)
            if d.invoice in explained:
                lines.extend(_explanation(explained[d.invoice], indent="         "))
    shown = {ChangeKind.CHANGED} if verbose else {ChangeKind.NEW, ChangeKind.CHANGED}
    for change in report.changes:
        if change.kind in shown:
            lines.append(f"  {change.kind.value:<7} {change.decision.invoice}  {change.note}")
    lines.extend(f"  CLOSED  {invoice}  no longer unpaid" for invoice in report.closed)
    if not verbose:
        for e in report.explanations:
            if e.created_at == report.at:  # only what was explained in this cycle
                lines.append(f"  {e.invoice}:")
                lines.extend(_explanation(e, indent="    "))
    for c in report.challenges:
        lines.append(
            f"  waiting for {c.supplier} to sign for wallet {c.wallet} "
            f"(until {c.expires_at:%Y-%m-%d})"
        )
    lines.extend(_payments(report))
    plan = report.plan
    to_pay = sum((d.amount for d in plan.pay_now), start=Decimal(0))
    lines.append(
        f"  plan: pay now {len(plan.pay_now)} ({fmt(to_pay)}), deferred {len(plan.deferred)}, "
        f"held {len(plan.held)}, ask {len(plan.asked)}; budget left {fmt(plan.budget_left)}"
    )
    return "\n".join(lines)


def _payments(report: CycleReport) -> list[str]:
    lines = []
    s = report.settlement
    if s is not None:
        if s.problem:
            lines.append(f"  payments skipped: {s.problem}")
        for e in s.events:
            where = f" tx {e.tx_hash}" if e.tx_hash else ""
            why = f" ({e.reason})" if e.reason else ""
            lines.append(f"  {e.status.value:<9} {e.invoice}  {fmt(e.amount)} USDC to "
                         f"{e.payee or e.supplier}{where}{why}")
        lines.extend(f"  waiting for room under the weekly cap: {i}" for i in s.waiting)
    if report.already_paid:
        lines.append(f"  paid by the agent, not yet closed in the ERP: "
                     f"{', '.join(report.already_paid)}")
    return lines


def _explanation(e: Explanation, indent: str) -> list[str]:
    return [f"{indent}AI: {e.summary}", f"{indent}next step: {e.next_step}"]

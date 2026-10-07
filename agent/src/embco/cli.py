"""The `embco` command: run the agent once or on its own, and the owner's commands.

    embco run                                one cycle, full report
    embco watch [--cycles N]                 a cycle every EMBCO_INTERVAL_MINUTES, until stopped
    embco answer INVOICE approve|reject      the owner answers an ASK
    embco challenge SUPPLIER                 the message the supplier must sign (EIP-712 JSON)
    embco sign-wallet SUPPLIER SIGNATURE     the supplier's signature comes back
    embco history INVOICE                    every decision taken on an invoice
    embco verify                             check that the memory was not altered
    embco create-wallet                      create the agent's paying wallet with Circle (once)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from embco.circle import CircleClient, CircleError
from embco.decision import Verdict
from embco.journal import JournalError, SqliteJournal, answer_ask, submit_wallet_signature
from embco.ledger import ErpnextAdapter, LedgerError
from embco.llm import ClaudeExplainer, Explainer
from embco.payments import ArcRpc, Payer
from embco.runner import format_report, run_cycle, watch
from embco.settings import Settings, SettingsError
from embco.signing import typed_data

log = logging.getLogger("embco")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per ERP request is noise
    try:
        settings = Settings.load(Path(args.env_file))
        settings.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteJournal(settings.journal_path) as journal:
            return _COMMANDS[args.command](args, settings, journal)
    except SettingsError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except (JournalError, LedgerError, CircleError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        log.info("stopped")
        return 0


def _ledger(settings: Settings) -> ErpnextAdapter:
    return ErpnextAdapter(
        settings.erpnext_url,
        settings.erpnext_api_key,
        settings.erpnext_api_secret,
        company=settings.company,
        paid_from=settings.erpnext_paid_from,
    )


def _explainer(settings: Settings) -> Explainer | None:
    if not settings.explain:
        return None
    return ClaudeExplainer(settings.llm_model, api_key=settings.anthropic_api_key)


def _payer(settings: Settings, ledger: ErpnextAdapter) -> Payer | None:
    if not settings.pay:
        return None
    return Payer(
        ledger=ledger,
        chain=ArcRpc(settings.arc_rpc_url),
        circle=CircleClient(settings.circle_api_key, settings.circle_entity_secret),
        shop=settings.shop_address,
        wallet_id=settings.agent_wallet_id,
        writer=ledger,
        approvals_file=settings.approvals_file,
    )


def _run(args, settings, journal) -> int:
    ledger = _ledger(settings)
    report = run_cycle(ledger, journal, settings.policy, settings.company,
                       explainer=_explainer(settings), payments=_payer(settings, ledger),
                       owners=settings.owner_users)
    print(format_report(report, verbose=True))
    return 0


def _watch(args, settings, journal) -> int:
    ledger = _ledger(settings)
    explainer = _explainer(settings)
    payer = _payer(settings, ledger)
    log.info("watching %s every %s (AI explanations %s, payments %s)", settings.company,
             settings.interval, f"on, {explainer.model}" if explainer else "off",
             f"on, shop {settings.shop_address}" if payer else "off")

    def cycle() -> None:
        report = run_cycle(ledger, journal, settings.policy, settings.company,
                           explainer=explainer, payments=payer, owners=settings.owner_users)
        log.info("%s", format_report(report))

    watch(cycle, settings.interval, cycles=args.cycles)
    return 0


def _answer(args, settings, journal) -> int:
    saved = answer_ask(journal, args.invoice, Verdict[args.verdict.upper()], args.by,
                       note=args.note)
    print(f"recorded: {saved.verdict} {saved.invoice} by {saved.answered_by}")
    return 0


def _challenge(args, settings, journal) -> int:
    challenge = journal.latest_wallet_challenge(args.supplier)
    if challenge is None:
        print(f"no wallet challenge for {args.supplier}", file=sys.stderr)
        return 1
    print(json.dumps(typed_data(challenge), indent=2))
    return 0


def _sign_wallet(args, settings, journal) -> int:
    proof = submit_wallet_signature(journal, _ledger(settings), args.supplier, args.signature)
    print(f"proof recorded: {proof.supplier} controls {proof.wallet}")
    return 0


def _history(args, settings, journal) -> int:
    entries = journal.history(args.invoice)
    if not entries:
        print(f"no decisions recorded for {args.invoice}")
    for e in entries:
        print(f"run {e.run_id} {e.recorded_at:%Y-%m-%d %H:%M} {e.action.value}: "
              f"{'; '.join(e.reasons)}")
    return 0


def _verify(args, settings, journal) -> int:
    journal.verify()
    print(f"journal verified: {settings.journal_path}")
    return 0


def _create_wallet(args, settings, journal) -> int:
    if settings.agent_wallet_id:
        print("EMBCO_AGENT_WALLET_ID is already set; the agent has a wallet", file=sys.stderr)
        return 1
    if not (settings.circle_api_key and settings.circle_entity_secret):
        raise SettingsError("missing settings: CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET")
    circle = CircleClient(settings.circle_api_key, settings.circle_entity_secret)
    wallet_set_id = circle.create_wallet_set(f"embco {settings.company}")
    wallet = circle.create_eoa_wallet(wallet_set_id, f"embco agent {settings.company}")
    print(f"wallet created on {wallet.blockchain}: {wallet.address}")
    print(f"add to the settings: EMBCO_AGENT_WALLET_ID={wallet.id}")
    return 0


_COMMANDS = {
    "run": _run,
    "watch": _watch,
    "answer": _answer,
    "challenge": _challenge,
    "sign-wallet": _sign_wallet,
    "history": _history,
    "verify": _verify,
    "create-wallet": _create_wallet,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="embco", description="Payables agent for ERPNext")
    parser.add_argument("--env-file", default=".env", help="settings file (default: .env)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run one cycle and print the full report")
    watch_cmd = sub.add_parser("watch", help="run a cycle on every interval until stopped")
    watch_cmd.add_argument("--cycles", type=int, help="stop after this many cycles")
    answer = sub.add_parser("answer", help="approve or reject an invoice the agent asked about")
    answer.add_argument("invoice")
    answer.add_argument("verdict", choices=["approve", "reject"])
    answer.add_argument("--by", default="owner", help="who answers (default: owner)")
    answer.add_argument("--note", default="")
    challenge = sub.add_parser("challenge", help="print the message a supplier must sign")
    challenge.add_argument("supplier")
    sign = sub.add_parser("sign-wallet", help="submit a supplier's signature")
    sign.add_argument("supplier")
    sign.add_argument("signature")
    history = sub.add_parser("history", help="show every decision taken on an invoice")
    history.add_argument("invoice")
    sub.add_parser("verify", help="check that the memory was not altered")
    sub.add_parser("create-wallet", help="create the agent's paying wallet with Circle (once)")
    return parser


if __name__ == "__main__":
    sys.exit(main())

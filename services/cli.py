"""The `embco` command: run the agent once or on its own, and the owner's commands.

    embco run                                one cycle, full report
    embco watch [--cycles N]                 a cycle every EMBCO_INTERVAL_MINUTES, until stopped
    embco answer INVOICE approve|reject      the owner answers an ASK
    embco challenge SUPPLIER                 the message the supplier must sign (EIP-712 JSON)
    embco sign-wallet SUPPLIER SIGNATURE     the supplier's signature comes back
    embco history INVOICE                    every decision taken on an invoice
    embco verify                             check that the memory was not altered, also on Arc
    embco audit FILE [--rpc-url URL]         check a downloaded journal against Arc, no keys
    embco prove FILE --entry N [--out F]     proof that one entry is anchored on Arc
    embco check-proof FILE [--rpc-url URL]   check a proof of one entry against Arc
    embco arc-proof FILE [--rpc-url URL]     a decision proof as one value for Arc's explorer
    embco witness --shop ADDR [--journal F]  keep when anchors were first seen; compare with a copy
    embco create-wallet                      create the agent's paying wallet with Circle (once)
    embco shop --dir ROOT/SHOP               the hosted agent of one shop, settings from its folder
    embco hub                                the API the dashboard uses to connect shops
"""

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from eth_utils import to_checksum_address

from agent.explain import ClaudeExplainer, Explainer
from agent.guardrails.rules import Verdict
from agent.memory import JournalError, SqliteJournal, answer_ask, submit_wallet_signature
from agent.reflexes.cycle import run_cycle
from agent.reflexes.report import format_report
from agent.reflexes.watch import watch
from agent.wiring import brain_setup, room_of
from services.anchor import JOURNAL_ANCHOR, AuditError, JournalAnchors, mismatches
from services.anchor import audit as audit_journal
from services.anchor import report as audit_report
from services.anchor.audit import load as load_journal_file
from services.anchor.decision import check_decision_bundle as check_decision
from services.anchor.decision import decision_report
from services.anchor.onchain import ARC_EXPLORER, DECISION_VERIFIER, arc_proof
from services.anchor.proof import check_bundle as check_proof
from services.anchor.proof import make_bundle as make_proof
from services.anchor.proof import proof_report
from services.anchor.proof import read_bundle as read_proof
from services.anchor.witness import load_state as load_witness_state
from services.anchor.witness import save_state as save_witness_state
from services.anchor.witness import witness as run_witness
from services.circle import CircleClient, CircleError
from services.erp import ErpnextAdapter, LedgerError
from services.payments import ArcRpc, ChainError, Payer
from services.payments.encoding import ref_scope
from services.settings import Settings, SettingsError
from services.shops import (
    BRAIN_PLATFORM,
    PLATFORM,
    SITE_PLATFORM,
    ShopError,
    ShopStore,
    erp_for,
    read_key,
    run_shop,
)
from services.signing import typed_data

log = logging.getLogger("embco")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per ERP request is noise
    if args.command == "shop":
        return _shop(args)
    if args.command == "hub":
        return _hub(args)
    if args.command == "telegram":
        return _telegram(args)
    if args.command == "audit":
        return _audit(args)
    if args.command == "prove":
        return _prove(args)
    if args.command == "check-proof":
        return _check_proof(args)
    if args.command == "arc-proof":
        return _arc_proof(args)
    if args.command == "witness":
        return _witness(args)
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
    return erp_for(settings)


def _hub(args) -> int:
    from services.hub import serve  # the web server is only loaded by the hub

    try:
        serve(Path(args.env_file))
    except SettingsError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    return 0


def _telegram(args) -> int:
    from services.telegram.serve import serve_bot  # only the bot process loads it

    try:
        serve_bot(Path(args.env_file))
    except SettingsError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


def _shop(args) -> int:
    """Settings come from the shop's folder; from this process's environment, only the
    platform's Circle account, Arc node and model account."""
    folder = Path(args.dir)
    try:
        store = ShopStore(folder.parent, read_key(Path(args.key_file)))
        names = (*PLATFORM, *BRAIN_PLATFORM, *SITE_PLATFORM)
        platform = {name: os.environ.get(name, "") for name in names}
        run_shop(store, folder.name, cycles=args.cycles, platform=platform)
    except (ShopError, SettingsError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except JournalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


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
        ref_scope=ref_scope(settings.erpnext_url, settings.company),
    )


def _payments_mode(settings, payer) -> str:
    """Said first, so nobody pays in a mode they did not mean to."""
    if not payer:
        return "off"
    entries = ("DRAFT MODE: Payment Entries stay as drafts marked TESTNET"
               if settings.erpnext_draft_payments
               else "Payment Entries are SUBMITTED in ERPNext")
    return f"on, shop {settings.shop_address}, {entries}"


def _run(args, settings, journal) -> int:
    ledger = _ledger(settings)
    payer = _payer(settings, ledger)
    print(f"payments {_payments_mode(settings, payer)}")
    report = run_cycle(ledger, journal, settings.policy, settings.company,
                       explainer=_explainer(settings), payments=payer,
                       owners=settings.owner_users, brain=brain_setup(settings, room_of(payer)))
    print(format_report(report, verbose=True))
    return 0


def _watch(args, settings, journal) -> int:
    ledger = _ledger(settings)
    explainer = _explainer(settings)
    payer = _payer(settings, ledger)
    brain = brain_setup(settings, room_of(payer))
    log.info("watching %s every %s (AI explanations %s, payments %s, agent brain %s)",
             settings.company, settings.interval, f"on, {explainer.model}" if explainer else "off",
             _payments_mode(settings, payer),
             f"on, {brain.brain.model}, {brain.autonomy.value}" if brain else "off")

    def cycle() -> None:
        report = run_cycle(ledger, journal, settings.policy, settings.company,
                           explainer=explainer, payments=payer, owners=settings.owner_users,
                           brain=brain)
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
    if not (JOURNAL_ANCHOR and settings.arc_rpc_url and settings.shop_address):
        print("not checked against Arc: no anchor registry or no shop on Arc in the settings")
        return 0
    anchors = JournalAnchors(ArcRpc(settings.arc_rpc_url), settings.shop_address).all()
    hashes = journal.entry_hashes()
    if problems := mismatches(hashes, anchors):
        for problem in problems:
            print(f"DOES NOT MATCH ARC: {problem}", file=sys.stderr)
        return 1
    covered = anchors[-1].size if anchors else 0
    print(f"matches all {len(anchors)} anchors on Arc: {covered} of {len(hashes)} entries "
          "anchored")
    return 0


def _audit(args) -> int:
    """No settings, no keys: a downloaded journal, and an Arc node to read the anchors from."""
    url = args.rpc_url or os.environ.get("ARC_TESTNET_RPC_URL", "").strip()
    try:
        if not url:
            raise AuditError("pass an Arc Testnet node with --rpc-url (or ARC_TESTNET_RPC_URL)")
        data = load_journal_file(args.file)
        anchors = JournalAnchors(ArcRpc(url), data["shop"]).all()
    except (AuditError, ChainError) as error:
        print(f"embco: {error}", file=sys.stderr)
        return 2
    result = audit_journal(data, anchors)
    print(audit_report(result))
    return 0 if result.ok else 1


def _arc_node(args) -> str:
    url = args.rpc_url or os.environ.get("ARC_TESTNET_RPC_URL", "").strip()
    if not url:
        raise AuditError("pass an Arc Testnet node with --rpc-url (or ARC_TESTNET_RPC_URL)")
    return url


def _prove(args) -> int:
    """One entry of a downloaded journal, with the proof that it is anchored on Arc."""
    try:
        data = load_journal_file(args.file)
        anchors = JournalAnchors(ArcRpc(_arc_node(args)), data["shop"]).all()
        bundle = make_proof(data, args.entry, anchors)
    except (AuditError, ChainError) as error:
        print(f"embco: {error}", file=sys.stderr)
        return 2
    text = json.dumps(bundle, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"proof of entry {args.entry} written to {args.out}")
    else:
        print(text)
    return 0


def _check_proof(args) -> int:
    """Check a proof bundle: its entry, its place in the tree, and the root on Arc."""
    try:
        bundle = read_proof(args.file)
        anchors = JournalAnchors(ArcRpc(_arc_node(args)), bundle["shop"]).all()
    except (AuditError, ChainError) as error:
        print(f"embco: {error}", file=sys.stderr)
        return 2
    if "proofs" in bundle:  # every entry behind one decision, as the dashboard downloads it
        try:
            checked = check_decision(bundle, anchors)
        except AuditError as error:
            print(f"embco: {error}", file=sys.stderr)
            return 2
        print(decision_report(bundle, checked))
        return 0 if checked.ok else 1
    result = check_proof(bundle, anchors)
    print(proof_report(bundle, result))
    return 0 if result.ok else 1


def _arc_proof(args) -> int:
    """A decision proof as the one value DecisionVerifier.verify takes, to paste in the explorer."""
    try:
        bundle = read_proof(args.file)
        anchors = JournalAnchors(ArcRpc(_arc_node(args)), bundle["shop"]).all()
        value = arc_proof(bundle, anchors)
    except (AuditError, ChainError) as error:
        print(f"embco: {error}", file=sys.stderr)
        return 2
    where = (f"{ARC_EXPLORER}/address/{DECISION_VERIFIER}?tab=read_contract" if DECISION_VERIFIER
             else "DecisionVerifier (not deployed yet)")
    print(f"Paste this in verify, at {where}:\n\n{value}")
    return 0


def _witness(args) -> int:
    """Read the anchors on Arc, keep when each was first seen, and compare them with a copy."""
    try:
        shop = to_checksum_address(args.shop)
        journal = load_journal_file(args.journal) if args.journal else None
        state_path = Path(args.state or f"witness-{shop[2:10].lower()}.json")
        state = load_witness_state(state_path, shop)
        anchors = JournalAnchors(ArcRpc(_arc_node(args)), shop).all()
    except (AuditError, ChainError) as error:
        print(f"embco: {error}", file=sys.stderr)
        return 2
    run = run_witness(state, anchors, journal, datetime.now(UTC))
    save_witness_state(state_path, state)
    for alert in run.alerts:
        print(f"ALERT: {alert}")
    for note in run.notes:
        print(note)
    print(f"{run.anchors_seen} anchors recorded in {state_path}: "
          + ("no alerts." if run.ok else "ALERTS, see above."))
    return 0 if run.ok else 1


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
    audit_cmd = sub.add_parser("audit", help="check a downloaded journal against Arc")
    audit_cmd.add_argument("file", help="the journal JSON downloaded from the dashboard")
    audit_cmd.add_argument("--rpc-url", help="an Arc Testnet node (default: ARC_TESTNET_RPC_URL)")
    prove_cmd = sub.add_parser("prove", help="make the proof that one entry is anchored on Arc")
    prove_cmd.add_argument("file", help="the journal JSON downloaded from the dashboard")
    prove_cmd.add_argument("--entry", type=int, required=True, help="the entry number, from 1")
    prove_cmd.add_argument("--out", help="write the proof to this file (default: print it)")
    prove_cmd.add_argument("--rpc-url", help="an Arc Testnet node (default: ARC_TESTNET_RPC_URL)")
    check_cmd = sub.add_parser("check-proof", help="check a proof of one entry against Arc")
    check_cmd.add_argument("file", help="a proof written by embco prove")
    check_cmd.add_argument("--rpc-url", help="an Arc Testnet node (default: ARC_TESTNET_RPC_URL)")
    arc_cmd = sub.add_parser("arc-proof", help="a decision proof as one value to check on Arc")
    arc_cmd.add_argument("file", help="a decision proof (from the dashboard's journal download)")
    arc_cmd.add_argument("--rpc-url", help="an Arc Testnet node (default: ARC_TESTNET_RPC_URL)")
    witness_cmd = sub.add_parser("witness", help="keep when each anchor was seen; compare")
    witness_cmd.add_argument("--shop", required=True, help="the shop contract address")
    witness_cmd.add_argument("--journal", help="your latest copy of the journal (downloaded JSON)")
    witness_cmd.add_argument("--state", help="the witness record (default: witness-<shop>.json)")
    witness_cmd.add_argument("--rpc-url", help="an Arc Testnet node (default: ARC_TESTNET_RPC_URL)")
    sub.add_parser("create-wallet", help="create the agent's paying wallet with Circle (once)")
    shop = sub.add_parser("shop", help="run the hosted agent of one shop from its folder")
    shop.add_argument("--dir", required=True, help="the shop's folder: ROOT/<contract address>")
    shop.add_argument("--key-file", default=os.environ.get("EMBCO_SHOP_KEY_FILE",
                                                           "/etc/embco/shops.key"),
                      help="the service key that encrypts ERP keys")
    shop.add_argument("--cycles", type=int, help="stop after this many cycles")
    sub.add_parser("hub", help="serve the API the dashboard uses to connect shops")
    sub.add_parser("telegram", help="run the Telegram bot: /stop, /pause and talking to the agent")
    return parser


if __name__ == "__main__":
    sys.exit(main())

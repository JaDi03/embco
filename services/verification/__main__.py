"""One bounded worker round, invoked every minute by systemd; no ERP or signing keys needed."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import httpx

from services.settings import read_env_file
from services.verification.client import Api
from services.verification.config import factories
from services.verification.store import Store
from services.verification.worker import Worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify embco factory-created contracts")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--state", type=Path,
                        default=Path("/var/lib/embco/verification/state.sqlite3"))
    parser.add_argument("--discover-only", action="store_true",
                        help="read events and queue contracts without submitting verification")
    parser.add_argument("--status", action="store_true", help="read local totals, without network")
    parser.add_argument("--max-jobs", type=int, default=10)
    args = parser.parse_args(argv)
    if args.max_jobs < 1:
        parser.error("--max-jobs must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx normally logs full request URLs, which may include private RPC credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store = Store(args.state)
    try:
        if args.status:
            print(json.dumps(store.summary()))
            return 0
        values = read_env_file(args.env_file) if args.env_file.exists() else {}
        specs = factories({**values, **os.environ})
        with httpx.Client(timeout=20) as http:
            result = Worker(store, Api(http, store), specs).run(
                discover_only=args.discover_only, max_jobs=args.max_jobs)
        print(json.dumps(result))
        return 0
    except ValueError:
        print("verification: invalid settings; check ARC_TESTNET_RPC_URL and factory address",
              file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

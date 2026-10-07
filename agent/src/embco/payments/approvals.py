"""The list of wallets waiting for the owner's approval, published for the dashboard.

Public by design: it holds only wallet addresses, invoice numbers and amounts, never supplier
names. The owner checks who the wallet belongs to in the ERP, then approves it by signing in
MetaMask. The file is replaced in one step, so the dashboard never reads half of it.
"""

import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from embco.payments.models import PendingApproval

FORMAT = 1


def write_approvals(
    path: Path, shop: str, pending: Sequence[PendingApproval], at: datetime
) -> None:
    body = {
        "format": FORMAT,
        "shop": shop,
        "updated_at": at.isoformat(),
        "pending": [
            {"wallet": p.wallet, "invoice": p.invoice, "amount": str(p.amount)} for p in pending
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o644)  # served to the dashboard by the web server
    os.replace(tmp, path)

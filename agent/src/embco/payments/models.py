"""What the agent remembers about each payment it tries. Amounts are Decimal, never float."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class PaymentStatus(StrEnum):
    SUBMITTED = "SUBMITTED"  # Circle accepted the transaction; it is on its way
    COMPLETE = "COMPLETE"  # final on chain
    FAILED = "FAILED"  # Circle or the chain rejected it; a new attempt may follow
    BLOCKED = "BLOCKED"  # not sent: the simulation or a check said it would fail
    RECORDED = "RECORDED"  # final on chain and written into the ERP as a payment entry


@dataclass(frozen=True)
class PaymentEvent:
    invoice: str
    status: PaymentStatus
    at: datetime
    supplier: str
    payee: str
    amount: Decimal
    invoice_ref: str  # 0x-prefixed bytes32 the contract stores
    attempt: int = 0
    circle_tx_id: str | None = None
    tx_hash: str | None = None
    reason: str = ""
    erp_entry: str | None = None  # the ERP's payment entry, once recorded


@dataclass(frozen=True)
class PendingApproval:
    """A wallet the agent would pay now, once the owner approves it in the shop contract."""

    wallet: str
    invoice: str
    amount: Decimal

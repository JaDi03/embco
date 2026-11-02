"""What the agent remembers about each payment it tries. Amounts are Decimal, never float."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.fx.rates import PAYABLE_CURRENCY


class PaymentStatus(StrEnum):
    # Circle accepted the transaction; it is on its way. Without a Circle id: written just
    # before Circle was asked, and its answer has not been seen
    SUBMITTED = "SUBMITTED"
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
    session: str | None = None  # the agent session that sent it; its later states keep it
    why: str = ""  # the agent's reason, written with the payment before Circle is asked
    # an invoice in another currency: what this USDC settles in it, and the rate (units of
    # that currency per US dollar) the agent paid at
    invoice_amount: Decimal | None = None
    currency: str = PAYABLE_CURRENCY
    rate: Decimal | None = None


@dataclass(frozen=True)
class PendingApproval:
    """A wallet the agent would pay now, once the owner approves it in the shop contract."""

    wallet: str
    invoice: str
    amount: Decimal

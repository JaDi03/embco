"""How a payment looks on chain: exact USDC units, the invoice reference and the call data.

The agent calls Arc's Memo contract, which calls `ShopPayables.pay` with the agent as the sender
and records the invoice number next to the payment. The same bytes are simulated and then sent.
"""

from decimal import Decimal

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

USDC_DECIMALS = 6
MEMO_CONTRACT = "0x5294E9927c3306DcBaDb03fe70b92e01cCede505"  # predeployed on Arc

_PAY = keccak(text="pay(address,uint256,bytes32)")[:4]
_MEMO = keccak(text="memo(address,bytes,bytes32,bytes)")[:4]


class EncodingError(ValueError):
    pass


def usdc_units(amount: Decimal) -> int:
    """Whole units of 6 decimals; an amount that does not fit exactly is refused, not rounded."""
    if not amount.is_finite() or amount <= 0:
        raise EncodingError(f"amount must be greater than zero, got {amount}")
    units = amount.scaleb(USDC_DECIMALS)
    if units != units.to_integral_value():
        raise EncodingError(f"{amount} has more than {USDC_DECIMALS} decimals")
    return int(units)


def invoice_ref(invoice: str) -> bytes:
    """The bytes32 the contract stores so each invoice is paid once: keccak256 of its name."""
    return keccak(text=invoice)


def pay_call(payee: str, units: int, ref: bytes) -> bytes:
    return _PAY + encode(["address", "uint256", "bytes32"],
                         [to_checksum_address(payee), units, ref])


def memo_call(shop: str, inner: bytes, ref: bytes, invoice: str) -> bytes:
    """Memo.memo(target, data, memoId, memo): memoId is the invoice reference, memo its name."""
    return _MEMO + encode(["address", "bytes", "bytes32", "bytes"],
                          [to_checksum_address(shop), inner, ref, invoice.encode()])

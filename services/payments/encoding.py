"""How a payment looks on chain: exact USDC units, the invoice reference and the call data.

The agent calls `ShopPayables.pay` itself, with the invoice reference the contract keeps so each
invoice is paid once. The same bytes are simulated and then sent.
"""

from decimal import Decimal
from urllib.parse import urlsplit

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

USDC_DECIMALS = 6
_PAY = keccak(text="pay(address,uint256,bytes32)")[:4]


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


def invoice_ref(invoice: str, scope: str = "") -> bytes:
    """The bytes32 the contract stores so each invoice is paid once.

    An invoice name is only unique inside one ERP and company (two ERPs both have an
    ACC-PINV-2026-00025), so the name is scoped by them: keccak256 of "scope/name". Without a
    scope it is keccak256 of the name alone, as before.
    """
    return keccak(text=f"{scope}/{invoice}" if scope else invoice)


def ref_scope(erp_url: str, company: str) -> str:
    """The ERP site and company an invoice name belongs to, e.g. "shop.frappe.cloud/Acme"."""
    return f"{(urlsplit(erp_url).hostname or '').lower()}/{company}"


def pay_call(payee: str, units: int, ref: bytes) -> bytes:
    return _PAY + encode(["address", "uint256", "bytes32"],
                         [to_checksum_address(payee), units, ref])
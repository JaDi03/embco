"""Sign-In with Ethereum (EIP-4361): the message a wallet signs to sign in, built and checked with
the siwe library. The wallet shows it as a sign-in request for this site and warns when the site
asking is not the one in the message.
"""

from datetime import datetime
from urllib.parse import urlsplit

from siwe import ISO8601Datetime, SiweMessage, VerificationError, generate_nonce

__all__ = ["VerificationError", "generate_nonce", "sign_in_text", "signer_of"]


def sign_in_text(*, public_url: str, address: str, statement: str, nonce: str, chain_id: int,
                 issued_at: datetime, expires_at: datetime) -> str:
    """The EIP-4361 message for `address` to sign in to the site at `public_url`."""
    return SiweMessage(
        domain=urlsplit(public_url).netloc, address=address, statement=statement, uri=public_url,
        version="1", chain_id=chain_id, nonce=nonce,
        issued_at=ISO8601Datetime.from_datetime(issued_at),
        expiration_time=ISO8601Datetime.from_datetime(expires_at),
    ).prepare_message()


def signer_of(text: str, signature: str, *, public_url: str, nonce: str, chain_id: int,
              now: datetime) -> str:
    """The address that signed `text`, after the siwe library checks the site, the nonce, the
    network, the expiry and the signature. Raises VerificationError when any of them fails."""
    message = SiweMessage.from_message(message=text)
    message.verify(signature, domain=urlsplit(public_url).netloc, uri=public_url, nonce=nonce,
                   chain_id=chain_id, timestamp=now, strict=True)
    return message.address

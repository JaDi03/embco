"""The ERP address is typed by a user and then visited by our server, so it must lead outside.

Only https, only a host name or address that resolves to public addresses: never this machine,
a private network or a cloud metadata address. Credentials or paths in the address are refused.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str, int], list[str]]


class UnsafeUrl(Exception):
    pass


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def check_erp_url(url: str, resolve: Resolver = _resolve) -> str:
    """The base address to use (scheme, host and port), or UnsafeUrl."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeUrl("the ERP address must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UnsafeUrl("the ERP address must not carry credentials, a query or a fragment")
    if parts.path not in ("", "/") and not parts.path.rstrip("/").endswith(("/app", "/desk")):
        raise UnsafeUrl("use the site address, for example https://shop.frappe.cloud")
    try:
        port = parts.port or 443
    except ValueError as error:
        raise UnsafeUrl("the ERP address has an invalid port") from error
    try:
        addresses = resolve(parts.hostname, port)
    except OSError as error:
        raise UnsafeUrl("the ERP address does not resolve") from error
    if not addresses:
        raise UnsafeUrl("the ERP address does not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise UnsafeUrl("the ERP address leads to a private or local network")
    netloc = parts.hostname if port == 443 else f"{parts.hostname}:{port}"
    return f"https://{netloc}"

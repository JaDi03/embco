"""The Merkle tree hash of RFC 9162 (Certificate Transparency v2), section 2.1.1.

One 32-byte root sums up every entry of the journal in order. The agent writes it on Arc; anyone
holding a copy of the journal recomputes it and compares. Leaves and inner nodes get different
prefixes (0x00, 0x01) so a leaf can never pass for a node.
"""

import hashlib
from collections.abc import Sequence

LEAF, NODE = b"\x00", b"\x01"


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(LEAF + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE + left + right).digest()


def root(leaves: Sequence[bytes]) -> bytes:
    """MTH(D[n]): the hash of the empty string for no leaves; otherwise the left subtree holds
    the largest power of two below n."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    level = [leaf_hash(d) for d in leaves]
    return _subtree(level)


def _subtree(hashes: Sequence[bytes]) -> bytes:
    if len(hashes) == 1:
        return hashes[0]
    split = 1 << ((len(hashes) - 1).bit_length() - 1)
    return node_hash(_subtree(hashes[:split]), _subtree(hashes[split:]))

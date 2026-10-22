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


def inclusion_proof(leaves: Sequence[bytes], index: int) -> list[bytes]:
    """The audit path of RFC 9162 section 2.1.3.1: the hashes that, with leaf `index`, give
    the root."""
    if not 0 <= index < len(leaves):
        raise IndexError(f"leaf {index} is not in a tree of {len(leaves)} leaves")
    return _path(index, [leaf_hash(d) for d in leaves])


def _path(index: int, hashes: Sequence[bytes]) -> list[bytes]:
    if len(hashes) <= 1:
        return []
    split = 1 << ((len(hashes) - 1).bit_length() - 1)
    if index < split:
        return [*_path(index, hashes[:split]), _subtree(hashes[split:])]
    return [*_path(index - split, hashes[split:]), _subtree(hashes[:split])]


def verify_inclusion(data: bytes, index: int, size: int, proof: Sequence[bytes],
                     expected_root: bytes) -> bool:
    """RFC 9162 section 2.1.3.2: does leaf `data` sit at `index` in a tree of `size` leaves with
    this root? Never raises on a bad proof: it returns False. The root alone does not pin the
    size: a caller that needs the size must check it too (services/anchor/proof.py does, against
    the anchor on Arc)."""
    if not 0 <= index < size:
        return False
    fn, sn = index, size - 1
    r = leaf_hash(data)
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root

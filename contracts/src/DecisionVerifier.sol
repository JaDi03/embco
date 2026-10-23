// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {JournalAnchor} from "./JournalAnchor.sol";

/// @notice The part of a shop contract this verifier reads: who owns the shop.
interface IShopOwner {
    function owner() external view returns (address);
}

/// @title DecisionVerifier
/// @notice Lets anyone check one decision of a shop's agent on Arc, in the explorer's Read form,
///         with one pasted value and no wallet. The value is a proof made from the shop's journal:
///         the decision's MANIFEST entry, its RFC 9162 audit path and which anchor it is under.
///         The contract hashes the entry as the journal does (sha256 of "embco-journal-v1\n",
///         the previous entry's hash, "\n" and the entry's canonical JSON), follows the path and
///         compares the root with the one the shop's agent wrote in JournalAnchor. Only then does
///         it read the decision out of the entry it just checked and return it. It has no owner,
///         no state and nothing to upgrade.
/// @dev The MANIFEST is written by agent/memory/manifest.py. It is read at fixed places, never as
///      general JSON: canonical JSON sorts keys, every value is plain (free text is hex), so any
///      other shape is refused rather than guessed at.
contract DecisionVerifier {
    struct Result {
        bool verified;
        string answer;
        address shop;
        address owner;
        string invoice;
        string decision;
        string reason;
        string amount;
        string supplier;
        string payment;
        string paymentTx;
        string decidedAt;
        uint256 records;
        uint64 anchoredAt;
    }

    struct Proof {
        address shop;
        uint256 anchor; // position of the anchor in JournalAnchor's list for this shop
        uint256 index; // position of the MANIFEST entry in the journal, from 0
        bytes32[] path;
        bytes manifest; // the hashed text: domain, previous hash and canonical JSON
    }

    JournalAnchor public immutable journalAnchor;

    constructor(JournalAnchor journalAnchor_) {
        journalAnchor = journalAnchor_;
    }

    /// @param proof abi.encode(address shop, uint256 anchor, uint256 index, bytes32[] path,
    ///        bytes manifest), as `embco prove --for-arc` prints it.
    function verify(bytes calldata proof) external view returns (Result memory r) {
        Proof memory p = abi.decode(proof, (Proof));
        r.shop = p.shop;
        if (p.anchor >= journalAnchor.count(p.shop)) return _no(r, "this shop has no such anchor on Arc");
        JournalAnchor.Anchor memory a = journalAnchor.anchorAt(p.shop, p.anchor);
        if (!_included(sha256(p.manifest), p.index, a.size, p.path, a.root)) {
            return _no(r, "this record is not in the journal the shop's agent anchored on Arc");
        }
        Result memory read = _read(p.manifest);
        if (bytes(read.invoice).length == 0) return _no(r, "this record is not a decision manifest");
        read.verified = true;
        read.shop = p.shop;
        read.owner = _owner(p.shop);
        read.anchoredAt = a.anchoredAt;
        read.answer = string.concat(
            "Anchored on Arc and unchanged since: the agent's decision on ",
            read.invoice,
            ", which rests on ",
            _str(read.records),
            " journal records"
        );
        return read;
    }

    function _no(Result memory r, string memory why) private pure returns (Result memory) {
        r.answer = why;
        return r;
    }

    function _owner(address shop) private view returns (address) {
        try IShopOwner(shop).owner() returns (address o) {
            return o;
        } catch {
            return address(0);
        }
    }

    // ---- the MANIFEST entry, read at fixed places ----------------------------------------------

    /// @dev An empty invoice means the text is not a manifest of the expected shape.
    function _read(bytes memory t) internal pure returns (Result memory r) {
        uint256 i = 0;
        bool ok;
        (ok, i) = _expect(t, i, "embco-journal-v1\n");
        if (!ok || t.length < i + 65) return r;
        for (uint256 k = i; k < i + 64; k++) {
            if (!_isHex(t[k])) return r;
        }
        i += 64;
        string memory at;
        string memory amount;
        string memory choice;
        uint256 records;
        string memory payment;
        string memory reasonHex;
        string memory supplierHex;
        string memory paymentTx;
        string memory invoice;
        (ok, i) = _expect(t, i, '\n{"at":"');
        if (!ok) return r;
        (ok, at, i) = _until(t, i, '"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',"body":"{\\"amount\\":\\"');
        if (!ok) return r;
        (ok, amount, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"choice\\":\\"');
        if (!ok) return r;
        (ok, choice, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"entries\\":[');
        if (!ok) return r;
        (ok, records, i) = _hashes(t, i);
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"payment\\":\\"');
        if (!ok) return r;
        (ok, payment, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"reason_hex\\":\\"');
        if (!ok) return r;
        (ok, reasonHex, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"session\\":\\"');
        if (!ok) return r;
        (ok,, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"supplier_hex\\":\\"');
        if (!ok) return r;
        (ok, supplierHex, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',\\"tx\\":\\"');
        if (!ok) return r;
        (ok, paymentTx, i) = _until(t, i, '\\"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, '}","invoice":"');
        if (!ok) return r;
        (ok, invoice, i) = _until(t, i, '"');
        if (!ok) return r;
        (ok, i) = _expect(t, i, ',"kind":"MANIFEST","run":null}');
        if (!ok || i != t.length || bytes(invoice).length == 0) return r;
        bytes memory reason;
        bytes memory supplier;
        (ok, reason) = _unhex(bytes(reasonHex));
        if (!ok) return r;
        (ok, supplier) = _unhex(bytes(supplierHex));
        if (!ok) return r;
        r.invoice = invoice;
        r.decidedAt = at;
        r.amount = amount;
        r.decision = choice;
        r.records = records;
        r.payment = payment;
        r.reason = string(reason);
        r.supplier = string(supplier);
        r.paymentTx = paymentTx;
    }

    function _expect(bytes memory t, uint256 i, bytes memory word) private pure returns (bool, uint256) {
        if (t.length < i + word.length) return (false, i);
        for (uint256 k = 0; k < word.length; k++) {
            if (t[i + k] != word[k]) return (false, i);
        }
        return (true, i + word.length);
    }

    /// @dev A plain value up to `end`: no backslash and no quote inside, so nothing is escaped.
    function _until(bytes memory t, uint256 i, bytes memory end) private pure returns (bool, string memory, uint256) {
        for (uint256 j = i; j < t.length; j++) {
            if (t[j] == end[0]) {
                (bool closed, uint256 next) = _expect(t, j, end);
                if (!closed) return (false, "", i);
                bytes memory value = new bytes(j - i);
                for (uint256 k = 0; k < value.length; k++) {
                    value[k] = t[i + k];
                }
                return (true, string(value), next);
            }
            if (t[j] == "\\" || t[j] == '"') return (false, "", i);
        }
        return (false, "", i);
    }

    /// @dev The list of entry hashes: \"<64 hex>\" separated by commas, then ].
    function _hashes(bytes memory t, uint256 i) private pure returns (bool, uint256, uint256) {
        uint256 n = 0;
        bool ok;
        while (true) {
            (ok, i) = _expect(t, i, '\\"');
            if (!ok || t.length < i + 64) return (false, 0, i);
            for (uint256 k = i; k < i + 64; k++) {
                if (!_isHex(t[k])) return (false, 0, i);
            }
            (ok, i) = _expect(t, i + 64, '\\"');
            if (!ok) return (false, 0, i);
            n++;
            if (i < t.length && t[i] == ",") {
                i++;
                continue;
            }
            (ok, i) = _expect(t, i, "]");
            return (ok, n, i);
        }
        return (false, 0, i); // unreachable; keeps the compiler sure every path returns
    }

    function _isHex(bytes1 c) private pure returns (bool) {
        return (c >= "0" && c <= "9") || (c >= "a" && c <= "f");
    }

    function _unhex(bytes memory h) private pure returns (bool, bytes memory) {
        if (h.length % 2 != 0) return (false, "");
        bytes memory out = new bytes(h.length / 2);
        for (uint256 k = 0; k < out.length; k++) {
            (bool hiOk, uint8 hi) = _nibble(h[2 * k]);
            (bool loOk, uint8 lo) = _nibble(h[2 * k + 1]);
            if (!hiOk || !loOk) return (false, "");
            out[k] = bytes1(hi * 16 + lo);
        }
        return (true, out);
    }

    function _nibble(bytes1 c) private pure returns (bool, uint8) {
        if (c >= "0" && c <= "9") return (true, uint8(c) - 48);
        if (c >= "a" && c <= "f") return (true, uint8(c) - 87);
        return (false, 0);
    }

    // ---- RFC 9162 section 2.1.3.2, as agent/memory/merkle.py verify_inclusion ----------------

    function _included(bytes32 leaf, uint256 index, uint256 size, bytes32[] memory path, bytes32 root)
        private
        pure
        returns (bool)
    {
        if (index >= size) return false;
        uint256 fn = index;
        uint256 sn = size - 1;
        bytes32 r = sha256(abi.encodePacked(bytes1(0x00), leaf));
        for (uint256 k = 0; k < path.length; k++) {
            if (sn == 0) return false;
            if (fn & 1 == 1 || fn == sn) {
                r = sha256(abi.encodePacked(bytes1(0x01), path[k], r));
                if (fn & 1 == 0) {
                    while (fn != 0 && fn & 1 == 0) {
                        fn >>= 1;
                        sn >>= 1;
                    }
                }
            } else {
                r = sha256(abi.encodePacked(bytes1(0x01), r, path[k]));
            }
            fn >>= 1;
            sn >>= 1;
        }
        return sn == 0 && r == root;
    }

    function _str(uint256 v) private pure returns (string memory) {
        if (v == 0) return "0";
        uint256 digits;
        for (uint256 t = v; t != 0; t /= 10) {
            digits++;
        }
        bytes memory out = new bytes(digits);
        while (v != 0) {
            out[--digits] = bytes1(uint8(48 + v % 10));
            v /= 10;
        }
        return string(out);
    }
}

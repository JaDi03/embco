// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice The part of a shop contract this registry reads: who the shop's agent is.
interface IShopAgent {
    function agent() external view returns (address);
}

/// @title JournalAnchor
/// @notice Public record of each shop's journal on Arc. The agent writes the Merkle root
///         (RFC 9162 tree hash) of the whole journal and how many entries it covers. Anyone can
///         then recompute the root from a copy of the journal and compare: a changed, removed or
///         reordered entry gives a different root. Anchors are only added, never changed, and
///         each one must cover more entries than the last, so the journal can only grow.
///         Only the agent the shop contract names can anchor for that shop. A contract that
///         names another agent only writes under its own address, never under a real shop's.
contract JournalAnchor {
    struct Anchor {
        bytes32 root;
        uint64 size;
        uint64 anchoredAt;
    }

    mapping(address shop => Anchor[] anchors) private _anchors;

    event Anchored(address indexed shop, uint256 indexed index, bytes32 root, uint64 size, address agent);

    error NotShopAgent(address shop, address caller);
    error SizeMustGrow(uint64 last, uint64 size);

    function anchor(address shop, bytes32 root, uint64 size) external {
        if (IShopAgent(shop).agent() != msg.sender) revert NotShopAgent(shop, msg.sender);
        Anchor[] storage list = _anchors[shop];
        uint64 last = list.length == 0 ? 0 : list[list.length - 1].size;
        if (size <= last) revert SizeMustGrow(last, size);
        list.push(Anchor(root, size, uint64(block.timestamp)));
        emit Anchored(shop, list.length - 1, root, size, msg.sender);
    }

    function count(address shop) external view returns (uint256) {
        return _anchors[shop].length;
    }

    function anchorAt(address shop, uint256 index) external view returns (Anchor memory) {
        return _anchors[shop][index];
    }

    /// @notice The newest anchor, or an empty one (size 0) when the shop has none yet.
    function latest(address shop) external view returns (Anchor memory) {
        Anchor[] storage list = _anchors[shop];
        if (list.length == 0) return Anchor(bytes32(0), 0, 0);
        return list[list.length - 1];
    }
}

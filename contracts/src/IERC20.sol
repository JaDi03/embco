// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice The part of ERC-20 the contracts use. On Arc, USDC's ERC-20 interface is at
///         0x3600000000000000000000000000000000000000 and uses 6 decimals.
interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

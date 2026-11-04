// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ShopEarnFactory} from "../src/ShopEarnFactory.sol";

/// @notice Deploys the ShopEarnFactory on Arc (mainnet or testnet). Owners then create their own
///         ShopEarn from their wallets; the deployer controls nothing.
///
///     arc-forge script script/DeployEarn.s.sol --network arc --rpc-url arc_mainnet \
///         --broadcast --account <keystore-name>
contract DeployEarn is Script {
    /// @dev USDC's ERC-20 interface on Arc, the same address on mainnet and testnet (Arc docs,
    ///      contract addresses).
    function usdcFor(uint256 chainId) internal pure returns (address) {
        if (chainId == 5042) return 0x3600000000000000000000000000000000000000; // Arc Mainnet
        if (chainId == 5042002) return 0x3600000000000000000000000000000000000000; // Arc Testnet
        revert("no USDC known for this chain");
    }

    function run() external returns (ShopEarnFactory factory) {
        address usdc = usdcFor(block.chainid);
        vm.startBroadcast();
        factory = new ShopEarnFactory(IERC20(usdc));
        vm.stopBroadcast();
        console.log("ShopEarnFactory:", address(factory));
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ShopPayablesFactory} from "../src/ShopPayablesFactory.sol";

/// @notice Deploys the factory on Arc (mainnet or testnet). Shops are then created by their owners
///         from their own wallets. The deployer gets no control over the factory or the shops.
///
///     arc-forge script script/DeployFactory.s.sol --network arc --rpc-url arc_mainnet \
///         --broadcast --account <keystore-name>
contract DeployFactory is Script {
    /// @dev USDC's ERC-20 interface on Arc, the same address on mainnet and testnet (Arc docs,
    ///      contract addresses).
    function usdcFor(uint256 chainId) internal pure returns (address) {
        if (chainId == 5042) return 0x3600000000000000000000000000000000000000; // Arc Mainnet
        if (chainId == 5042002) return 0x3600000000000000000000000000000000000000; // Arc Testnet
        revert("no USDC known for this chain");
    }

    function run() external returns (ShopPayablesFactory factory) {
        address usdc = usdcFor(block.chainid);
        vm.startBroadcast();
        factory = new ShopPayablesFactory(IERC20(usdc));
        vm.stopBroadcast();
        console.log("ShopPayablesFactory:", address(factory));
    }
}

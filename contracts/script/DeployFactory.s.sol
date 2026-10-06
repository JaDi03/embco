// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "../src/IERC20.sol";
import {ShopPayablesFactory} from "../src/ShopPayablesFactory.sol";

/// @notice Deploys the factory on Arc Testnet. Shops are then created by their owners from
///         their own wallets. The deployer gets no control over the factory or the shops.
///
///     arc-forge script script/DeployFactory.s.sol --network arc --rpc-url arc_testnet \
///         --broadcast --account <keystore-name>
contract DeployFactory is Script {
    address constant ARC_TESTNET_USDC = 0x3600000000000000000000000000000000000000;

    function run() external returns (ShopPayablesFactory factory) {
        vm.startBroadcast();
        factory = new ShopPayablesFactory(IERC20(ARC_TESTNET_USDC));
        vm.stopBroadcast();
        console.log("ShopPayablesFactory:", address(factory));
    }
}

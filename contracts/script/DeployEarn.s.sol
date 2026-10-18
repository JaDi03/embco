// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ShopEarnFactory} from "../src/ShopEarnFactory.sol";

/// @notice Deploys the ShopEarnFactory on Arc Testnet. Owners then create their own ShopEarn
///         from their wallets; the deployer controls nothing.
///
///     arc-forge script script/DeployEarn.s.sol --network arc --rpc-url arc_testnet \
///         --broadcast --account <keystore-name>
contract DeployEarn is Script {
    address constant ARC_TESTNET_USDC = 0x3600000000000000000000000000000000000000;

    function run() external returns (ShopEarnFactory factory) {
        vm.startBroadcast();
        factory = new ShopEarnFactory(IERC20(ARC_TESTNET_USDC));
        vm.stopBroadcast();
        console.log("ShopEarnFactory:", address(factory));
    }
}


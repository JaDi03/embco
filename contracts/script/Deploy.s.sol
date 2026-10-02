// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Script, console} from "forge-std/Script.sol";
import {EmbcoCampaigns} from "../src/EmbcoCampaigns.sol";

/// Deploys EmbcoCampaigns to Arc Testnet. The deployer gets no special powers:
/// each campaign is owned by the agency that creates it.
///
///   forge script script/Deploy.s.sol --rpc-url arc_testnet --broadcast
///
/// Signs with DEPLOYER_PRIVATE_KEY from .env, or with `--account <keystore>` if it is empty.
contract Deploy is Script {
    uint256 constant ARC_TESTNET_CHAIN_ID = 5042002;
    address constant ARC_TESTNET_USDC = 0x3600000000000000000000000000000000000000;

    function run() external returns (EmbcoCampaigns embco) {
        require(block.chainid == ARC_TESTNET_CHAIN_ID, "not Arc Testnet");

        uint256 key = vm.envOr("DEPLOYER_PRIVATE_KEY", uint256(0));
        if (key != 0) vm.startBroadcast(key);
        else vm.startBroadcast();
        embco = new EmbcoCampaigns(ARC_TESTNET_USDC);
        vm.stopBroadcast();

        console.log("EmbcoCampaigns:", address(embco));
    }
}

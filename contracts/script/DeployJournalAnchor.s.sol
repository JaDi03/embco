// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {JournalAnchor} from "../src/JournalAnchor.sol";

/// @notice Deploys the JournalAnchor registry on Arc Testnet. It has no owner: each shop's agent
///         anchors its own journal and the deployer controls nothing.
///
///     arc-forge script script/DeployJournalAnchor.s.sol --network arc --rpc-url arc_testnet \
///         --broadcast --account <keystore-name>
contract DeployJournalAnchor is Script {
    function run() external returns (JournalAnchor registry) {
        vm.startBroadcast();
        registry = new JournalAnchor();
        vm.stopBroadcast();
        console.log("JournalAnchor:", address(registry));
    }
}

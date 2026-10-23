// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {DecisionVerifier} from "../src/DecisionVerifier.sol";
import {JournalAnchor} from "../src/JournalAnchor.sol";

/// @notice Deploys DecisionVerifier on Arc Testnet over the live JournalAnchor. It has no owner and
///         no state: the deployer controls nothing.
///
///     arc-forge script script/DeployDecisionVerifier.s.sol --network arc --rpc-url arc_testnet \
///         --broadcast --account <keystore-name>
contract DeployDecisionVerifier is Script {
    JournalAnchor constant JOURNAL_ANCHOR = JournalAnchor(0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582);

    function run() external returns (DecisionVerifier verifier) {
        vm.startBroadcast();
        verifier = new DecisionVerifier(JOURNAL_ANCHOR);
        vm.stopBroadcast();
        console.log("DecisionVerifier:", address(verifier));
    }
}

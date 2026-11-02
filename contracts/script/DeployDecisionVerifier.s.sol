// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {DecisionVerifier, IShopFactory} from "../src/DecisionVerifier.sol";
import {JournalAnchor} from "../src/JournalAnchor.sol";

/// @notice Deploys DecisionVerifier on Arc (mainnet or testnet) over that chain's JournalAnchor and
///         ShopPayablesFactory. It has no owner and no state: the deployer controls nothing. On
///         mainnet, deploy JournalAnchor and the factory first and pass their addresses:
///
///     JOURNAL_ANCHOR=<address> SHOP_FACTORY=<address> arc-forge script \
///         script/DeployDecisionVerifier.s.sol --network arc --rpc-url arc_mainnet \
///         --broadcast --account <keystore-name>
contract DeployDecisionVerifier is Script {
    function journalAnchorFor(uint256 chainId) internal view returns (JournalAnchor) {
        if (chainId == 5042) return JournalAnchor(vm.envAddress("JOURNAL_ANCHOR")); // Arc Mainnet
        if (chainId == 5042002) return JournalAnchor(0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582); // Arc Testnet
        revert("no JournalAnchor known for this chain");
    }

    function factoryFor(uint256 chainId) internal view returns (IShopFactory) {
        if (chainId == 5042) return IShopFactory(vm.envAddress("SHOP_FACTORY")); // Arc Mainnet
        if (chainId == 5042002) return IShopFactory(0x5D455dF984C85abe160E4585E0c1139ded5d2c3b); // Arc Testnet
        revert("no shop factory known for this chain");
    }

    function run() external returns (DecisionVerifier verifier) {
        JournalAnchor journalAnchor = journalAnchorFor(block.chainid);
        if (address(journalAnchor).code.length == 0) revert("JournalAnchor is not deployed on this chain");
        IShopFactory factory = factoryFor(block.chainid);
        if (address(factory).code.length == 0) revert("the shop factory is not deployed on this chain");
        vm.startBroadcast();
        verifier = new DecisionVerifier(journalAnchor, factory);
        vm.stopBroadcast();
        console.log("DecisionVerifier:", address(verifier));
    }
}

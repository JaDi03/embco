// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IGatewayDeposits} from "../src/ShopFunding.sol";
import {ShopFundingFactory} from "../src/ShopFundingFactory.sol";

/// @notice Deploys the funding factory on a source chain of the owner's Unified Balance. Owners
///         then create their own ShopFunding from their wallets; the deployer controls nothing.
///         USDC and Circle's GatewayWallet addresses come from Circle's Unified Balance Kit.
///
///     forge script script/DeployFunding.s.sol --rpc-url https://sepolia.base.org \
///         --broadcast --account <keystore-name>
contract DeployFunding is Script {
    address constant GATEWAY_WALLET_TESTNET = 0x0077777d7EBA4688BDeF3E311b846F25870A19B9;

    function usdcFor(uint256 chainId) internal pure returns (address) {
        if (chainId == 84532) return 0x036CbD53842c5426634e7929541eC2318f3dCF7e; // Base Sepolia
        if (chainId == 11155111) return 0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238; // Ethereum Sepolia
        revert("no USDC known for this chain");
    }

    function run() external returns (ShopFundingFactory factory) {
        address usdc = usdcFor(block.chainid);
        vm.startBroadcast();
        factory = new ShopFundingFactory(IERC20(usdc), IGatewayDeposits(GATEWAY_WALLET_TESTNET));
        vm.stopBroadcast();
        console.log("ShopFundingFactory:", address(factory));
    }
}

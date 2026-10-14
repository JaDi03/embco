// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IGatewayDeposits, ShopFunding} from "../src/ShopFunding.sol";
import {ShopFundingFactory} from "../src/ShopFundingFactory.sol";

/// @dev The balance view of Circle's GatewayWallet (src/modules/wallet/Balances.sol).
interface IGatewayBalances {
    function availableBalance(address token, address depositor) external view returns (uint256);
}

/// @notice Runs against a fork of Base Sepolia with Circle's real GatewayWallet and USDC (addresses
///         from Circle's Unified Balance Kit). Skipped unless BASE_SEPOLIA_RPC_URL is set.
contract GatewayForkTest is Test {
    IERC20 constant USDC = IERC20(0x036CbD53842c5426634e7929541eC2318f3dCF7e);
    address constant GATEWAY_WALLET = 0x0077777d7EBA4688BDeF3E311b846F25870A19B9;

    address owner = makeAddr("fork-owner");
    address agent = makeAddr("fork-agent");

    function test_the_agent_deposits_the_owner_usdc_into_the_owner_gateway_balance() public {
        string memory rpc = vm.envOr("BASE_SEPOLIA_RPC_URL", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
        }
        vm.createSelectFork(rpc);
        vm.prank(GATEWAY_WALLET); // it holds every deposit: a source of test USDC for the owner
        assertTrue(USDC.transfer(owner, 100e6));

        ShopFundingFactory factory = new ShopFundingFactory(USDC, IGatewayDeposits(GATEWAY_WALLET));
        vm.startPrank(owner);
        ShopFunding funding = factory.create(agent, 50e6, 80e6);
        USDC.approve(address(funding), 100e6);
        vm.stopPrank();

        uint256 before = IGatewayBalances(GATEWAY_WALLET).availableBalance(address(USDC), owner);
        vm.prank(agent);
        funding.fund(40_250_000);

        assertEq(IGatewayBalances(GATEWAY_WALLET).availableBalance(address(USDC), owner), before + 40_250_000);
        assertEq(IGatewayBalances(GATEWAY_WALLET).availableBalance(address(USDC), agent), 0);
        assertEq(USDC.balanceOf(owner), 100e6 - 40_250_000);
        assertEq(USDC.balanceOf(address(funding)), 0);
    }
}

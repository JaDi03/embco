// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC4626, ShopEarn} from "../src/ShopEarn.sol";
import {ShopEarnFactory} from "../src/ShopEarnFactory.sol";

interface IERC4626Full is IERC4626 {
    function maxDeposit(address receiver) external view returns (uint256);
}

interface IUSDC {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function decimals() external view returns (uint8);
}

/// @notice Runs against a fork of Arc Testnet with the real Morpho USDC Vault and real Arc USDC.
///         Skipped unless ARC_TESTNET_RPC_URL is set. Run with `--network arc`.
contract EarnForkTest is Test {
    IUSDC constant USDC = IUSDC(0x3600000000000000000000000000000000000000);
    IERC4626Full constant MORPHO = IERC4626Full(0x8f2D33B5D4B9B5F02DF635AE308F7B4C9dA8D2DC);
    IERC4626Full constant EARNKIT = IERC4626Full(0xAabbeF1D3971c710276ed41eC791BbE14CdB8E88);

    address owner = makeAddr("fork-owner");
    address agent = makeAddr("fork-agent");

    function test_the_agent_parks_and_unparks_in_real_morpho_vault() public {
        string memory rpc = vm.envOr("ARC_TESTNET_RPC_URL", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
        }
        vm.createSelectFork(rpc);

        // Verify EarnKit maxDeposit is 0 on Arc Testnet (auditor finding 1)
        assertEq(EARNKIT.maxDeposit(owner), 0);

        // Verify Morpho accepts deposits
        assertGt(MORPHO.maxDeposit(owner), 0);

        // Fund owner with real Arc USDC (1,000 native USDC = 1,000e6 ERC-20 USDC)
        vm.deal(owner, 1_000 ether);
        assertEq(USDC.balanceOf(owner), 1_000e6);

        ShopEarnFactory factory = new ShopEarnFactory(IERC20(address(USDC)));
        vm.startPrank(owner);
        ShopEarn earn = factory.create(agent, 300e6, 1_000e6);
        USDC.approve(address(earn), 500e6);
        earn.setVault(address(MORPHO), true);
        MORPHO.approve(address(earn), type(uint256).max);
        vm.stopPrank();

        // 1. Agent parks 100 USDC into Morpho
        vm.prank(agent);
        earn.park(address(MORPHO), 100e6);

        assertEq(USDC.balanceOf(owner), 900e6);
        uint256 shares = MORPHO.balanceOf(owner);
        assertGt(shares, 0);
        assertEq(MORPHO.balanceOf(address(earn)), 0);
        assertEq(USDC.balanceOf(address(earn)), 0);

        // 2. Agent unparks 40 USDC back to owner
        vm.prank(agent);
        earn.unpark(address(MORPHO), 40e6);

        assertApproxEqAbs(USDC.balanceOf(owner), 940e6, 2);

        // 3. Agent unparks all remaining shares
        vm.prank(agent);
        earn.unparkAll(address(MORPHO));

        assertEq(MORPHO.balanceOf(owner), 0);
        assertApproxEqAbs(USDC.balanceOf(owner), 1_000e6, 10);
    }
}

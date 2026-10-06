// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "../src/IERC20.sol";
import {ShopPayables} from "../src/ShopPayables.sol";
import {ShopPayablesFactory} from "../src/ShopPayablesFactory.sol";

interface IUSDC {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function decimals() external view returns (uint8);
}

/// @notice Runs against a fork of Arc Testnet with the real USDC, whose ERC-20 interface (6
///         decimals) and native gas balance (18 decimals) share one balance.
///         Skipped unless ARC_TESTNET_RPC_URL is set.
contract ArcForkTest is Test {
    IUSDC constant USDC = IUSDC(0x3600000000000000000000000000000000000000);

    address owner = makeAddr("fork-owner");
    address agent = makeAddr("fork-agent");
    address supplier = makeAddr("fork-supplier");

    function test_the_shop_pays_with_real_arc_usdc() public {
        string memory rpc = vm.envOr("ARC_TESTNET_RPC_URL", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
        }
        vm.createSelectFork(rpc);
        assertEq(USDC.decimals(), 6);

        vm.deal(owner, 1_000 ether); // 1,000 USDC on the native 18-decimal interface
        assertEq(USDC.balanceOf(owner), 1_000e6);

        ShopPayablesFactory factory = new ShopPayablesFactory(IERC20(address(USDC)));
        vm.startPrank(owner);
        ShopPayables shop = factory.create(agent, 300e6, 1_000e6);
        USDC.approve(address(shop), 500e6);
        shop.setPayee(supplier, true);
        vm.stopPrank();

        vm.prank(agent);
        shop.pay(supplier, 125_500_000, keccak256("ACC-PINV-2026-00013"));

        assertEq(USDC.balanceOf(supplier), 125_500_000);
        assertEq(USDC.balanceOf(owner), 1_000e6 - 125_500_000);
        assertEq(supplier.balance, 125.5 ether); // the same money seen as native USDC
    }
}

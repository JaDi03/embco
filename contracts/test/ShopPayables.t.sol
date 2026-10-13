// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ShopPayables} from "../src/ShopPayables.sol";
import {ShopPayablesFactory} from "../src/ShopPayablesFactory.sol";
import {MockUSDC, FalseUSDC} from "./MockUSDC.sol";

contract ShopPayablesTest is Test {
    uint256 constant USDC = 1e6;
    uint256 constant MAX_PER_PAYMENT = 300 * USDC;
    uint256 constant WEEKLY_CAP = 1_000 * USDC;

    MockUSDC usdc;
    ShopPayablesFactory factory;
    ShopPayables shop;

    address owner = makeAddr("owner");
    address agent = makeAddr("agent");
    address supplier = makeAddr("supplier");
    address stranger = makeAddr("stranger");

    function setUp() public {
        usdc = new MockUSDC();
        factory = new ShopPayablesFactory(IERC20(address(usdc)));
        vm.prank(owner);
        shop = factory.create(agent, MAX_PER_PAYMENT, WEEKLY_CAP);
        usdc.mint(owner, 10_000 * USDC);
        vm.startPrank(owner);
        usdc.approve(address(shop), 5_000 * USDC);
        shop.setPayee(supplier, true);
        vm.stopPrank();
    }

    function ref(string memory invoice) internal pure returns (bytes32) {
        return keccak256(bytes(invoice));
    }

    function agentPays(uint256 amount, string memory invoice) internal {
        vm.prank(agent);
        shop.pay(supplier, amount, ref(invoice));
    }

    // The factory

    function test_the_caller_of_the_factory_owns_the_shop() public view {
        assertEq(shop.owner(), owner);
        assertEq(shop.agent(), agent);
        assertEq(factory.shopsOf(owner)[0], address(shop));
        assertEq(factory.shopCount(), 1);
    }

    // Paying from the owner's wallet

    function test_the_agent_pays_an_approved_supplier_from_the_owner_wallet() public {
        vm.expectEmit(address(shop));
        emit ShopPayables.Paid(ref("PINV-1"), supplier, 100 * USDC, agent);
        agentPays(100 * USDC, "PINV-1");
        assertEq(usdc.balanceOf(supplier), 100 * USDC);
        assertEq(usdc.balanceOf(owner), 9_900 * USDC);
        assertEq(usdc.balanceOf(address(shop)), 0);
    }

    function test_an_invoice_is_never_paid_twice() public {
        agentPays(100 * USDC, "PINV-1");
        vm.expectRevert(abi.encodeWithSelector(ShopPayables.AlreadyPaid.selector, ref("PINV-1")));
        agentPays(100 * USDC, "PINV-1");
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(ShopPayables.AlreadyPaid.selector, ref("PINV-1")));
        shop.pay(supplier, 100 * USDC, ref("PINV-1"));
    }

    function test_revoking_the_approval_stops_every_payment() public {
        vm.prank(owner);
        usdc.approve(address(shop), 0);
        vm.expectRevert(bytes("allowance"));
        agentPays(100 * USDC, "PINV-1");
    }

    // The agent's limits

    function test_the_agent_cannot_pay_a_supplier_the_owner_did_not_approve() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(ShopPayables.PayeeNotApproved.selector, stranger));
        shop.pay(stranger, 10 * USDC, ref("PINV-1"));
    }

    function test_the_agent_cannot_go_over_the_per_payment_limit() public {
        vm.expectRevert(abi.encodeWithSelector(ShopPayables.OverPaymentLimit.selector, 301 * USDC, MAX_PER_PAYMENT));
        agentPays(301 * USDC, "PINV-1");
    }

    function test_the_agent_cannot_go_over_the_weekly_cap_until_next_week() public {
        agentPays(300 * USDC, "PINV-1");
        agentPays(300 * USDC, "PINV-2");
        agentPays(300 * USDC, "PINV-3");
        assertEq(shop.remainingThisWeek(), 100 * USDC);
        vm.expectRevert(abi.encodeWithSelector(ShopPayables.OverWeeklyCap.selector, 200 * USDC, 100 * USDC));
        agentPays(200 * USDC, "PINV-4");
        vm.warp(block.timestamp + 1 weeks);
        assertEq(shop.remainingThisWeek(), WEEKLY_CAP);
        agentPays(200 * USDC, "PINV-4");
    }

    function test_a_stranger_cannot_pay() public {
        vm.prank(stranger);
        vm.expectRevert(ShopPayables.NotAuthorized.selector);
        shop.pay(supplier, 1 * USDC, ref("PINV-1"));
    }

    function test_payments_need_a_payee_an_amount_and_a_reference() public {
        vm.startPrank(agent);
        vm.expectRevert(ShopPayables.InvalidPayment.selector);
        shop.pay(address(0), 1 * USDC, ref("PINV-1"));
        vm.expectRevert(ShopPayables.InvalidPayment.selector);
        shop.pay(supplier, 0, ref("PINV-1"));
        vm.expectRevert(ShopPayables.InvalidPayment.selector);
        shop.pay(supplier, 1 * USDC, bytes32(0));
        vm.stopPrank();
    }

    // The owner

    function test_the_owner_can_pay_above_the_agent_limits_and_to_any_wallet() public {
        vm.prank(owner);
        shop.pay(stranger, 2_000 * USDC, ref("PINV-BIG"));
        assertEq(usdc.balanceOf(stranger), 2_000 * USDC);
        assertEq(shop.remainingThisWeek(), WEEKLY_CAP);
    }

    function test_only_the_owner_changes_limits_agent_and_suppliers() public {
        bytes memory notOwner = abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, agent);
        vm.startPrank(agent);
        vm.expectRevert(notOwner);
        shop.setLimits(1_000 * USDC, 5_000 * USDC);
        vm.expectRevert(notOwner);
        shop.setAgent(stranger);
        vm.expectRevert(notOwner);
        shop.setPayee(stranger, true);
        vm.stopPrank();

        vm.startPrank(owner);
        shop.setLimits(50 * USDC, 100 * USDC);
        shop.setAgent(stranger);
        vm.stopPrank();
        assertEq(shop.maxPerPayment(), 50 * USDC);
        assertEq(shop.agent(), stranger);
        vm.expectRevert(ShopPayables.NotAuthorized.selector);
        agentPays(10 * USDC, "PINV-1");
    }

    function test_limits_must_make_sense() public {
        vm.startPrank(owner);
        vm.expectRevert(ShopPayables.InvalidLimits.selector);
        shop.setLimits(0, 100 * USDC);
        vm.expectRevert(ShopPayables.InvalidLimits.selector);
        shop.setLimits(200 * USDC, 100 * USDC);
        vm.expectRevert(ShopPayables.InvalidAddress.selector);
        shop.setAgent(owner);
        vm.stopPrank();
    }

    // The brake

    function test_the_agent_can_pause_itself_but_only_the_owner_resumes() public {
        vm.prank(agent);
        shop.pause();
        assertTrue(shop.paused());
        vm.expectRevert(Pausable.EnforcedPause.selector);
        agentPays(10 * USDC, "PINV-1");

        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, agent));
        shop.unpause();

        vm.prank(owner);
        shop.unpause();
        agentPays(10 * USDC, "PINV-1");
    }

    function test_pulling_the_brake_twice_changes_nothing_and_resuming_needs_a_pause() public {
        vm.prank(agent);
        shop.pause();
        vm.prank(owner);
        shop.pause();
        assertTrue(shop.paused());
        vm.prank(owner);
        shop.unpause();
        vm.prank(owner);
        vm.expectRevert(Pausable.ExpectedPause.selector);
        shop.unpause();
    }

    function test_a_stranger_cannot_pause() public {
        vm.prank(stranger);
        vm.expectRevert(ShopPayables.NotAuthorized.selector);
        shop.pause();
    }

    function test_the_owner_still_pays_while_the_agent_is_paused() public {
        vm.prank(owner);
        shop.pause();
        vm.prank(owner);
        shop.pay(supplier, 10 * USDC, ref("PINV-1"));
        assertEq(usdc.balanceOf(supplier), 10 * USDC);
    }

    // Ownership

    function test_the_ownership_cannot_be_transferred_or_renounced() public {
        vm.startPrank(owner);
        vm.expectRevert(ShopPayables.OwnershipFixed.selector);
        shop.transferOwnership(stranger);
        vm.expectRevert(ShopPayables.OwnershipFixed.selector);
        shop.renounceOwnership();
        vm.stopPrank();
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, stranger));
        shop.transferOwnership(stranger);
        assertEq(shop.owner(), owner);
    }

    function test_a_token_that_answers_false_fails_the_payment() public {
        FalseUSDC token = new FalseUSDC();
        ShopPayablesFactory other = new ShopPayablesFactory(IERC20(address(token)));
        vm.startPrank(owner);
        ShopPayables falseShop = other.create(agent, MAX_PER_PAYMENT, WEEKLY_CAP);
        falseShop.setPayee(supplier, true);
        vm.stopPrank();
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(SafeERC20.SafeERC20FailedOperation.selector, address(token)));
        falseShop.pay(supplier, 10 * USDC, ref("PINV-1"));
    }

    // Fuzz: whatever the agent tries, a week never goes over the cap

    function testFuzz_the_agent_never_spends_more_than_the_weekly_cap(uint256[8] memory amounts) public {
        uint256 total;
        for (uint256 i = 0; i < amounts.length; i++) {
            uint256 amount = bound(amounts[i], 1, MAX_PER_PAYMENT);
            vm.prank(agent);
            try shop.pay(supplier, amount, bytes32(i + 1)) {
                total += amount;
            } catch {}
        }
        assertLe(total, WEEKLY_CAP);
        assertEq(usdc.balanceOf(supplier), total);
    }
}

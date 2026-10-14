// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {IGatewayDeposits, ShopFunding} from "../src/ShopFunding.sol";
import {ShopFundingFactory} from "../src/ShopFundingFactory.sol";
import {MockUSDC} from "./MockUSDC.sol";

/// @dev Stands in for Circle's GatewayWallet: takes the tokens from the caller and credits the
///      depositor, as depositFor does.
contract MockGateway is IGatewayDeposits {
    mapping(address depositor => uint256 amount) public credited;

    function depositFor(address token, address depositor, uint256 value) external {
        MockUSDC(token).transferFrom(msg.sender, address(this), value);
        credited[depositor] += value;
    }
}

contract ShopFundingTest is Test {
    uint256 constant USDC = 1e6;
    uint256 constant MAX_PER_MOVE = 200 * USDC;
    uint256 constant WEEKLY_CAP = 500 * USDC;

    MockUSDC usdc;
    MockGateway gateway;
    ShopFundingFactory factory;
    ShopFunding funding;

    address owner = makeAddr("owner");
    address agent = makeAddr("agent");
    address stranger = makeAddr("stranger");

    function setUp() public {
        usdc = new MockUSDC();
        gateway = new MockGateway();
        factory = new ShopFundingFactory(IERC20(address(usdc)), gateway);
        vm.prank(owner);
        funding = factory.create(agent, MAX_PER_MOVE, WEEKLY_CAP);
        usdc.mint(owner, 10_000 * USDC);
        vm.prank(owner);
        usdc.approve(address(funding), 5_000 * USDC);
    }

    function agentFunds(uint256 amount) internal {
        vm.prank(agent);
        funding.fund(amount);
    }

    function test_the_caller_of_the_factory_owns_its_funding() public view {
        assertEq(funding.owner(), owner);
        assertEq(funding.agent(), agent);
        assertEq(factory.fundingsOf(owner)[0], address(funding));
    }

    function test_its_address_is_known_before_it_is_created() public {
        address next = factory.predict(stranger, agent, MAX_PER_MOVE, WEEKLY_CAP);
        assertEq(next.code.length, 0);
        vm.prank(stranger);
        ShopFunding created = factory.create(agent, MAX_PER_MOVE, WEEKLY_CAP);
        assertEq(address(created), next);
        assertEq(created.owner(), stranger);
        assertEq(factory.predict(owner, agent, MAX_PER_MOVE, WEEKLY_CAP), address(funding));
    }

    function test_the_same_owner_and_settings_cannot_create_twice() public {
        vm.prank(owner);
        vm.expectRevert();
        factory.create(agent, MAX_PER_MOVE, WEEKLY_CAP);
    }

    function test_the_agent_moves_the_owner_usdc_into_the_owner_gateway_balance() public {
        vm.expectEmit(address(funding));
        emit ShopFunding.Funded(150 * USDC, agent);
        agentFunds(150 * USDC);
        assertEq(gateway.credited(owner), 150 * USDC);
        assertEq(gateway.credited(agent), 0);
        assertEq(usdc.balanceOf(owner), 9_850 * USDC);
        assertEq(usdc.balanceOf(address(funding)), 0);
        assertEq(funding.remainingThisWeek(), 350 * USDC);
    }

    function test_the_agent_is_held_to_the_per_move_limit() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(ShopFunding.OverMoveLimit.selector, 201 * USDC, MAX_PER_MOVE));
        funding.fund(201 * USDC);
    }

    function test_the_agent_is_held_to_the_weekly_cap_until_the_week_renews() public {
        agentFunds(200 * USDC);
        agentFunds(200 * USDC);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(ShopFunding.OverWeeklyCap.selector, 150 * USDC, 100 * USDC));
        funding.fund(150 * USDC);
        vm.warp(block.timestamp + 1 weeks);
        agentFunds(150 * USDC);
        assertEq(gateway.credited(owner), 550 * USDC);
    }

    function test_the_owner_is_not_held_to_the_limits() public {
        vm.prank(owner);
        funding.fund(900 * USDC);
        assertEq(gateway.credited(owner), 900 * USDC);
        assertEq(funding.remainingThisWeek(), WEEKLY_CAP);
    }

    function test_nobody_else_can_move_the_owner_funds() public {
        vm.prank(stranger);
        vm.expectRevert(ShopFunding.NotAuthorized.selector);
        funding.fund(1 * USDC);
    }

    function test_revoking_the_approval_stops_the_agent() public {
        vm.prank(owner);
        usdc.approve(address(funding), 0);
        vm.prank(agent);
        vm.expectRevert();
        funding.fund(1 * USDC);
    }

    function test_a_pause_stops_the_agent_and_only_the_owner_resumes() public {
        vm.prank(agent);
        funding.pause();
        vm.prank(agent);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        funding.fund(1 * USDC);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, agent));
        funding.unpause();
        vm.prank(owner);
        funding.unpause();
        agentFunds(1 * USDC);
    }

    function test_only_the_owner_changes_limits_and_agent() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, agent));
        funding.setLimits(1_000 * USDC, 2_000 * USDC);
        vm.startPrank(owner);
        funding.setLimits(50 * USDC, 100 * USDC);
        funding.setAgent(stranger);
        vm.stopPrank();
        vm.prank(agent);
        vm.expectRevert(ShopFunding.NotAuthorized.selector);
        funding.fund(1 * USDC);
    }

    function test_ownership_is_fixed() public {
        vm.prank(owner);
        vm.expectRevert(ShopFunding.OwnershipFixed.selector);
        funding.transferOwnership(stranger);
    }

    function test_zero_and_bad_limits_are_refused() public {
        vm.prank(agent);
        vm.expectRevert(ShopFunding.InvalidAmount.selector);
        funding.fund(0);
        vm.prank(owner);
        vm.expectRevert(ShopFunding.InvalidLimits.selector);
        funding.setLimits(600 * USDC, 500 * USDC);
    }

    function testFuzz_the_agent_never_moves_more_than_the_week_allows(uint256 a, uint256 b) public {
        a = bound(a, 1, MAX_PER_MOVE);
        b = bound(b, 1, MAX_PER_MOVE);
        agentFunds(a);
        vm.prank(agent);
        if (a + b > WEEKLY_CAP) vm.expectRevert();
        funding.fund(b);
        assertLe(gateway.credited(owner), WEEKLY_CAP);
    }
}

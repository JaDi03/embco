// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IERC4626, ShopEarn} from "../src/ShopEarn.sol";
import {ShopEarnFactory} from "../src/ShopEarnFactory.sol";
import {MockUSDC} from "./MockUSDC.sol";

/// @dev Minimal ERC-4626 vault for testing both 6-decimal shares (Morpho USDC Vault) and
///      18-decimal shares (EarnKit USDC Vault), including yield accrual and ceiling rounding on
///      `withdraw` vs exact share burning on `redeem`.
contract MockERC4626 is IERC4626 {
    using Math for uint256;

    address public immutable override asset;
    uint8 public immutable decimals;
    uint256 private immutable _scale;

    uint256 public override totalSupply;
    mapping(address => uint256) public override balanceOf;
    mapping(address => mapping(address => uint256)) public override allowance;

    constructor(address asset_, uint8 decimals_) {
        asset = asset_;
        decimals = decimals_;
        _scale = 10 ** (decimals_ - 6);
    }

    function totalAssets() public view returns (uint256) {
        return MockUSDC(asset).balanceOf(address(this));
    }

    function approve(address spender, uint256 amount) external override returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external override returns (bool) {
        require(balanceOf[msg.sender] >= amount, "shares balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external override returns (bool) {
        require(allowance[from][msg.sender] >= amount, "shares allowance");
        require(balanceOf[from] >= amount, "shares balance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
        return true;
    }

    function convertToShares(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply;
        return supply == 0 ? assets * _scale : assets.mulDiv(supply, totalAssets(), Math.Rounding.Floor);
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply;
        return supply == 0 ? shares / _scale : shares.mulDiv(totalAssets(), supply, Math.Rounding.Floor);
    }

    function previewWithdraw(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply;
        return supply == 0 ? assets * _scale : assets.mulDiv(supply, totalAssets(), Math.Rounding.Ceil);
    }

    function deposit(uint256 assets, address receiver) external override returns (uint256 shares) {
        shares = convertToShares(assets);
        MockUSDC(asset).transferFrom(msg.sender, address(this), assets);
        totalSupply += shares;
        balanceOf[receiver] += shares;
        emit Transfer(address(0), receiver, shares);
    }

    function withdraw(uint256 assets, address receiver, address owner_) external override returns (uint256 shares) {
        shares = previewWithdraw(assets);
        _burnFrom(owner_, shares);
        _sendAssets(receiver, assets);
    }

    function redeem(uint256 shares, address receiver, address owner_) external override returns (uint256 assets) {
        assets = convertToAssets(shares);
        _burnFrom(owner_, shares);
        _sendAssets(receiver, assets);
    }

    function _burnFrom(address owner_, uint256 shares) private {
        if (msg.sender != owner_) {
            uint256 allowed = allowance[owner_][msg.sender];
            require(allowed >= shares, "shares allowance");
            if (allowed != type(uint256).max) {
                allowance[owner_][msg.sender] = allowed - shares;
            }
        }
        require(balanceOf[owner_] >= shares, "shares balance");
        balanceOf[owner_] -= shares;
        totalSupply -= shares;
        emit Transfer(owner_, address(0), shares);
    }

    function _sendAssets(address receiver, uint256 assets) private {
        require(MockUSDC(asset).balanceOf(address(this)) >= assets, "vault liquidity");
        MockUSDC(asset).approve(address(this), assets);
        MockUSDC(asset).transferFrom(address(this), receiver, assets);
    }
}

    contract ShopEarnTest is Test {
        uint256 constant USDC = 1e6;
        uint256 constant MAX_PER_PARK = 300 * USDC;
        uint256 constant WEEKLY_CAP = 1_000 * USDC;

        MockUSDC usdc;
        MockERC4626 vault6; // Morpho USDC Vault style (6-decimal shares)
        MockERC4626 vault18; // EarnKit USDC Vault style (18-decimal shares)
        ShopEarnFactory factory;
        ShopEarn earn;

        address owner = makeAddr("owner");
        address agent = makeAddr("agent");
        address stranger = makeAddr("stranger");

        function setUp() public {
            usdc = new MockUSDC();
            vault6 = new MockERC4626(address(usdc), 6);
            vault18 = new MockERC4626(address(usdc), 18);
            factory = new ShopEarnFactory(IERC20(address(usdc)));

            vm.prank(owner);
            earn = factory.create(agent, MAX_PER_PARK, WEEKLY_CAP);

            usdc.mint(owner, 10_000 * USDC);
            vm.startPrank(owner);
            usdc.approve(address(earn), 5_000 * USDC);
            earn.setVault(address(vault6), true);
            earn.setVault(address(vault18), true);
            vault6.approve(address(earn), type(uint256).max);
            vault18.approve(address(earn), type(uint256).max);
            vm.stopPrank();
        }

        function agentParks(address vault, uint256 assets) internal returns (uint256) {
            vm.prank(agent);
            return earn.park(vault, assets);
        }

        function agentUnparks(address vault, uint256 assets) internal returns (uint256) {
            vm.prank(agent);
            return earn.unpark(vault, assets);
        }

        // Factory & deterministic prediction

        function test_the_caller_of_the_factory_owns_its_earn_contract() public view {
            assertEq(earn.owner(), owner);
            assertEq(earn.agent(), agent);
            assertEq(factory.earnsOf(owner)[0], address(earn));
        }

        function test_its_address_is_known_before_it_is_created() public {
            address predicted = factory.predict(stranger, agent, MAX_PER_PARK, WEEKLY_CAP);
            assertEq(predicted.code.length, 0);
            vm.prank(stranger);
            ShopEarn created = factory.create(agent, MAX_PER_PARK, WEEKLY_CAP);
            assertEq(address(created), predicted);
            assertEq(created.owner(), stranger);
            assertEq(factory.predict(owner, agent, MAX_PER_PARK, WEEKLY_CAP), address(earn));
        }

        // Zero custody & 6-decimal / 18-decimal share vaults

        function test_park_mints_6_decimal_shares_directly_to_owner_and_leaves_zero_in_contract() public {
            vm.expectEmit(address(earn));
            emit ShopEarn.Parked(address(vault6), 200 * USDC, 200e6, agent);
            uint256 shares = agentParks(address(vault6), 200 * USDC);

            assertEq(shares, 200e6);
            assertEq(vault6.balanceOf(owner), 200e6);
            assertEq(vault6.balanceOf(address(earn)), 0);
            assertEq(vault6.balanceOf(agent), 0);
            assertEq(usdc.balanceOf(owner), 9_800 * USDC);
            assertEq(usdc.balanceOf(address(earn)), 0);
            assertEq(earn.remainingThisWeek(), 800 * USDC);
        }

        function test_park_mints_18_decimal_shares_directly_to_owner_and_unparks_cleanly() public {
            uint256 shares = agentParks(address(vault18), 250 * USDC);
            assertEq(shares, 250e18);
            assertEq(vault18.balanceOf(owner), 250e18);
            assertEq(vault18.balanceOf(address(earn)), 0);

            uint256 burned = agentUnparks(address(vault18), 100 * USDC);
            assertEq(burned, 100e18);
            assertEq(vault18.balanceOf(owner), 150e18);
            assertEq(usdc.balanceOf(owner), 9_850 * USDC);
            assertEq(usdc.balanceOf(address(earn)), 0);
        }

        function test_unparkAll_redeems_all_shares_including_accrued_yield_and_odd_rounding() public {
            // Seed another depositor so share price is non-trivial when yield accrues
            address whale = makeAddr("whale");
            usdc.mint(whale, 300 * USDC);
            vm.startPrank(whale);
            usdc.approve(address(vault18), 300 * USDC);
            vault18.deposit(300 * USDC, whale);
            vm.stopPrank();

            // Agent parks 200 USDC for the shop owner
            agentParks(address(vault18), 200 * USDC);

            // Vault earns 17.777777 USDC of interest
            usdc.mint(address(vault18), 17_777_777);

            uint256 expectedAssets = vault18.convertToAssets(vault18.balanceOf(owner));
            assertGt(expectedAssets, 200 * USDC);

            vm.prank(agent);
            uint256 redeemed = earn.unparkAll(address(vault18));

            assertEq(redeemed, expectedAssets);
            assertEq(vault18.balanceOf(owner), 0);
            assertEq(usdc.balanceOf(owner), 9_800 * USDC + expectedAssets);
            assertEq(usdc.balanceOf(address(earn)), 0);
        }

        function test_unpark_does_not_consume_weekly_cap_or_fail_over_maxPerPark() public {
            // Owner parks 900 USDC directly (above agent's maxPerPark and nearly the weeklyCap)
            vm.prank(owner);
            earn.park(address(vault6), 900 * USDC);
            assertEq(earn.remainingThisWeek(), WEEKLY_CAP);

            // Agent can still unpark 600 USDC (above maxPerPark = 300 USDC) back to the owner's wallet
            agentUnparks(address(vault6), 600 * USDC);
            assertEq(usdc.balanceOf(owner), 9_700 * USDC);
            assertEq(earn.remainingThisWeek(), WEEKLY_CAP);
        }

        function test_agent_can_still_unpark_from_a_vault_after_owner_unlist_it_for_new_parks() public {
            agentParks(address(vault6), 200 * USDC);

            vm.prank(owner);
            earn.setVault(address(vault6), false);

            // New parks are refused
            vm.prank(agent);
            vm.expectRevert(abi.encodeWithSelector(ShopEarn.VaultNotApproved.selector, address(vault6)));
            earn.park(address(vault6), 50 * USDC);

            // Bringing the owner's money back to the owner's wallet still succeeds
            agentUnparks(address(vault6), 200 * USDC);
            assertEq(usdc.balanceOf(owner), 10_000 * USDC);
        }

        // Limits & guardrails

        function test_the_agent_cannot_park_into_an_unapproved_vault() public {
            MockERC4626 unapproved = new MockERC4626(address(usdc), 6);
            vm.prank(agent);
            vm.expectRevert(abi.encodeWithSelector(ShopEarn.VaultNotApproved.selector, address(unapproved)));
            earn.park(address(unapproved), 50 * USDC);
        }

        function test_cannot_approve_a_vault_whose_asset_is_not_usdc() public {
            MockUSDC otherToken = new MockUSDC();
            MockERC4626 wrongVault = new MockERC4626(address(otherToken), 6);
            vm.prank(owner);
            vm.expectRevert(
                abi.encodeWithSelector(ShopEarn.InvalidVaultAsset.selector, address(wrongVault), address(otherToken))
            );
            earn.setVault(address(wrongVault), true);
        }

        function test_the_agent_is_held_to_maxPerPark_and_weeklyCap() public {
            vm.prank(agent);
            vm.expectRevert(abi.encodeWithSelector(ShopEarn.OverParkLimit.selector, 301 * USDC, MAX_PER_PARK));
            earn.park(address(vault6), 301 * USDC);

            agentParks(address(vault6), 300 * USDC);
            agentParks(address(vault6), 300 * USDC);
            agentParks(address(vault6), 300 * USDC);
            assertEq(earn.remainingThisWeek(), 100 * USDC);

            vm.prank(agent);
            vm.expectRevert(abi.encodeWithSelector(ShopEarn.OverWeeklyCap.selector, 150 * USDC, 100 * USDC));
            earn.park(address(vault6), 150 * USDC);

            vm.warp(block.timestamp + 1 weeks);
            assertEq(earn.remainingThisWeek(), WEEKLY_CAP);
            agentParks(address(vault6), 150 * USDC);
        }

        function test_revoking_usdc_or_share_approval_stops_park_or_unpark() public {
            agentParks(address(vault6), 100 * USDC);

            vm.prank(owner);
            vault6.approve(address(earn), 0);
            vm.prank(agent);
            vm.expectRevert(bytes("shares allowance"));
            earn.unpark(address(vault6), 50 * USDC);

            vm.prank(owner);
            usdc.approve(address(earn), 0);
            vm.prank(agent);
            vm.expectRevert(bytes("allowance"));
            earn.park(address(vault6), 50 * USDC);
        }

        function test_a_pause_stops_both_park_and_unpark_by_agent_until_owner_unpauses() public {
            agentParks(address(vault6), 100 * USDC);

            vm.prank(agent);
            earn.pause();
            assertTrue(earn.paused());

            vm.prank(agent);
            vm.expectRevert(Pausable.EnforcedPause.selector);
            earn.park(address(vault6), 50 * USDC);

            vm.prank(agent);
            vm.expectRevert(Pausable.EnforcedPause.selector);
            earn.unpark(address(vault6), 50 * USDC);

            vm.prank(agent);
            vm.expectRevert(Pausable.EnforcedPause.selector);
            earn.unparkAll(address(vault6));

            // The owner can still unpark their own funds while paused
            vm.prank(owner);
            earn.unparkAll(address(vault6));
            assertEq(usdc.balanceOf(owner), 10_000 * USDC);

            vm.prank(agent);
            vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, agent));
            earn.unpause();

            vm.prank(owner);
            earn.unpause();
            agentParks(address(vault6), 50 * USDC);
        }

        function test_stranger_cannot_park_unpark_or_pause() public {
            vm.startPrank(stranger);
            vm.expectRevert(ShopEarn.NotAuthorized.selector);
            earn.park(address(vault6), 10 * USDC);
            vm.expectRevert(ShopEarn.NotAuthorized.selector);
            earn.unpark(address(vault6), 10 * USDC);
            vm.expectRevert(ShopEarn.NotAuthorized.selector);
            earn.unparkAll(address(vault6));
            vm.expectRevert(ShopEarn.NotAuthorized.selector);
            earn.pause();
            vm.stopPrank();
        }

        function test_ownership_is_fixed() public {
            vm.startPrank(owner);
            vm.expectRevert(ShopEarn.OwnershipFixed.selector);
            earn.transferOwnership(stranger);
            vm.expectRevert(ShopEarn.OwnershipFixed.selector);
            earn.renounceOwnership();
            vm.stopPrank();
        }

        function testFuzz_the_agent_never_parks_more_than_weekly_cap(uint256 a, uint256 b) public {
            a = bound(a, 1, MAX_PER_PARK);
            b = bound(b, 1, MAX_PER_PARK);
            agentParks(address(vault18), a);
            vm.prank(agent);
            if (a + b > WEEKLY_CAP) vm.expectRevert();
            earn.park(address(vault18), b);
            assertLe(vault18.convertToAssets(vault18.balanceOf(owner)), WEEKLY_CAP);
        }
    }

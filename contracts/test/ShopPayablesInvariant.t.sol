// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ShopPayables} from "../src/ShopPayables.sol";
import {ShopPayablesFactory} from "../src/ShopPayablesFactory.sol";
import {MockUSDC} from "./MockUSDC.sol";

/// @notice Drives one shop with random calls from the agent, the owner and a stranger, and keeps
///         a record of what succeeded so the invariants can compare it with the contract.
contract PayablesHandler is Test {
    uint256 constant USDC = 1e6;

    ShopPayables public shop;
    MockUSDC public usdc;
    address public owner;
    address public agent;
    address public stranger = makeAddr("stranger");
    address[] public payees;

    mapping(bytes32 invoiceRef => uint256 count) public timesPaid;
    mapping(uint256 week => uint256 amount) public agentPaidInWeek;
    uint256 public totalPaid;
    uint256 public agentPayments;

    // Set when a call succeeded that the contract must refuse.
    bool public agentOverMaxPerPayment;
    bool public agentOverWeeklyCap;
    bool public agentPaidUnapproved;
    bool public agentPaidWhilePaused;
    bool public strangerActed;

    constructor(ShopPayables shop_, MockUSDC usdc_, address owner_, address agent_) {
        shop = shop_;
        usdc = usdc_;
        owner = owner_;
        agent = agent_;
        payees.push(makeAddr("supplier-a"));
        payees.push(makeAddr("supplier-b"));
        payees.push(makeAddr("supplier-c"));
    }

    function payeeCount() external view returns (uint256) {
        return payees.length;
    }

    function agentPays(uint256 payeeSeed, uint256 amount, uint256 refSeed) external {
        address payee = payees[payeeSeed % payees.length];
        amount = bound(amount, 1, 600 * USDC);
        bytes32 invoiceRef = _ref(refSeed);
        bool approved = shop.approvedPayee(payee);
        bool paused = shop.paused();
        uint256 maxPerPayment = shop.maxPerPayment();
        uint256 remaining = shop.remainingThisWeek();
        uint256 week = shop.currentWeek();
        vm.prank(agent);
        try shop.pay(payee, amount, invoiceRef) {
            if (!approved) agentPaidUnapproved = true;
            if (paused) agentPaidWhilePaused = true;
            if (amount > maxPerPayment) agentOverMaxPerPayment = true;
            if (amount > remaining) agentOverWeeklyCap = true;
            agentPaidInWeek[week] += amount;
            agentPayments++;
            _paid(invoiceRef, amount);
        } catch {}
    }

    function ownerPays(uint256 payeeSeed, uint256 amount, uint256 refSeed) external {
        address payee = payees[payeeSeed % payees.length];
        amount = bound(amount, 1, 600 * USDC);
        bytes32 invoiceRef = _ref(refSeed);
        vm.prank(owner);
        try shop.pay(payee, amount, invoiceRef) {
            _paid(invoiceRef, amount);
        } catch {}
    }

    function strangerPays(uint256 payeeSeed, uint256 amount, uint256 refSeed) external {
        address payee = payees[payeeSeed % payees.length];
        amount = bound(amount, 1, 600 * USDC);
        vm.prank(stranger);
        try shop.pay(payee, amount, _ref(refSeed)) {
            strangerActed = true;
        } catch {}
    }

    function strangerChanges(uint256 action, uint256 value) external {
        vm.startPrank(stranger);
        action = action % 5;
        if (action == 0) {
            try shop.setLimits(bound(value, 1, 1_000 * USDC), 2_000 * USDC) {
                strangerActed = true;
            } catch {}
        } else if (action == 1) {
            try shop.setPayee(stranger, true) {
                strangerActed = true;
            } catch {}
        } else if (action == 2) {
            try shop.setAgent(stranger) {
                strangerActed = true;
            } catch {}
        } else if (action == 3) {
            try shop.pause() {
                strangerActed = true;
            } catch {}
        } else {
            try shop.unpause() {
                strangerActed = true;
            } catch {}
        }
        vm.stopPrank();
    }

    function ownerSetsPayee(uint256 payeeSeed, bool approved) external {
        vm.prank(owner);
        shop.setPayee(payees[payeeSeed % payees.length], approved);
    }

    function ownerSetsLimits(uint256 maxPerPayment, uint256 weeklyCap) external {
        weeklyCap = bound(weeklyCap, 1, 2_000 * USDC);
        maxPerPayment = bound(maxPerPayment, 1, weeklyCap);
        vm.prank(owner);
        shop.setLimits(maxPerPayment, weeklyCap);
    }

    function agentPauses() external {
        vm.prank(agent);
        shop.pause();
    }

    function ownerUnpauses() external {
        if (!shop.paused()) return;
        vm.prank(owner);
        shop.unpause();
    }

    function timePasses(uint256 seconds_) external {
        vm.warp(block.timestamp + bound(seconds_, 1, 10 days));
    }

    function _paid(bytes32 invoiceRef, uint256 amount) private {
        timesPaid[invoiceRef]++;
        totalPaid += amount;
    }

    /// @dev Few distinct references, so the same invoice is tried again often.
    function _ref(uint256 seed) private pure returns (bytes32) {
        return keccak256(abi.encode("invoice", seed % 20));
    }

    function refFor(uint256 seed) external pure returns (bytes32) {
        return _ref(seed);
    }
}

contract ShopPayablesInvariantTest is Test {
    uint256 constant USDC = 1e6;
    uint256 constant MINTED = 1_000_000 * USDC;

    MockUSDC usdc;
    ShopPayables shop;
    PayablesHandler handler;
    address owner = makeAddr("owner");
    address agent = makeAddr("agent");

    function setUp() public {
        usdc = new MockUSDC();
        ShopPayablesFactory factory = new ShopPayablesFactory(IERC20(address(usdc)));
        vm.prank(owner);
        shop = factory.create(agent, 300 * USDC, 1_000 * USDC);
        usdc.mint(owner, MINTED);
        vm.prank(owner);
        usdc.approve(address(shop), type(uint256).max);
        handler = new PayablesHandler(shop, usdc, owner, agent);
        vm.startPrank(owner);
        shop.setPayee(handler.payees(0), true);
        shop.setPayee(handler.payees(1), true);
        vm.stopPrank();
        targetContract(address(handler));
    }

    function invariant_agentStaysWithinEachPaymentLimit() public view {
        assertFalse(handler.agentOverMaxPerPayment());
    }

    function invariant_agentStaysWithinTheWeeklyCap() public view {
        assertFalse(handler.agentOverWeeklyCap());
        uint256 week = shop.currentWeek();
        assertEq(shop.agentSpentInWeek(week), handler.agentPaidInWeek(week));
    }

    function invariant_agentPaysOnlyApprovedPayees() public view {
        assertFalse(handler.agentPaidUnapproved());
    }

    function invariant_agentCannotPayWhilePaused() public view {
        assertFalse(handler.agentPaidWhilePaused());
    }

    function invariant_strangerChangesNothing() public view {
        assertFalse(handler.strangerActed());
        assertEq(shop.owner(), owner);
        assertEq(shop.agent(), agent);
    }

    function invariant_eachInvoiceIsPaidAtMostOnce() public view {
        for (uint256 seed = 0; seed < 20; seed++) {
            bytes32 invoiceRef = handler.refFor(seed);
            uint256 times = handler.timesPaid(invoiceRef);
            assertLe(times, 1);
            assertEq(shop.paid(invoiceRef), times == 1);
        }
    }

    function invariant_moneyOnlyGoesFromOwnerToPayees() public view {
        assertEq(usdc.balanceOf(address(shop)), 0);
        uint256 payeesHold;
        for (uint256 i = 0; i < handler.payeeCount(); i++) {
            payeesHold += usdc.balanceOf(handler.payees(i));
        }
        assertEq(payeesHold, handler.totalPaid());
        assertEq(usdc.balanceOf(owner) + payeesHold, MINTED);
        assertEq(usdc.balanceOf(handler.stranger()), 0);
        assertEq(usdc.balanceOf(agent), 0);
    }
}

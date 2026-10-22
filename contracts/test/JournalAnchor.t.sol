// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {JournalAnchor} from "../src/JournalAnchor.sol";

contract FakeShop {
    address public agent;

    constructor(address agent_) {
        agent = agent_;
    }

    function setAgent(address agent_) external {
        agent = agent_;
    }
}

contract JournalAnchorTest is Test {
    JournalAnchor registry;
    FakeShop shop;
    address agent = makeAddr("agent");
    address stranger = makeAddr("stranger");

    event Anchored(address indexed shop, uint256 indexed index, bytes32 root, uint64 size, address agent);

    function setUp() public {
        registry = new JournalAnchor();
        shop = new FakeShop(agent);
    }

    function test_agentAnchorsAndAnyoneReadsIt() public {
        vm.warp(1_760_000_000);
        vm.expectEmit(true, true, false, true);
        emit Anchored(address(shop), 0, bytes32("root-1"), 10, agent);
        vm.prank(agent);
        registry.anchor(address(shop), bytes32("root-1"), 10);

        JournalAnchor.Anchor memory last = registry.latest(address(shop));
        assertEq(last.root, bytes32("root-1"));
        assertEq(last.size, 10);
        assertEq(last.anchoredAt, 1_760_000_000);
        assertEq(registry.count(address(shop)), 1);
    }

    function test_historyIsKeptInOrder() public {
        vm.startPrank(agent);
        registry.anchor(address(shop), bytes32("root-1"), 10);
        registry.anchor(address(shop), bytes32("root-2"), 25);
        vm.stopPrank();

        assertEq(registry.count(address(shop)), 2);
        assertEq(registry.anchorAt(address(shop), 0).root, bytes32("root-1"));
        assertEq(registry.anchorAt(address(shop), 1).size, 25);
        assertEq(registry.latest(address(shop)).root, bytes32("root-2"));
    }

    function test_onlyTheShopsAgentAnchors() public {
        vm.expectRevert(abi.encodeWithSelector(JournalAnchor.NotShopAgent.selector, address(shop), stranger));
        vm.prank(stranger);
        registry.anchor(address(shop), bytes32("forged"), 10);
    }

    function test_aNewAgentTakesOverAndTheOldOneCannot() public {
        vm.prank(agent);
        registry.anchor(address(shop), bytes32("root-1"), 10);
        address next = makeAddr("next");
        shop.setAgent(next);

        vm.expectRevert(abi.encodeWithSelector(JournalAnchor.NotShopAgent.selector, address(shop), agent));
        vm.prank(agent);
        registry.anchor(address(shop), bytes32("root-2"), 20);

        vm.prank(next);
        registry.anchor(address(shop), bytes32("root-2"), 20);
        assertEq(registry.count(address(shop)), 2);
    }

    function test_theJournalCanOnlyGrow() public {
        vm.startPrank(agent);
        registry.anchor(address(shop), bytes32("root-1"), 10);
        vm.expectRevert(abi.encodeWithSelector(JournalAnchor.SizeMustGrow.selector, uint64(10), uint64(10)));
        registry.anchor(address(shop), bytes32("rewritten"), 10);
        vm.expectRevert(abi.encodeWithSelector(JournalAnchor.SizeMustGrow.selector, uint64(10), uint64(4)));
        registry.anchor(address(shop), bytes32("shorter"), 4);
        vm.stopPrank();
    }

    function test_emptyShopHasNoAnchor() public view {
        JournalAnchor.Anchor memory none = registry.latest(address(shop));
        assertEq(none.size, 0);
        assertEq(none.root, bytes32(0));
    }

    function test_aFakeShopOnlyWritesUnderItsOwnAddress() public {
        FakeShop fake = new FakeShop(stranger);
        vm.prank(stranger);
        registry.anchor(address(fake), bytes32("fake"), 99);
        assertEq(registry.count(address(shop)), 0);
        assertEq(registry.count(address(fake)), 1);
    }

    function testFuzz_sizeMustStrictlyGrow(uint64 first, uint64 second) public {
        vm.assume(first > 0);
        vm.startPrank(agent);
        registry.anchor(address(shop), bytes32("a"), first);
        if (second <= first) {
            vm.expectRevert(abi.encodeWithSelector(JournalAnchor.SizeMustGrow.selector, first, second));
        }
        registry.anchor(address(shop), bytes32("b"), second);
        vm.stopPrank();
    }
}

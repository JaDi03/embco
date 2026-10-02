// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";
import {EmbcoCampaigns} from "../src/EmbcoCampaigns.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

/// @dev Drives random sequences of actions across several campaigns and
///      tracks what the contract should hold and what each worker earned.
contract Handler is Test {
    EmbcoCampaigns internal embco;
    MockUSDC internal usdc;

    address[] public agencies;
    address[] public agents;
    address[] public workers;

    uint256 public campaigns;
    uint256 public totalDeposited;
    uint256 public totalWithdrawn;
    uint256 public totalPaid;
    uint256 public paidSubmissions;
    mapping(address => uint256) public earned;

    constructor(EmbcoCampaigns embco_, MockUSDC usdc_) {
        embco = embco_;
        usdc = usdc_;
        for (uint160 i = 1; i <= 3; i++) {
            agencies.push(address(0xA000 + i));
            agents.push(address(0xB000 + i));
            usdc.mint(agencies[i - 1], type(uint128).max);
            vm.prank(agencies[i - 1]);
            usdc.approve(address(embco), type(uint256).max);
        }
        for (uint160 i = 1; i <= 6; i++) {
            workers.push(address(0xC000 + i));
        }
    }

    function create(uint256 who, uint256 reward, uint256 deposit, uint8 slots) external {
        who = bound(who, 0, 2);
        reward = bound(reward, 1, 5e6);
        deposit = bound(deposit, 0, 1_000e6);
        EmbcoCampaigns.CampaignParams memory p = EmbcoCampaigns.CampaignParams({
            agent: agents[who],
            reward: reward,
            taskCount: 5,
            maxSubmissionsPerTask: uint8(bound(slots, 1, 4)),
            capPerPeriod: reward * 10,
            workerCapPerPeriod: reward * 3,
            periodLength: 1 hours
        });
        vm.prank(agencies[who]);
        embco.createCampaign(p, deposit);
        campaigns++;
        totalDeposited += deposit;
    }

    function fund(uint256 cid, uint256 amount) external {
        if (campaigns == 0) return;
        cid = bound(cid, 0, campaigns - 1);
        amount = bound(amount, 1, 100e6);
        vm.prank(embco.getCampaign(cid).owner);
        embco.fund(cid, amount);
        totalDeposited += amount;
    }

    function withdraw(uint256 cid, uint256 amount) external {
        if (campaigns == 0) return;
        cid = bound(cid, 0, campaigns - 1);
        EmbcoCampaigns.Campaign memory c = embco.getCampaign(cid);
        if (c.balance == 0) return;
        amount = bound(amount, 1, c.balance);
        vm.prank(c.owner);
        embco.withdraw(cid, amount);
        totalWithdrawn += amount;
    }

    function submit(uint256 cid, uint256 worker, uint32 taskId) external {
        if (campaigns == 0) return;
        cid = bound(cid, 0, campaigns - 1);
        vm.prank(workers[bound(worker, 0, workers.length - 1)]);
        embco.submit(cid, uint32(bound(taskId, 0, 4)), keccak256(abi.encode(cid, worker, taskId)));
    }

    function pay(uint256 sid) external {
        if (embco.submissionCount() == 0) return;
        sid = bound(sid, 0, embco.submissionCount() - 1);
        EmbcoCampaigns.Submission memory s = embco.getSubmission(sid);
        EmbcoCampaigns.Campaign memory c = embco.getCampaign(s.campaignId);
        vm.prank(c.agent);
        try embco.pay(sid, keccak256(abi.encode("pay", sid))) {
            _recordPaid(s.worker, c.reward);
        } catch {}
    }

    function escalate(uint256 sid) external {
        if (embco.submissionCount() == 0) return;
        sid = bound(sid, 0, embco.submissionCount() - 1);
        vm.prank(embco.getCampaign(embco.getSubmission(sid).campaignId).agent);
        try embco.escalate(sid, keccak256(abi.encode("esc", sid))) {} catch {}
    }

    function approve(uint256 sid) external {
        if (embco.submissionCount() == 0) return;
        sid = bound(sid, 0, embco.submissionCount() - 1);
        EmbcoCampaigns.Submission memory s = embco.getSubmission(sid);
        EmbcoCampaigns.Campaign memory c = embco.getCampaign(s.campaignId);
        vm.prank(c.owner);
        try embco.approve(sid) {
            _recordPaid(s.worker, c.reward);
        } catch {}
    }

    function reject(uint256 sid, bool byOwner) external {
        if (embco.submissionCount() == 0) return;
        sid = bound(sid, 0, embco.submissionCount() - 1);
        EmbcoCampaigns.Campaign memory c = embco.getCampaign(embco.getSubmission(sid).campaignId);
        vm.prank(byOwner ? c.owner : c.agent);
        try embco.reject(sid, keccak256(abi.encode("rej", sid))) {} catch {}
    }

    function warp(uint256 secs) external {
        vm.warp(block.timestamp + bound(secs, 1, 2 hours));
    }

    function workerCount() external view returns (uint256) {
        return workers.length;
    }

    function _recordPaid(address worker, uint256 amount) internal {
        totalPaid += amount;
        paidSubmissions++;
        earned[worker] += amount;
    }
}

contract EmbcoCampaignsInvariantTest is Test {
    EmbcoCampaigns internal embco;
    MockUSDC internal usdc;
    Handler internal handler;

    function setUp() public {
        usdc = new MockUSDC();
        embco = new EmbcoCampaigns(address(usdc));
        handler = new Handler(embco, usdc);
        targetContract(address(handler));
    }

    /// The contract holds exactly the sum of all campaign budgets.
    function invariant_balancesMatchHoldings() public view {
        uint256 sum;
        for (uint256 i = 0; i < embco.campaignCount(); i++) {
            sum += embco.getCampaign(i).balance;
        }
        assertEq(usdc.balanceOf(address(embco)), sum);
    }

    /// Money in = money still held + money paid to workers + money returned to agencies.
    function invariant_conservation() public view {
        assertEq(
            handler.totalDeposited(), usdc.balanceOf(address(embco)) + handler.totalPaid() + handler.totalWithdrawn()
        );
    }

    /// Workers receive USDC only through paid submissions, for the fixed reward.
    function invariant_workersOnlyEarnFromTheirSubmissions() public view {
        for (uint256 i = 0; i < handler.workerCount(); i++) {
            address w = handler.workers(i);
            assertEq(usdc.balanceOf(w), handler.earned(w));
        }
    }

    /// Open (not rejected) submissions per task never exceed the campaign's slots.
    function invariant_slotsNeverExceeded() public view {
        for (uint256 i = 0; i < embco.campaignCount(); i++) {
            uint8 max = embco.getCampaign(i).maxSubmissionsPerTask;
            for (uint32 t = 0; t < 5; t++) {
                assertLe(embco.openSlotsUsed(i, t), max);
            }
        }
    }
}

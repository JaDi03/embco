// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";
import {EmbcoCampaigns} from "../src/EmbcoCampaigns.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

contract EmbcoCampaignsTest is Test {
    EmbcoCampaigns internal embco;
    MockUSDC internal usdc;

    address internal agency = makeAddr("agency");
    address internal agent = makeAddr("agent");
    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");
    address internal carol = makeAddr("carol");
    address internal stranger = makeAddr("stranger");

    uint256 internal constant REWARD = 50_000; // 0.05 USDC
    uint256 internal constant DEPOSIT = 100e6; // 100 USDC
    uint256 internal constant CAP = 1e6; // 1 USDC per period, whole campaign
    uint256 internal constant WORKER_CAP = 100_000; // 0.10 USDC per period, per worker
    uint64 internal constant PERIOD = 1 days;
    bytes32 internal constant CONTENT = keccak256("answer");
    bytes32 internal constant DECISION = keccak256("decision");

    uint256 internal cid;

    function setUp() public {
        usdc = new MockUSDC();
        embco = new EmbcoCampaigns(address(usdc));
        usdc.mint(agency, 1_000e6);
        vm.prank(agency);
        usdc.approve(address(embco), type(uint256).max);
        cid = _create(DEPOSIT);
    }

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------

    function _params() internal view returns (EmbcoCampaigns.CampaignParams memory) {
        return EmbcoCampaigns.CampaignParams({
            agent: agent,
            reward: REWARD,
            taskCount: 10,
            maxSubmissionsPerTask: 3,
            capPerPeriod: CAP,
            workerCapPerPeriod: WORKER_CAP,
            periodLength: PERIOD
        });
    }

    function _create(uint256 deposit) internal returns (uint256) {
        vm.prank(agency);
        return embco.createCampaign(_params(), deposit);
    }

    function _submit(address worker, uint32 taskId) internal returns (uint256) {
        vm.prank(worker);
        return embco.submit(cid, taskId, CONTENT);
    }

    function _pay(uint256 sid) internal {
        vm.prank(agent);
        embco.pay(sid, DECISION);
    }

    function _status(uint256 sid) internal view returns (EmbcoCampaigns.Status) {
        return embco.getSubmission(sid).status;
    }

    // -------------------------------------------------------------------
    // Setup and funding
    // -------------------------------------------------------------------

    function test_constructor_rejectsAddressWithoutCode() public {
        vm.expectRevert(EmbcoCampaigns.InvalidParams.selector);
        new EmbcoCampaigns(makeAddr("not-a-token"));
    }

    function test_create_setsCampaignAndPullsDeposit() public view {
        EmbcoCampaigns.Campaign memory c = embco.getCampaign(cid);
        assertEq(c.owner, agency);
        assertEq(c.agent, agent);
        assertEq(c.reward, REWARD);
        assertEq(c.balance, DEPOSIT);
        assertEq(usdc.balanceOf(address(embco)), DEPOSIT);
    }

    function test_create_rejectsInvalidParams() public {
        EmbcoCampaigns.CampaignParams memory p = _params();
        p.reward = 0;
        vm.prank(agency);
        vm.expectRevert(EmbcoCampaigns.InvalidParams.selector);
        embco.createCampaign(p, 0);

        p = _params();
        p.agent = address(0);
        vm.prank(agency);
        vm.expectRevert(EmbcoCampaigns.InvalidParams.selector);
        embco.createCampaign(p, 0);
    }

    function test_fund_onlyOwner() public {
        vm.prank(stranger);
        vm.expectRevert(EmbcoCampaigns.NotOwner.selector);
        embco.fund(cid, 1e6);

        vm.prank(agency);
        embco.fund(cid, 1e6);
        assertEq(embco.getCampaign(cid).balance, DEPOSIT + 1e6);
    }

    // -------------------------------------------------------------------
    // Rule 1: each campaign only spends its own budget
    // -------------------------------------------------------------------

    function test_budget_campaignsAreIsolated() public {
        uint256 poor = _create(REWARD); // budget for exactly one payment
        vm.prank(alice);
        uint256 s1 = embco.submit(poor, 0, CONTENT);
        _pay(s1);
        assertEq(embco.getCampaign(poor).balance, 0);
        assertEq(embco.getCampaign(cid).balance, DEPOSIT, "other campaign untouched");

        vm.prank(bob);
        vm.expectRevert(EmbcoCampaigns.InsufficientBudget.selector);
        embco.submit(poor, 0, CONTENT);
    }

    function test_budget_payRevertsWhenDrained() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agency);
        embco.withdraw(cid, DEPOSIT);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.InsufficientBudget.selector);
        embco.pay(sid, DECISION);
    }

    // -------------------------------------------------------------------
    // Rule 2: fixed reward, always to the submitter
    // -------------------------------------------------------------------

    function test_pay_sendsFixedRewardToSubmitter() public {
        uint256 sid = _submit(alice, 0);
        _pay(sid);
        assertEq(usdc.balanceOf(alice), REWARD);
        assertEq(embco.getCampaign(cid).balance, DEPOSIT - REWARD);
        EmbcoCampaigns.Submission memory s = embco.getSubmission(sid);
        assertEq(uint8(s.status), uint8(EmbcoCampaigns.Status.Paid));
        assertEq(s.decisionHash, DECISION);
    }

    function test_pay_onlyAgent() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agency);
        vm.expectRevert(EmbcoCampaigns.NotAgent.selector);
        embco.pay(sid, DECISION);
        vm.prank(alice);
        vm.expectRevert(EmbcoCampaigns.NotAgent.selector);
        embco.pay(sid, DECISION);
    }

    function test_pay_requiresDecisionHash() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.EmptyHash.selector);
        embco.pay(sid, bytes32(0));
    }

    function test_pay_blockedWorkerReverts() public {
        uint256 sid = _submit(alice, 0);
        usdc.setBlocked(alice, true);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.TransferFailed.selector);
        embco.pay(sid, DECISION);
        assertEq(uint8(_status(sid)), uint8(EmbcoCampaigns.Status.Submitted), "still open, agent can reject");
    }

    // -------------------------------------------------------------------
    // Rules 3 and 4: period caps
    // -------------------------------------------------------------------

    function test_workerCap_blocksThirdPaymentInPeriod() public {
        _pay(_submit(alice, 0));
        _pay(_submit(alice, 1));
        uint256 third = _submit(alice, 2);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.OverWorkerCap.selector);
        embco.pay(third, DECISION);

        vm.warp(block.timestamp + PERIOD);
        _pay(third);
        assertEq(usdc.balanceOf(alice), 3 * REWARD);
    }

    function test_periodCap_blocksCampaignWideOverspend() public {
        // CAP allows 20 payments; spread them over workers so the worker cap never binds
        for (uint160 i = 0; i < 20; i++) {
            address w = address(0x1000 + i);
            vm.prank(w);
            uint256 sid = embco.submit(cid, uint32(i % 10), CONTENT);
            _pay(sid);
        }
        vm.prank(address(0x2000));
        uint256 extra = embco.submit(cid, 0, CONTENT);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.OverPeriodCap.selector);
        embco.pay(extra, DECISION);

        vm.warp(block.timestamp + PERIOD);
        _pay(extra);
    }

    // -------------------------------------------------------------------
    // Rule 5: one submission, one payment; one submission per worker per task
    // -------------------------------------------------------------------

    function test_pay_twiceReverts() public {
        uint256 sid = _submit(alice, 0);
        _pay(sid);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.WrongStatus.selector);
        embco.pay(sid, DECISION);
    }

    function test_submit_oncePerWorkerPerTask() public {
        _submit(alice, 0);
        vm.prank(alice);
        vm.expectRevert(EmbcoCampaigns.AlreadySubmitted.selector);
        embco.submit(cid, 0, CONTENT);
    }

    function test_submit_taskFullAfterMaxWorkers() public {
        _submit(alice, 0);
        _submit(bob, 0);
        _submit(carol, 0);
        vm.prank(stranger);
        vm.expectRevert(EmbcoCampaigns.TaskFull.selector);
        embco.submit(cid, 0, CONTENT);
    }

    function test_submit_rejectionFreesSlotButNotResubmission() public {
        uint256 a = _submit(alice, 0);
        _submit(bob, 0);
        _submit(carol, 0);
        vm.prank(agent);
        embco.reject(a, DECISION);

        _submit(stranger, 0); // slot was freed
        vm.prank(alice);
        vm.expectRevert(EmbcoCampaigns.AlreadySubmitted.selector);
        embco.submit(cid, 0, CONTENT);
    }

    function test_submit_validation() public {
        vm.startPrank(alice);
        vm.expectRevert(EmbcoCampaigns.UnknownTask.selector);
        embco.submit(cid, 10, CONTENT);
        vm.expectRevert(EmbcoCampaigns.EmptyHash.selector);
        embco.submit(cid, 0, bytes32(0));
        vm.expectRevert(EmbcoCampaigns.UnknownCampaign.selector);
        embco.submit(99, 0, CONTENT);
        vm.stopPrank();
    }

    // -------------------------------------------------------------------
    // Rule 6: pause
    // -------------------------------------------------------------------

    function test_pause_agentCanPauseOnlyOwnerUnpauses() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agent);
        embco.pause(cid);

        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.IsPaused.selector);
        embco.pay(sid, DECISION);
        vm.prank(bob);
        vm.expectRevert(EmbcoCampaigns.IsPaused.selector);
        embco.submit(cid, 0, CONTENT);

        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotOwner.selector);
        embco.unpause(cid);

        vm.prank(agency);
        embco.unpause(cid);
        _pay(sid);
    }

    function test_pause_strangerCannot() public {
        vm.prank(stranger);
        vm.expectRevert(EmbcoCampaigns.NotOwnerOrAgent.selector);
        embco.pause(cid);
    }

    // -------------------------------------------------------------------
    // Rule 7: the agent only tightens
    // -------------------------------------------------------------------

    function test_lowerLimits_agentCanOnlyLower() public {
        vm.prank(agent);
        embco.lowerLimits(cid, CAP / 2, WORKER_CAP / 2);
        assertEq(embco.getCampaign(cid).capPerPeriod, CAP / 2);

        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotLower.selector);
        embco.lowerLimits(cid, CAP, WORKER_CAP / 2);
    }

    function test_setLimits_onlyOwner() public {
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotOwner.selector);
        embco.setLimits(cid, CAP * 2, WORKER_CAP * 2);

        vm.prank(agency);
        embco.setLimits(cid, CAP * 2, WORKER_CAP * 2);
        assertEq(embco.getCampaign(cid).workerCapPerPeriod, WORKER_CAP * 2);
    }

    function test_setAgent_onlyOwnerAndOldAgentLosesPower() public {
        address newAgent = makeAddr("newAgent");
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotOwner.selector);
        embco.setAgent(cid, newAgent);

        vm.prank(agency);
        embco.setAgent(cid, newAgent);
        uint256 sid = _submit(alice, 0);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotAgent.selector);
        embco.pay(sid, DECISION);
    }

    // -------------------------------------------------------------------
    // Rule 8: escalation and agency approval
    // -------------------------------------------------------------------

    function test_escalate_approvePaysOverCapToSubmitter() public {
        _pay(_submit(alice, 0));
        _pay(_submit(alice, 1));
        uint256 third = _submit(alice, 2); // over the worker cap

        vm.prank(agent);
        embco.escalate(third, DECISION);
        assertEq(uint8(_status(third)), uint8(EmbcoCampaigns.Status.Escalated));

        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.WrongStatus.selector);
        embco.pay(third, DECISION); // the agent cannot settle what it escalated

        vm.prank(agency);
        embco.approve(third);
        assertEq(usdc.balanceOf(alice), 3 * REWARD);
        assertEq(embco.getSubmission(third).decisionHash, DECISION);
    }

    function test_approve_onlyOwnerAndOnlyEscalated() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agency);
        vm.expectRevert(EmbcoCampaigns.WrongStatus.selector);
        embco.approve(sid);

        vm.prank(agent);
        embco.escalate(sid, DECISION);
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotOwner.selector);
        embco.approve(sid);
    }

    function test_approve_respectsPauseAndBudget() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agent);
        embco.escalate(sid, DECISION);

        vm.prank(agency);
        embco.pause(cid);
        vm.prank(agency);
        vm.expectRevert(EmbcoCampaigns.IsPaused.selector);
        embco.approve(sid);

        vm.startPrank(agency);
        embco.unpause(cid);
        embco.withdraw(cid, DEPOSIT);
        vm.expectRevert(EmbcoCampaigns.InsufficientBudget.selector);
        embco.approve(sid);
        vm.stopPrank();
    }

    function test_reject_agentCannotRejectEscalated_ownerCan() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(agent);
        embco.escalate(sid, DECISION);

        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.WrongStatus.selector);
        embco.reject(sid, DECISION);

        vm.prank(agency);
        embco.reject(sid, bytes32(0));
        assertEq(uint8(_status(sid)), uint8(EmbcoCampaigns.Status.Rejected));
        assertEq(embco.getSubmission(sid).decisionHash, DECISION, "agent's escalation hash kept");
    }

    function test_reject_strangerCannot() public {
        uint256 sid = _submit(alice, 0);
        vm.prank(stranger);
        vm.expectRevert(EmbcoCampaigns.NotOwnerOrAgent.selector);
        embco.reject(sid, DECISION);
    }

    // -------------------------------------------------------------------
    // Rule 9: withdrawals only to the agency
    // -------------------------------------------------------------------

    function test_withdraw_onlyOwnerToOwnAddress() public {
        vm.prank(agent);
        vm.expectRevert(EmbcoCampaigns.NotOwner.selector);
        embco.withdraw(cid, 1e6);

        uint256 before = usdc.balanceOf(agency);
        vm.prank(agency);
        embco.withdraw(cid, 1e6);
        assertEq(usdc.balanceOf(agency), before + 1e6);

        vm.prank(agency);
        vm.expectRevert(EmbcoCampaigns.InsufficientBudget.selector);
        embco.withdraw(cid, DEPOSIT);
    }

    // -------------------------------------------------------------------
    // Fuzz
    // -------------------------------------------------------------------

    /// Whatever the agent passes, the money goes to the submitter and equals the reward.
    function testFuzz_pay_payeeAndAmountAreFixed(address worker, bytes32 decision) public {
        vm.assume(worker != address(0) && worker != address(embco) && worker != agency);
        vm.assume(decision != bytes32(0));
        uint256 before = usdc.balanceOf(worker);
        vm.prank(worker);
        uint256 sid = embco.submit(cid, 0, CONTENT);
        vm.prank(agent);
        embco.pay(sid, decision);
        assertEq(usdc.balanceOf(worker), before + REWARD);
    }
}

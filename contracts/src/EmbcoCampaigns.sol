// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title EmbcoCampaigns
/// @notice Micro-task campaigns whose workers are paid in USDC by an AI agent.
///         An agency funds a campaign and fixes the reward per submission.
///         Workers register their own submissions onchain, so a payment can
///         only ever go to whoever made the submission, for the fixed reward.
///         The agent decides whether to pay, escalate or reject, inside limits
///         it can tighten but never loosen. Payments over those limits need
///         the agency's signature. Every decision carries the hash of the
///         agent's signed decision log entry.
contract EmbcoCampaigns {
    // ---------------------------------------------------------------------
    // Types
    // ---------------------------------------------------------------------

    enum Status {
        None,
        Submitted,
        Escalated,
        Paid,
        Rejected
    }

    struct CampaignParams {
        address agent;
        uint256 reward; // fixed USDC paid per accepted submission (6 decimals)
        uint32 taskCount; // valid task ids are 0..taskCount-1
        uint8 maxSubmissionsPerTask; // distinct workers per task, for consensus
        uint256 capPerPeriod; // max the agent alone may pay in one period, whole campaign
        uint256 workerCapPerPeriod; // max the agent alone may pay one worker in one period
        uint256 periodLength; // seconds
    }

    struct Campaign {
        address owner; // the agency
        address agent;
        bool paused;
        uint32 taskCount;
        uint8 maxSubmissionsPerTask;
        uint256 reward;
        uint256 balance; // USDC held for this campaign
        uint256 capPerPeriod;
        uint256 workerCapPerPeriod;
        uint256 periodLength;
        uint256 periodStart;
        uint256 spentInPeriod;
    }

    struct WorkerSpend {
        uint256 spent;
        uint256 periodStart;
    }

    struct Submission {
        uint256 campaignId;
        address worker;
        uint32 taskId;
        Status status;
        bytes32 contentHash; // hash of the answer; the answer itself stays offchain
        bytes32 decisionHash; // hash of the decision log entry that settled it
    }

    // ---------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------

    IERC20 public immutable usdc;

    uint256 public campaignCount;
    uint256 public submissionCount;

    mapping(uint256 => Campaign) internal _campaigns;
    mapping(uint256 => Submission) internal _submissions;
    mapping(uint256 => mapping(address => WorkerSpend)) internal _workerSpend;

    /// @notice Submissions per task that are not rejected (rejecting frees a slot).
    mapping(uint256 => mapping(uint32 => uint256)) public openSlotsUsed;
    mapping(uint256 => mapping(uint32 => mapping(address => bool))) public hasSubmitted;

    // ---------------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------------

    event CampaignCreated(uint256 indexed campaignId, address indexed owner, address indexed agent, uint256 reward);
    event Funded(uint256 indexed campaignId, uint256 amount);
    event Withdrawn(uint256 indexed campaignId, uint256 amount);
    event Submitted(
        uint256 indexed submissionId,
        uint256 indexed campaignId,
        address indexed worker,
        uint32 taskId,
        bytes32 contentHash
    );
    event Paid(
        uint256 indexed submissionId,
        uint256 indexed campaignId,
        address indexed worker,
        uint256 amount,
        bytes32 decisionHash,
        bool approvedByOwner
    );
    event Escalated(uint256 indexed submissionId, uint256 indexed campaignId, bytes32 decisionHash);
    event Rejected(uint256 indexed submissionId, uint256 indexed campaignId, bytes32 decisionHash, bool byOwner);
    event Paused(uint256 indexed campaignId, address indexed by);
    event Unpaused(uint256 indexed campaignId);
    event LimitsSet(uint256 indexed campaignId, uint256 capPerPeriod, uint256 workerCapPerPeriod);
    event AgentSet(uint256 indexed campaignId, address indexed agent);

    // ---------------------------------------------------------------------
    // Errors
    // ---------------------------------------------------------------------

    error InvalidParams();
    error UnknownCampaign();
    error UnknownTask();
    error NotOwner();
    error NotAgent();
    error NotOwnerOrAgent();
    error IsPaused();
    error AlreadySubmitted();
    error TaskFull();
    error EmptyHash();
    error WrongStatus();
    error InsufficientBudget();
    error OverPeriodCap();
    error OverWorkerCap();
    error NotLower();
    error TransferFailed();

    // ---------------------------------------------------------------------
    // Setup
    // ---------------------------------------------------------------------

    constructor(address usdc_) {
        // A call to an address without code "succeeds", so a wrong token address would fake every transfer
        if (usdc_.code.length == 0) revert InvalidParams();
        usdc = IERC20(usdc_);
    }

    // ---------------------------------------------------------------------
    // Agency: create and fund
    // ---------------------------------------------------------------------

    /// @notice Create a campaign owned by the caller and optionally fund it.
    ///         The caller must have approved `deposit` USDC to this contract.
    function createCampaign(CampaignParams calldata p, uint256 deposit) external returns (uint256 id) {
        if (
            p.agent == address(0) || p.reward == 0 || p.taskCount == 0 || p.maxSubmissionsPerTask == 0
                || p.periodLength == 0
        ) revert InvalidParams();

        id = campaignCount++;
        Campaign storage c = _campaigns[id];
        c.owner = msg.sender;
        c.agent = p.agent;
        c.taskCount = p.taskCount;
        c.maxSubmissionsPerTask = p.maxSubmissionsPerTask;
        c.periodLength = p.periodLength;
        c.periodStart = block.timestamp;
        c.reward = p.reward;
        c.capPerPeriod = p.capPerPeriod;
        c.workerCapPerPeriod = p.workerCapPerPeriod;

        emit CampaignCreated(id, msg.sender, p.agent, p.reward);
        emit LimitsSet(id, p.capPerPeriod, p.workerCapPerPeriod);
        if (deposit > 0) _fund(c, id, deposit);
    }

    function fund(uint256 campaignId, uint256 amount) external {
        Campaign storage c = _ownedCampaign(campaignId);
        if (amount == 0) revert InvalidParams();
        _fund(c, campaignId, amount);
    }

    /// @notice Leftover budget only goes back to the agency's own address.
    function withdraw(uint256 campaignId, uint256 amount) external {
        Campaign storage c = _ownedCampaign(campaignId);
        if (amount == 0 || amount > c.balance) revert InsufficientBudget();
        c.balance -= amount;
        emit Withdrawn(campaignId, amount);
        _transfer(msg.sender, amount);
    }

    // ---------------------------------------------------------------------
    // Worker: register a submission
    // ---------------------------------------------------------------------

    /// @notice Open to any wallet. The caller becomes the only possible payee.
    function submit(uint256 campaignId, uint32 taskId, bytes32 contentHash) external returns (uint256 id) {
        Campaign storage c = _campaign(campaignId);
        if (c.paused) revert IsPaused();
        if (taskId >= c.taskCount) revert UnknownTask();
        if (contentHash == bytes32(0)) revert EmptyHash();
        if (c.balance < c.reward) revert InsufficientBudget();
        if (hasSubmitted[campaignId][taskId][msg.sender]) revert AlreadySubmitted();
        if (openSlotsUsed[campaignId][taskId] >= c.maxSubmissionsPerTask) revert TaskFull();

        hasSubmitted[campaignId][taskId][msg.sender] = true;
        openSlotsUsed[campaignId][taskId]++;

        id = submissionCount++;
        _submissions[id] = Submission(campaignId, msg.sender, taskId, Status.Submitted, contentHash, bytes32(0));
        emit Submitted(id, campaignId, msg.sender, taskId, contentHash);
    }

    // ---------------------------------------------------------------------
    // Agent: pay inside the limits, escalate, reject, tighten
    // ---------------------------------------------------------------------

    /// @notice Pay the fixed reward to the submission's own worker. Reverts if
    ///         any limit is broken; the agent then has to escalate instead.
    function pay(uint256 submissionId, bytes32 decisionHash) external {
        (Submission storage s, Campaign storage c) = _agentSubmission(submissionId);
        if (c.paused) revert IsPaused();
        if (s.status != Status.Submitted) revert WrongStatus();
        if (decisionHash == bytes32(0)) revert EmptyHash();
        if (c.balance < c.reward) revert InsufficientBudget();

        WorkerSpend storage w = _rollPeriods(c, s.campaignId, s.worker);
        if (c.spentInPeriod + c.reward > c.capPerPeriod) revert OverPeriodCap();
        if (w.spent + c.reward > c.workerCapPerPeriod) revert OverWorkerCap();

        _settle(s, c, w, submissionId, decisionHash, false);
    }

    /// @notice Hand the decision to the agency. Allowed while paused.
    function escalate(uint256 submissionId, bytes32 decisionHash) external {
        (Submission storage s,) = _agentSubmission(submissionId);
        if (s.status != Status.Submitted) revert WrongStatus();
        if (decisionHash == bytes32(0)) revert EmptyHash();
        s.status = Status.Escalated;
        s.decisionHash = decisionHash;
        emit Escalated(submissionId, s.campaignId, decisionHash);
    }

    /// @notice The agent may reject a submission it has not escalated; the
    ///         agency may reject any open one. Rejecting frees the task slot.
    function reject(uint256 submissionId, bytes32 decisionHash) external {
        Submission storage s = _submission(submissionId);
        Campaign storage c = _campaigns[s.campaignId];
        bool byOwner = msg.sender == c.owner;
        if (byOwner) {
            if (s.status != Status.Submitted && s.status != Status.Escalated) revert WrongStatus();
        } else if (msg.sender == c.agent) {
            if (s.status != Status.Submitted) revert WrongStatus();
            if (decisionHash == bytes32(0)) revert EmptyHash();
        } else {
            revert NotOwnerOrAgent();
        }
        s.status = Status.Rejected;
        if (decisionHash != bytes32(0)) s.decisionHash = decisionHash;
        openSlotsUsed[s.campaignId][s.taskId]--;
        emit Rejected(submissionId, s.campaignId, decisionHash, byOwner);
    }

    /// @notice The agent may only tighten its own limits, never loosen them.
    function lowerLimits(uint256 campaignId, uint256 capPerPeriod, uint256 workerCapPerPeriod) external {
        Campaign storage c = _campaign(campaignId);
        if (msg.sender != c.agent) revert NotAgent();
        if (capPerPeriod > c.capPerPeriod || workerCapPerPeriod > c.workerCapPerPeriod) revert NotLower();
        _setLimits(c, campaignId, capPerPeriod, workerCapPerPeriod);
    }

    /// @notice Agency or agent can stop a campaign. Only the agency restarts it.
    function pause(uint256 campaignId) external {
        Campaign storage c = _campaign(campaignId);
        if (msg.sender != c.owner && msg.sender != c.agent) revert NotOwnerOrAgent();
        c.paused = true;
        emit Paused(campaignId, msg.sender);
    }

    // ---------------------------------------------------------------------
    // Agency: rules and approvals
    // ---------------------------------------------------------------------

    function unpause(uint256 campaignId) external {
        Campaign storage c = _ownedCampaign(campaignId);
        c.paused = false;
        emit Unpaused(campaignId);
    }

    function setLimits(uint256 campaignId, uint256 capPerPeriod, uint256 workerCapPerPeriod) external {
        Campaign storage c = _ownedCampaign(campaignId);
        _setLimits(c, campaignId, capPerPeriod, workerCapPerPeriod);
    }

    function setAgent(uint256 campaignId, address agent) external {
        Campaign storage c = _ownedCampaign(campaignId);
        if (agent == address(0)) revert InvalidParams();
        c.agent = agent;
        emit AgentSet(campaignId, agent);
    }

    /// @notice Pay an escalated submission. Skips the period and per-worker
    ///         caps, but not the budget, the fixed reward, the payee or the
    ///         pause. It still counts toward the period spend.
    function approve(uint256 submissionId) external {
        Submission storage s = _submission(submissionId);
        Campaign storage c = _campaigns[s.campaignId];
        if (msg.sender != c.owner) revert NotOwner();
        if (c.paused) revert IsPaused();
        if (s.status != Status.Escalated) revert WrongStatus();
        if (c.balance < c.reward) revert InsufficientBudget();

        WorkerSpend storage w = _rollPeriods(c, s.campaignId, s.worker);
        _settle(s, c, w, submissionId, s.decisionHash, true);
    }

    // ---------------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------------

    function getCampaign(uint256 campaignId) external view returns (Campaign memory) {
        return _campaigns[campaignId];
    }

    function getSubmission(uint256 submissionId) external view returns (Submission memory) {
        return _submissions[submissionId];
    }

    function getWorkerSpend(uint256 campaignId, address worker) external view returns (WorkerSpend memory) {
        return _workerSpend[campaignId][worker];
    }

    // ---------------------------------------------------------------------
    // Internal
    // ---------------------------------------------------------------------

    function _campaign(uint256 campaignId) internal view returns (Campaign storage c) {
        c = _campaigns[campaignId];
        if (c.owner == address(0)) revert UnknownCampaign();
    }

    function _ownedCampaign(uint256 campaignId) internal view returns (Campaign storage c) {
        c = _campaign(campaignId);
        if (msg.sender != c.owner) revert NotOwner();
    }

    function _submission(uint256 submissionId) internal view returns (Submission storage s) {
        s = _submissions[submissionId];
        if (s.status == Status.None) revert WrongStatus();
    }

    function _agentSubmission(uint256 submissionId) internal view returns (Submission storage s, Campaign storage c) {
        s = _submission(submissionId);
        c = _campaigns[s.campaignId];
        if (msg.sender != c.agent) revert NotAgent();
    }

    function _setLimits(Campaign storage c, uint256 campaignId, uint256 capPerPeriod, uint256 workerCapPerPeriod)
        internal
    {
        c.capPerPeriod = capPerPeriod;
        c.workerCapPerPeriod = workerCapPerPeriod;
        emit LimitsSet(campaignId, capPerPeriod, workerCapPerPeriod);
    }

    function _rollPeriods(Campaign storage c, uint256 campaignId, address worker)
        internal
        returns (WorkerSpend storage w)
    {
        // Periods last hours or days, so a validator nudging the timestamp by seconds doesn't matter
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp >= c.periodStart + c.periodLength) {
            c.periodStart = block.timestamp;
            c.spentInPeriod = 0;
        }
        w = _workerSpend[campaignId][worker];
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp >= w.periodStart + c.periodLength) {
            w.periodStart = block.timestamp;
            w.spent = 0;
        }
    }

    function _settle(
        Submission storage s,
        Campaign storage c,
        WorkerSpend storage w,
        uint256 submissionId,
        bytes32 decisionHash,
        bool byOwner
    ) internal {
        uint256 amount = c.reward;
        s.status = Status.Paid;
        s.decisionHash = decisionHash;
        c.balance -= amount;
        c.spentInPeriod += amount;
        w.spent += amount;
        emit Paid(submissionId, s.campaignId, s.worker, amount, decisionHash, byOwner);
        _transfer(s.worker, amount);
    }

    function _fund(Campaign storage c, uint256 campaignId, uint256 amount) internal {
        c.balance += amount;
        emit Funded(campaignId, amount);
        (bool ok, bytes memory data) =
            address(usdc).call(abi.encodeCall(IERC20.transferFrom, (msg.sender, address(this), amount)));
        if (!ok || (data.length > 0 && !abi.decode(data, (bool)))) revert TransferFailed();
    }

    function _transfer(address to, uint256 amount) internal {
        (bool ok, bytes memory data) = address(usdc).call(abi.encodeCall(IERC20.transfer, (to, amount)));
        if (!ok || (data.length > 0 && !abi.decode(data, (bool)))) revert TransferFailed();
    }
}

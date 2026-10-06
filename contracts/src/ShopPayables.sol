// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "./IERC20.sol";

/// @title ShopPayables
/// @notice Lets a payables agent pay a shop's suppliers in USDC from the shop owner's own wallet,
///         within limits that only the owner can change.
/// @dev The money never sits in this contract. The owner approves it to spend USDC from the
///      owner's wallet; revoking that approval stops every payment at once. The agent can only
///      pay approved suppliers, once per invoice, within a per-payment and a weekly cap. The owner
///      can pay anything through the same function, which is how a large payment gets the
///      owner's signature. Nobody, the agent included, can move the limits but the owner.
contract ShopPayables {
    IERC20 public immutable usdc;
    address public immutable owner;
    uint256 public immutable startedAt;

    address public agent;
    uint256 public maxPerPayment;
    uint256 public weeklyCap;
    bool public paused;

    mapping(address payee => bool) public approvedPayee;
    mapping(bytes32 invoiceRef => bool) public paid;
    mapping(uint256 week => uint256 amount) public agentSpentInWeek;

    event Paid(bytes32 indexed invoiceRef, address indexed payee, uint256 amount, address indexed by);
    event LimitsChanged(uint256 maxPerPayment, uint256 weeklyCap);
    event AgentChanged(address indexed agent);
    event PayeeSet(address indexed payee, bool approved);
    event Paused(address indexed by);
    event Unpaused();

    error NotOwner();
    error NotAuthorized();
    error IsPaused();
    error PayeeNotApproved(address payee);
    error AlreadyPaid(bytes32 invoiceRef);
    error OverPaymentLimit(uint256 amount, uint256 maxPerPayment);
    error OverWeeklyCap(uint256 amount, uint256 remaining);
    error InvalidLimits();
    error InvalidAddress();
    error InvalidPayment();
    error TransferFailed();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(IERC20 usdc_, address owner_, address agent_, uint256 maxPerPayment_, uint256 weeklyCap_) {
        if (address(usdc_) == address(0) || owner_ == address(0)) revert InvalidAddress();
        usdc = usdc_;
        owner = owner_;
        startedAt = block.timestamp;
        _setAgent(agent_);
        _setLimits(maxPerPayment_, weeklyCap_);
    }

    /// @notice Pay one invoice. The agent is held to the limits; the owner is not.
    /// @param invoiceRef A unique reference for the invoice, for example a hash of its ERP name.
    function pay(address payee, uint256 amount, bytes32 invoiceRef) external {
        if (payee == address(0) || amount == 0 || invoiceRef == bytes32(0)) revert InvalidPayment();
        if (paid[invoiceRef]) revert AlreadyPaid(invoiceRef);
        if (msg.sender == agent) {
            _checkAgentPayment(payee, amount);
        } else if (msg.sender != owner) {
            revert NotAuthorized();
        }
        paid[invoiceRef] = true;
        emit Paid(invoiceRef, payee, amount, msg.sender);
        if (!usdc.transferFrom(owner, payee, amount)) revert TransferFailed();
    }

    /// @notice Stop the agent. The owner or the agent itself can pull this brake.
    function pause() external {
        if (msg.sender != owner && msg.sender != agent) revert NotAuthorized();
        paused = true;
        emit Paused(msg.sender);
    }

    /// @notice Only the owner can let the agent pay again.
    function unpause() external onlyOwner {
        paused = false;
        emit Unpaused();
    }

    function setLimits(uint256 maxPerPayment_, uint256 weeklyCap_) external onlyOwner {
        _setLimits(maxPerPayment_, weeklyCap_);
    }

    function setAgent(address agent_) external onlyOwner {
        _setAgent(agent_);
    }

    /// @notice Approve or remove a supplier wallet the agent may pay.
    function setPayee(address payee, bool approved) external onlyOwner {
        if (payee == address(0)) revert InvalidAddress();
        approvedPayee[payee] = approved;
        emit PayeeSet(payee, approved);
    }

    function currentWeek() public view returns (uint256) {
        return (block.timestamp - startedAt) / 1 weeks;
    }

    /// @notice How much the agent may still pay this week.
    function remainingThisWeek() public view returns (uint256) {
        uint256 spent = agentSpentInWeek[currentWeek()];
        return spent >= weeklyCap ? 0 : weeklyCap - spent;
    }

    function _checkAgentPayment(address payee, uint256 amount) private {
        if (paused) revert IsPaused();
        if (!approvedPayee[payee]) revert PayeeNotApproved(payee);
        if (amount > maxPerPayment) revert OverPaymentLimit(amount, maxPerPayment);
        uint256 remaining = remainingThisWeek();
        if (amount > remaining) revert OverWeeklyCap(amount, remaining);
        agentSpentInWeek[currentWeek()] += amount;
    }

    function _setLimits(uint256 maxPerPayment_, uint256 weeklyCap_) private {
        if (maxPerPayment_ == 0 || maxPerPayment_ > weeklyCap_) revert InvalidLimits();
        maxPerPayment = maxPerPayment_;
        weeklyCap = weeklyCap_;
        emit LimitsChanged(maxPerPayment_, weeklyCap_);
    }

    function _setAgent(address agent_) private {
        if (agent_ == owner) revert InvalidAddress();
        agent = agent_;
        emit AgentChanged(agent_);
    }
}

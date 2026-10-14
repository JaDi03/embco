// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @dev The deposit entry of Circle Gateway's GatewayWallet (circlefin/evm-gateway-contracts,
///      src/modules/wallet/Deposits.sol): takes `value` of `token` from the caller and credits it
///      to `depositor`'s Gateway balance.
interface IGatewayDeposits {
    function depositFor(address token, address depositor, uint256 value) external;
}

/// @title ShopFunding
/// @notice Lets a payables agent move the shop owner's USDC on this chain into the owner's own
///         Circle Gateway balance, within limits only the owner can change. From there the agent,
///         as the owner's delegate, brings it to the owner's wallet on Arc to pay invoices.
/// @dev The money never stays here: one call takes it from the owner's wallet (the owner
///      approves this contract, and revoking that approval stops it) and deposits it for the
///      owner in the same transaction. There is no other destination. The agent is held to a
///      per-move and a weekly cap; the owner is not. Ownership is fixed.
contract ShopFunding is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable usdc;
    IGatewayDeposits public immutable gateway;
    uint256 public immutable startedAt;

    address public agent;
    uint256 public maxPerMove;
    uint256 public weeklyCap;

    mapping(uint256 week => uint256 amount) public agentMovedInWeek;

    event Funded(uint256 amount, address indexed by);
    event LimitsChanged(uint256 maxPerMove, uint256 weeklyCap);
    event AgentChanged(address indexed agent);

    error NotAuthorized();
    error OverMoveLimit(uint256 amount, uint256 maxPerMove);
    error OverWeeklyCap(uint256 amount, uint256 remaining);
    error InvalidLimits();
    error InvalidAddress();
    error InvalidAmount();
    error OwnershipFixed();

    constructor(
        IERC20 usdc_,
        IGatewayDeposits gateway_,
        address owner_,
        address agent_,
        uint256 maxPerMove_,
        uint256 weeklyCap_
    ) Ownable(owner_) {
        if (address(usdc_) == address(0) || address(gateway_) == address(0)) {
            revert InvalidAddress();
        }
        usdc = usdc_;
        gateway = gateway_;
        startedAt = block.timestamp;
        _setAgent(agent_);
        _setLimits(maxPerMove_, weeklyCap_);
    }

    /// @notice Move `amount` of the owner's USDC into the owner's Gateway balance.
    function fund(uint256 amount) external nonReentrant {
        if (amount == 0) revert InvalidAmount();
        if (msg.sender == agent) {
            _checkAgentMove(amount);
        } else if (msg.sender != owner()) {
            revert NotAuthorized();
        }
        address owner_ = owner();
        emit Funded(amount, msg.sender);
        usdc.safeTransferFrom(owner_, address(this), amount);
        usdc.forceApprove(address(gateway), amount);
        gateway.depositFor(address(usdc), owner_, amount);
    }

    /// @notice Stop the agent. The owner or the agent itself can pull this brake.
    function pause() external {
        if (msg.sender != owner() && msg.sender != agent) revert NotAuthorized();
        if (!paused()) _pause();
    }

    /// @notice Only the owner can let the agent move funds again.
    function unpause() external onlyOwner {
        _unpause();
    }

    function setLimits(uint256 maxPerMove_, uint256 weeklyCap_) external onlyOwner {
        _setLimits(maxPerMove_, weeklyCap_);
    }

    function setAgent(address agent_) external onlyOwner {
        _setAgent(agent_);
    }

    function currentWeek() public view returns (uint256) {
        return (block.timestamp - startedAt) / 1 weeks;
    }

    /// @notice How much the agent may still move this week.
    function remainingThisWeek() public view returns (uint256) {
        uint256 moved = agentMovedInWeek[currentWeek()];
        return moved >= weeklyCap ? 0 : weeklyCap - moved;
    }

    function transferOwnership(address) public view override onlyOwner {
        revert OwnershipFixed();
    }

    function renounceOwnership() public view override onlyOwner {
        revert OwnershipFixed();
    }

    function _checkAgentMove(uint256 amount) private {
        _requireNotPaused();
        if (amount > maxPerMove) revert OverMoveLimit(amount, maxPerMove);
        uint256 remaining = remainingThisWeek();
        if (amount > remaining) revert OverWeeklyCap(amount, remaining);
        agentMovedInWeek[currentWeek()] += amount;
    }

    function _setLimits(uint256 maxPerMove_, uint256 weeklyCap_) private {
        if (maxPerMove_ == 0 || maxPerMove_ > weeklyCap_) revert InvalidLimits();
        maxPerMove = maxPerMove_;
        weeklyCap = weeklyCap_;
        emit LimitsChanged(maxPerMove_, weeklyCap_);
    }

    function _setAgent(address agent_) private {
        if (agent_ == owner()) revert InvalidAddress();
        agent = agent_;
        emit AgentChanged(agent_);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @dev Minimal ERC-4626 tokenized vault interface used by ShopEarn.
interface IERC4626 is IERC20 {
    function asset() external view returns (address);
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets);
}

/// @title ShopEarn
/// @notice Lets a payables agent park the shop owner's idle USDC into owner-approved ERC-4626
///         Earn vaults on Arc and unpark it back to the owner's wallet when invoices come due,
///         within limits only the owner can change.
/// @dev Zero custody: neither USDC nor vault shares ever sit in this contract.
///      - On `park`: USDC is pulled from `owner()` and deposited into the vault with `receiver = owner()`
///        in the same transaction. Vault shares are minted directly to the owner's wallet.
///      - On `unpark` / `unparkAll`: the owner's vault shares are burned (via the share allowance the
///        owner grants this contract) and the redeemed USDC goes directly to `owner()`.
///      - The agent is held to `approvedVault`, `maxPerPark`, `weeklyCap`, and `paused()` on `park`.
///      - `unpark` and `unparkAll` always return USDC to `owner()` and do not consume the weekly park
///        cap or require the per-park limit, so the agent can always recall funds to pay invoices.
contract ShopEarn is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable usdc;
    uint256 public immutable startedAt;

    address public agent;
    uint256 public maxPerPark;
    uint256 public weeklyCap;

    mapping(address vault => bool) public approvedVault;
    mapping(uint256 week => uint256 amount) public agentParkedInWeek;

    event Parked(address indexed vault, uint256 assets, uint256 shares, address indexed by);
    event Unparked(address indexed vault, uint256 assets, uint256 shares, address indexed by);
    event VaultSet(address indexed vault, bool approved);
    event LimitsChanged(uint256 maxPerPark, uint256 weeklyCap);
    event AgentChanged(address indexed agent);

    error NotAuthorized();
    error VaultNotApproved(address vault);
    error InvalidVaultAsset(address vault, address asset);
    error OverParkLimit(uint256 amount, uint256 maxPerPark);
    error OverWeeklyCap(uint256 amount, uint256 remaining);
    error InvalidLimits();
    error InvalidAddress();
    error InvalidAmount();
    error ZeroShares();
    error ZeroAssets();
    error OwnershipFixed();

    constructor(IERC20 usdc_, address owner_, address agent_, uint256 maxPerPark_, uint256 weeklyCap_) Ownable(owner_) {
        if (address(usdc_) == address(0)) revert InvalidAddress();
        usdc = usdc_;
        startedAt = block.timestamp;
        _setAgent(agent_);
        _setLimits(maxPerPark_, weeklyCap_);
    }

    /// @notice Deposit `assets` of the owner's USDC into `vault`, minting vault shares directly to `owner()`.
    function park(address vault, uint256 assets) external nonReentrant returns (uint256 shares) {
        if (vault == address(0)) revert InvalidAddress();
        if (assets == 0) revert InvalidAmount();
        if (msg.sender == agent) {
            _checkAgentPark(vault, assets);
        } else if (msg.sender == owner()) {
            _checkVaultAsset(vault);
        } else {
            revert NotAuthorized();
        }
        address owner_ = owner();
        usdc.safeTransferFrom(owner_, address(this), assets);
        usdc.forceApprove(vault, assets);
        shares = IERC4626(vault).deposit(assets, owner_);
        if (shares == 0) revert ZeroShares();
        emit Parked(vault, assets, shares, msg.sender);
    }

    /// @notice Withdraw exact `assets` of USDC from `vault` back to `owner()`, burning the owner's shares.
    ///         Does not count against `weeklyCap` or `maxPerPark`.
    function unpark(address vault, uint256 assets) external nonReentrant returns (uint256 shares) {
        if (vault == address(0)) revert InvalidAddress();
        if (assets == 0) revert InvalidAmount();
        _checkUnparkCaller(vault);
        address owner_ = owner();
        shares = IERC4626(vault).withdraw(assets, owner_, owner_);
        if (shares == 0) revert ZeroShares();
        emit Unparked(vault, assets, shares, msg.sender);
    }

    /// @notice Redeem all of the owner's shares in `vault` back to `owner()` in USDC, avoiding
    ///         ERC-4626 `previewWithdraw` ceiling rounding reverts or leftover dust shares.
    function unparkAll(address vault) external nonReentrant returns (uint256 assets) {
        if (vault == address(0)) revert InvalidAddress();
        _checkUnparkCaller(vault);
        address owner_ = owner();
        uint256 shares = IERC4626(vault).balanceOf(owner_);
        if (shares == 0) revert ZeroShares();
        assets = IERC4626(vault).redeem(shares, owner_, owner_);
        if (assets == 0) revert ZeroAssets();
        emit Unparked(vault, assets, shares, msg.sender);
    }

    /// @notice Stop the agent. The owner or the agent itself can pull this brake.
    function pause() external {
        if (msg.sender != owner() && msg.sender != agent) revert NotAuthorized();
        if (!paused()) _pause();
    }

    /// @notice Only the owner can let the agent act again.
    function unpause() external onlyOwner {
        _unpause();
    }

    /// @notice Approve or remove an ERC-4626 vault the agent may park USDC into.
    function setVault(address vault, bool approved) external onlyOwner {
        if (vault == address(0)) revert InvalidAddress();
        if (approved) _checkVaultAsset(vault);
        approvedVault[vault] = approved;
        emit VaultSet(vault, approved);
    }

    function setLimits(uint256 maxPerPark_, uint256 weeklyCap_) external onlyOwner {
        _setLimits(maxPerPark_, weeklyCap_);
    }

    function setAgent(address agent_) external onlyOwner {
        _setAgent(agent_);
    }

    function currentWeek() public view returns (uint256) {
        return (block.timestamp - startedAt) / 1 weeks;
    }

    /// @notice How much the agent may still park this week.
    function remainingThisWeek() public view returns (uint256) {
        uint256 parked = agentParkedInWeek[currentWeek()];
        return parked >= weeklyCap ? 0 : weeklyCap - parked;
    }

    function transferOwnership(address) public view override onlyOwner {
        revert OwnershipFixed();
    }

    function renounceOwnership() public view override onlyOwner {
        revert OwnershipFixed();
    }

    function _checkAgentPark(address vault, uint256 assets) private {
        _requireNotPaused();
        if (!approvedVault[vault]) revert VaultNotApproved(vault);
        if (assets > maxPerPark) revert OverParkLimit(assets, maxPerPark);
        uint256 remaining = remainingThisWeek();
        if (assets > remaining) revert OverWeeklyCap(assets, remaining);
        agentParkedInWeek[currentWeek()] += assets;
    }

    function _checkUnparkCaller(address vault) private view {
        if (msg.sender == agent) {
            _requireNotPaused();
            if (!approvedVault[vault]) _checkVaultAsset(vault);
        } else if (msg.sender == owner()) {
            _checkVaultAsset(vault);
        } else {
            revert NotAuthorized();
        }
    }

    function _checkVaultAsset(address vault) private view {
        address asset = IERC4626(vault).asset();
        if (asset != address(usdc)) revert InvalidVaultAsset(vault, asset);
    }

    function _setLimits(uint256 maxPerPark_, uint256 weeklyCap_) private {
        if (maxPerPark_ == 0 || maxPerPark_ > weeklyCap_) revert InvalidLimits();
        maxPerPark = maxPerPark_;
        weeklyCap = weeklyCap_;
        emit LimitsChanged(maxPerPark_, weeklyCap_);
    }

    function _setAgent(address agent_) private {
        if (agent_ == owner()) revert InvalidAddress();
        agent = agent_;
        emit AgentChanged(agent_);
    }
}

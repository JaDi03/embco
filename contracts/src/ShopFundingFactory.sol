// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Create2} from "@openzeppelin/contracts/utils/Create2.sol";
import {IGatewayDeposits, ShopFunding} from "./ShopFunding.sol";

/// @title ShopFundingFactory
/// @notice Each shop owner creates their own ShopFunding on this chain, with their own wallet.
///         The caller becomes the owner; the factory keeps no control over what it creates and
///         only lists them so the agent and the dashboard can find them. Each one is created
///         with CREATE2 under the owner's address, so its address is known before it exists:
///         the dashboard can create it and approve it in one batch the owner confirms once.
contract ShopFundingFactory {
    IERC20 public immutable usdc;
    IGatewayDeposits public immutable gateway;

    mapping(address owner => address[] fundings) private _fundingsOf;

    event FundingCreated(
        address indexed owner, address indexed funding, address agent, uint256 maxPerMove, uint256 weeklyCap
    );

    constructor(IERC20 usdc_, IGatewayDeposits gateway_) {
        usdc = usdc_;
        gateway = gateway_;
    }

    function create(address agent, uint256 maxPerMove, uint256 weeklyCap) external returns (ShopFunding funding) {
        funding = new ShopFunding{salt: _salt(msg.sender)}(usdc, gateway, msg.sender, agent, maxPerMove, weeklyCap);
        _fundingsOf[msg.sender].push(address(funding));
        emit FundingCreated(msg.sender, address(funding), agent, maxPerMove, weeklyCap);
    }

    function fundingsOf(address owner) external view returns (address[] memory) {
        return _fundingsOf[owner];
    }

    /// @notice The address `create` gives the owner's ShopFunding with these settings.
    function predict(address owner, address agent, uint256 maxPerMove, uint256 weeklyCap)
        external
        view
        returns (address)
    {
        bytes memory init = abi.encodePacked(
            type(ShopFunding).creationCode, abi.encode(usdc, gateway, owner, agent, maxPerMove, weeklyCap)
        );
        return Create2.computeAddress(_salt(owner), keccak256(init));
    }

    function _salt(address owner) private pure returns (bytes32) {
        return bytes32(uint256(uint160(owner)));
    }
}

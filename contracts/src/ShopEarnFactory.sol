// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Create2} from "@openzeppelin/contracts/utils/Create2.sol";
import {ShopEarn} from "./ShopEarn.sol";

/// @title ShopEarnFactory
/// @notice Each shop owner creates their own ShopEarn on Arc, with their own wallet.
///         The caller becomes the owner; the factory keeps no control over what it creates and
///         only lists them so the agent and the dashboard can find them. Each one is created
///         with CREATE2 under the owner's address, so its address is known before it exists:
///         the dashboard can create it, approve vaults, and approve USDC and shares in one batch.
contract ShopEarnFactory {
    IERC20 public immutable usdc;

    mapping(address owner => address[] earns) private _earnsOf;

    event EarnCreated(
        address indexed owner, address indexed earn, address agent, uint256 maxPerPark, uint256 weeklyCap
    );

    constructor(IERC20 usdc_) {
        usdc = usdc_;
    }

    function create(address agent, uint256 maxPerPark, uint256 weeklyCap) external returns (ShopEarn earn) {
        earn = new ShopEarn{salt: _salt(msg.sender)}(usdc, msg.sender, agent, maxPerPark, weeklyCap);
        _earnsOf[msg.sender].push(address(earn));
        emit EarnCreated(msg.sender, address(earn), agent, maxPerPark, weeklyCap);
    }

    function earnsOf(address owner) external view returns (address[] memory) {
        return _earnsOf[owner];
    }

    /// @notice The address `create` gives the owner's ShopEarn with these settings.
    function predict(address owner, address agent, uint256 maxPerPark, uint256 weeklyCap)
        external
        view
        returns (address)
    {
        bytes memory init =
            abi.encodePacked(type(ShopEarn).creationCode, abi.encode(usdc, owner, agent, maxPerPark, weeklyCap));
        return Create2.computeAddress(_salt(owner), keccak256(init));
    }

    function _salt(address owner) private pure returns (bytes32) {
        return bytes32(uint256(uint160(owner)));
    }
}


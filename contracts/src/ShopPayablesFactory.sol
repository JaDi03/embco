// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "./IERC20.sol";
import {ShopPayables} from "./ShopPayables.sol";

/// @title ShopPayablesFactory
/// @notice Each shop owner creates their own ShopPayables from here, with their own wallet.
///         The caller becomes the owner; the factory keeps no control over what it creates and
///         only lists the shops so they can be found.
contract ShopPayablesFactory {
    IERC20 public immutable usdc;

    address[] public allShops;
    mapping(address owner => address[] shops) private _shopsOf;

    event ShopCreated(
        address indexed owner, address indexed shop, address agent, uint256 maxPerPayment, uint256 weeklyCap
    );

    constructor(IERC20 usdc_) {
        usdc = usdc_;
    }

    function create(address agent, uint256 maxPerPayment, uint256 weeklyCap) external returns (ShopPayables shop) {
        shop = new ShopPayables(usdc, msg.sender, agent, maxPerPayment, weeklyCap);
        allShops.push(address(shop));
        _shopsOf[msg.sender].push(address(shop));
        emit ShopCreated(msg.sender, address(shop), agent, maxPerPayment, weeklyCap);
    }

    function shopsOf(address owner) external view returns (address[] memory) {
        return _shopsOf[owner];
    }

    function shopCount() external view returns (uint256) {
        return allShops.length;
    }
}

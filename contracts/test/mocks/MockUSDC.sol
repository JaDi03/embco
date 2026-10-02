// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @dev Minimal 6-decimal ERC-20 standing in for Arc's USDC. `blocked` mimics
///      USDC's blocklist, where a transfer to a blocked address reverts.
contract MockUSDC {
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    mapping(address => bool) public blocked;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function setBlocked(address account, bool isBlocked) external {
        blocked[account] = isBlocked;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        _move(from, to, amount);
        return true;
    }

    function _move(address from, address to, uint256 amount) internal {
        require(!blocked[from] && !blocked[to], "blocked");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {JournalAnchor} from "../src/JournalAnchor.sol";
import {DecisionVerifier, IShopFactory} from "../src/DecisionVerifier.sol";
import {ShopPayables} from "../src/ShopPayables.sol";
import {ShopPayablesFactory} from "../src/ShopPayablesFactory.sol";
import {MockUSDC} from "./MockUSDC.sol";

contract OwnedShop {
    address public agent;
    address public owner;

    constructor(address agent_, address owner_) {
        agent = agent_;
        owner = owner_;
    }
}

/// @notice Stands in for the factory, so the fixture's shop (at a fixed address) can be listed.
contract ShopList {
    mapping(address owner => address[] shops) private _shopsOf;

    function add(address owner, address shop) external {
        _shopsOf[owner].push(shop);
    }

    function shopsOf(address owner) external view returns (address[] memory) {
        return _shopsOf[owner];
    }
}

/// @notice Reads manifest texts directly, to try shapes that could never be anchored.
contract Reader is DecisionVerifier {
    constructor() DecisionVerifier(JournalAnchor(address(0)), IShopFactory(address(0))) {}

    function read(bytes memory t) external pure returns (Result memory) {
        return _read(t);
    }
}

/// @notice The fixture is made by tests/test_arc_proof.py from a synthetic journal: the same
///         value Python makes is the one the contract checks.
contract DecisionVerifierTest is Test {
    JournalAnchor registry;
    DecisionVerifier verifier;
    address agent = makeAddr("agent");
    address owner = makeAddr("owner");
    address shop;
    bytes proof;
    string json;

    function setUp() public {
        json = vm.readFile("test/fixtures/decision-proof.json");
        shop = vm.parseJsonAddress(json, ".shop");
        proof = vm.parseJsonBytes(json, ".proof");
        registry = new JournalAnchor();
        ShopList shops = new ShopList();
        verifier = new DecisionVerifier(registry, IShopFactory(address(shops)));
        deployCodeTo("DecisionVerifier.t.sol:OwnedShop", abi.encode(agent, owner), shop);
        shops.add(owner, shop);
        vm.warp(1_760_000_000);
        _anchorFixture(shop, agent);
    }

    /// @dev Writes the fixture's two anchors under `shop_`, signed by `agent_`.
    function _anchorFixture(address shop_, address agent_) internal {
        for (uint256 i = 0; i < 2; i++) {
            string memory at = string.concat(".anchors[", vm.toString(i), "]");
            vm.prank(agent_);
            registry.anchor(
                shop_,
                vm.parseJsonBytes32(json, string.concat(at, ".root")),
                uint64(vm.parseJsonUint(json, string.concat(at, ".size")))
            );
        }
    }

    function test_theDecisionPythonProvedIsVerifiedAndRead() public view {
        DecisionVerifier.Result memory r = verifier.verify(proof);
        assertTrue(r.verified, r.answer);
        assertEq(r.shop, shop);
        assertEq(r.owner, owner);
        assertEq(r.invoice, vm.parseJsonString(json, ".expected.invoice"));
        assertEq(r.decision, vm.parseJsonString(json, ".expected.decision"));
        assertEq(r.reason, vm.parseJsonString(json, ".expected.reason"));
        assertEq(r.amount, vm.parseJsonString(json, ".expected.amount"));
        assertEq(r.supplier, vm.parseJsonString(json, ".expected.supplier"));
        assertEq(r.payment, vm.parseJsonString(json, ".expected.payment"));
        assertEq(r.paymentTx, vm.parseJsonString(json, ".expected.paymentTx"));
        assertEq(r.decidedAt, vm.parseJsonString(json, ".expected.decidedAt"));
        assertEq(r.records, vm.parseJsonUint(json, ".expected.records"));
        assertEq(r.anchoredAt, 1_760_000_000);
        assertEq(
            r.answer,
            "Anchored on Arc and unchanged since: the agent's decision on PINV-1, which rests on 8 journal records"
        );
    }

    function _parts() internal view returns (DecisionVerifier.Proof memory p) {
        p = abi.decode(proof, (DecisionVerifier.Proof));
    }

    function test_oneChangedLetterIsRefusedAndNothingIsRead() public view {
        DecisionVerifier.Proof memory p = _parts();
        bytes memory t = p.manifest;
        for (uint256 i = 0; i + 7 < t.length; i++) {
            if (t[i] == "P" && t[i + 1] == "A" && t[i + 2] == "Y" && t[i + 3] == "_") {
                t[i + 4] = "L"; // PAY_NOW becomes PAY_LOW
                break;
            }
        }
        DecisionVerifier.Result memory r = verifier.verify(abi.encode(p));
        assertFalse(r.verified);
        assertEq(r.answer, "this record is not in the journal the shop's agent anchored on Arc");
        assertEq(bytes(r.decision).length, 0);
    }

    function test_aChangedAuditPathIsRefused() public view {
        DecisionVerifier.Proof memory p = _parts();
        p.path[0] = bytes32(uint256(p.path[0]) ^ 1);
        assertFalse(verifier.verify(abi.encode(p)).verified);
    }

    function test_anotherAnchorOrAnotherShopIsRefused() public {
        DecisionVerifier.Proof memory p = _parts();
        p.anchor = 0; // the earlier anchor, which does not cover the manifest
        assertFalse(verifier.verify(abi.encode(p)).verified);
        p = _parts();
        p.anchor = 2;
        assertEq(verifier.verify(abi.encode(p)).answer, "this shop has no such anchor on Arc");
        p = _parts();
        p.shop = address(new OwnedShop(agent, owner)); // a shop with no anchors
        assertFalse(verifier.verify(abi.encode(p)).verified);
    }

    function test_aShopWithoutAnOwnerFunctionIsRefused() public {
        vm.etch(shop, address(new Reader()).code); // the anchored shop now has no owner()
        DecisionVerifier.Result memory r = verifier.verify(proof);
        assertFalse(r.verified);
        assertEq(r.answer, "this shop was not created by the shop factory");
    }

    /// @dev A contract can name any agent and any owner. Here an attacker's contract names the
    ///      real owner and anchors a journal under its own address; the verifier must not vouch
    ///      for it, or the audit page would show the real owner beside a record they never made.
    function test_aContractClaimingARealOwnerIsRefused() public {
        address attacker = makeAddr("attacker");
        address fake = address(new OwnedShop(attacker, owner));
        _anchorFixture(fake, attacker);
        DecisionVerifier.Proof memory p = _parts();
        p.shop = fake;
        DecisionVerifier.Result memory r = verifier.verify(abi.encode(p));
        assertFalse(r.verified);
        assertEq(r.answer, "this shop was not created by the shop factory");
        assertEq(r.owner, address(0));
        assertEq(bytes(r.invoice).length, 0);
    }

    function test_aShopFromTheRealFactoryVerifies() public {
        ShopPayablesFactory factory = new ShopPayablesFactory(IERC20(address(new MockUSDC())));
        DecisionVerifier withFactory = new DecisionVerifier(registry, IShopFactory(address(factory)));
        vm.prank(owner);
        ShopPayables real = factory.create(agent, 1, 1);
        _anchorFixture(address(real), agent);
        DecisionVerifier.Proof memory p = _parts();
        p.shop = address(real);
        DecisionVerifier.Result memory r = withFactory.verify(abi.encode(p));
        assertTrue(r.verified, r.answer);
        assertEq(r.owner, owner);
        // the fixture's shop is not in this factory, so this verifier refuses it
        assertEq(withFactory.verify(proof).answer, "this shop was not created by the shop factory");
    }

    function test_onlyTheExactManifestShapeIsRead() public {
        Reader reader = new Reader();
        bytes memory good = _parts().manifest;
        assertEq(reader.read(good).invoice, "PINV-1");
        // an escaped quote smuggled into a value, or any other kind of entry, reads as nothing
        bytes memory smuggled = _replace(good, "\\\"choice\\\":\\\"PAY_NOW", "\\\"choice\\\":\\\"PAY\\\"_NOW");
        assertEq(bytes(reader.read(smuggled).invoice).length, 0);
        bytes memory other = _replace(good, "\"kind\":\"MANIFEST\"", "\"kind\":\"AGENT\"");
        assertEq(bytes(reader.read(other).invoice).length, 0);
        bytes memory longer = bytes.concat(good, " ");
        assertEq(bytes(reader.read(longer).invoice).length, 0);
        bytes memory badHex = _replace(good, "\\\"reason_hex\\\":\\\"6f", "\\\"reason_hex\\\":\\\"zz");
        assertEq(bytes(reader.read(badHex).invoice).length, 0);
    }

    function _replace(bytes memory t, bytes memory from, bytes memory to) internal pure returns (bytes memory) {
        for (uint256 i = 0; i + from.length <= t.length; i++) {
            bool same = true;
            for (uint256 k = 0; k < from.length && same; k++) {
                same = t[i + k] == from[k];
            }
            if (same) {
                bytes memory head = new bytes(i);
                for (uint256 k = 0; k < i; k++) {
                    head[k] = t[k];
                }
                bytes memory tail = new bytes(t.length - i - from.length);
                for (uint256 k = 0; k < tail.length; k++) {
                    tail[k] = t[i + from.length + k];
                }
                return bytes.concat(head, to, tail);
            }
        }
        revert("not found");
    }
}

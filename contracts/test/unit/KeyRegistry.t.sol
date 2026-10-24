// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";

contract KeyRegistryTest is Test {
    KeyRegistry internal reg;
    address internal admin = makeAddr("admin");
    address internal ctrlA = makeAddr("ctrlA");
    address internal k1 = makeAddr("k1");
    address internal k2 = makeAddr("k2");
    bytes32 internal constant ORG_A = "OBS_A";
    bytes32 internal constant ORG_B = "OBS_B";

    function setUp() public {
        vm.warp(1_780_000_000);
        reg = new KeyRegistry(admin);
        vm.prank(admin);
        reg.setOrgController(ORG_A, ctrlA);
    }

    function test_register_and_views() public {
        vm.prank(admin);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 1, 0);
        assertEq(reg.orgOf(k1), ORG_A);
        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp));
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.SELLER, block.timestamp)); // wrong role
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp - 1)); // before registration
        assertEq(reg.orgKeys(ORG_A).length, 1);
    }

    function test_rotate_keepsHistory() public {
        vm.prank(ctrlA);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 1, 0);
        uint256 t1 = block.timestamp;
        vm.warp(t1 + 100);
        vm.prank(ctrlA);
        reg.rotateKey(k1, k2, 2, keccak256("mldsa-pk"));

        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, t1 + 99)); // history: valid before rotation
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, t1 + 100));
        assertTrue(reg.isValidAt(k2, KeyRegistry.Role.OBSERVER, t1 + 100));
        KeyRegistry.Key memory old = reg.getKey(k1);
        assertEq(old.replacedBy, k2);
        assertEq(old.validUntil, t1 + 100);
        assertEq(reg.getKey(k2).pqPubKeyHash, keccak256("mldsa-pk"));
        assertEq(reg.orgKeys(ORG_A).length, 2);

        // A closed key cannot be rotated again or re-registered.
        vm.prank(ctrlA);
        vm.expectRevert(abi.encodeWithSelector(KeyRegistry.KeyNotActive.selector, k1));
        reg.rotateKey(k1, makeAddr("k3"), 1, 0);
        vm.prank(ctrlA);
        vm.expectRevert(abi.encodeWithSelector(KeyRegistry.KeyExists.selector, k1));
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 1, 0);
    }

    function test_revoke_bySelf_controller() public {
        vm.startPrank(ctrlA);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 1, 0);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k2, 1, 0);
        vm.stopPrank();
        vm.warp(block.timestamp + 1 days);

        vm.prank(k1);
        reg.revokeKey(k1);
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp));
        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp - 1));

        vm.prank(k1); // a key cannot revoke another key
        vm.expectRevert(KeyRegistry.NotAuthorized.selector);
        reg.revokeKey(k2);
        vm.prank(ctrlA);
        reg.revokeKey(k2);
    }

    function test_accessControl_and_renounce() public {
        vm.prank(ctrlA); // controller of A cannot register into B
        vm.expectRevert(KeyRegistry.NotAuthorized.selector);
        reg.registerKey(ORG_B, KeyRegistry.Role.OBSERVER, k1, 1, 0);

        vm.prank(admin);
        reg.renounceAdmin();
        assertEq(reg.admin(), address(0));

        vm.startPrank(admin);
        vm.expectRevert(KeyRegistry.NotAuthorized.selector);
        reg.registerKey(ORG_B, KeyRegistry.Role.OBSERVER, k1, 1, 0);
        vm.expectRevert(KeyRegistry.NotAuthorized.selector);
        reg.setOrgController(ORG_B, admin);
        vm.expectRevert(KeyRegistry.NotAuthorized.selector);
        reg.renounceAdmin();
        vm.stopPrank();

        vm.prank(ctrlA); // org controllers keep managing their own keys
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 1, 0);
    }

    function test_invalidParams() public {
        vm.startPrank(admin);
        vm.expectRevert(KeyRegistry.InvalidParams.selector);
        reg.registerKey(ORG_A, KeyRegistry.Role.NONE, k1, 1, 0);
        vm.expectRevert(KeyRegistry.InvalidParams.selector);
        reg.registerKey(bytes32(0), KeyRegistry.Role.OBSERVER, k1, 1, 0);
        vm.expectRevert(KeyRegistry.InvalidParams.selector);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 2, 0); // hybrid needs a PQ key hash
        vm.expectRevert(KeyRegistry.InvalidParams.selector);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 1, keccak256("x")); // ECDSA must not carry one
        vm.expectRevert(KeyRegistry.InvalidParams.selector);
        reg.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, k1, 3, 0);
        vm.stopPrank();
    }

    function test_holdsNoFunds() public {
        vm.deal(address(this), 1 ether);
        (bool ok,) = address(reg).call{value: 1 ether}("");
        assertFalse(ok);
    }
}

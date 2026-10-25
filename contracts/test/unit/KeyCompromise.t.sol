// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Fixture} from "../utils/Fixture.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Attestation, KIND_DETECT} from "../../src/lib/Attestation.sol";

/// @notice What a stolen or revoked key can and cannot do, on a world built the way `contracts/script/Deploy.s.sol`
///         builds the Sepolia main deployment: each organisation's controller is its first key (`_org`), then the
///         admin renounces. Backs docs/SECURITY.md section 8 (controller permanence; parties fixed at creation).
contract KeyCompromiseTest is Fixture {
    bytes32 internal E1;
    uint32 internal constant RATE = 2548; // tier 2
    uint64 internal detected;

    function setUp() public override {
        super.setUp();
        // Replace the Fixture's registry and covenant with a Deploy.s.sol-shaped world.
        vm.startPrank(admin);
        registry = new KeyRegistry(admin);
        _org(ORG_SELLER, KeyRegistry.Role.SELLER, seller);
        _org(ORG_BUYER, KeyRegistry.Role.BUYER, buyer);
        _org(ORG_A, KeyRegistry.Role.OBSERVER, obsA);
        _org(ORG_B, KeyRegistry.Role.OBSERVER, obsB);
        _org(ORG_C, KeyRegistry.Role.OBSERVER, obsC);
        registry.renounceAdmin();
        vm.stopPrank();

        cov = new MethaneCovenant(registry, PROPOSER_BOND, CHALLENGER_BOND);
        MethaneCovenant.CovenantParams memory p = _params();
        vm.prank(seller);
        covId = cov.createCovenant{value: BOND}(p);

        E1 = sha256("bundle TKM_S_276 2026-06-11 2548 kg/h");
        detected = uint64(block.timestamp + 10 days);
        vm.warp(block.timestamp + 11 days);
    }

    /// @dev Deploy.s.sol `_org`: the controller is the organisation's first key.
    function _org(bytes32 orgId, KeyRegistry.Role role, address key) internal {
        registry.setOrgController(orgId, key);
        registry.registerKey(orgId, role, key, 1, 0);
    }

    // ------------------------------------------------------------------ controller permanence

    /// @notice In v1 an organisation's controller is fixed once the admin renounces: revoking the first key closes it
    ///         as a signer, but the same address stays the controller and can register a new OBSERVER key, which then
    ///         counts toward a quorum. Answered by KeyRegistryV2's removal vote (`test_v2_removal_closesKeys`) or by
    ///         the parties naming a new registry; production uses a cold controller key per organisation.
    function test_revokedFirstKey_staysController_registersNewObserver() public {
        assertEq(registry.admin(), address(0), "admin renounced");
        assertEq(registry.orgController(ORG_A), obsA, "controller = first key (Deploy.s.sol pattern)");

        vm.prank(obsA);
        registry.revokeKey(obsA);
        assertFalse(registry.isValidAt(obsA, KeyRegistry.Role.OBSERVER, block.timestamp), "first key closed");
        assertEq(registry.orgController(ORG_A), obsA, "still the controller");

        (address fresh, uint256 freshPk) = makeAddrAndKey("attackerObsA");
        vm.prank(obsA);
        registry.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, fresh, 1, 0);
        assertTrue(registry.isValidAt(fresh, KeyRegistry.Role.OBSERVER, block.timestamp), "new key active");
        assertEq(registry.orgOf(fresh), ORG_A);

        // The new key counts for OBS_A in a 2-of-n quorum with OBS_B.
        uint256 id = _proposeWith(freshPk, obsBPk);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.PROPOSED));
        assertEq(cov.getEpisode(id).quorumOrgs, 2);
    }

    // ------------------------------------------------------------------ parties are addresses fixed at creation

    /// @notice A covenant's seller is an address fixed at `createCovenant`; the registry gates creation, not the
    ///         seller's later calls. A revoked seller key can still `contest` and is still credited the remaining
    ///         bond at `settle`; it cannot create a new covenant.
    function test_revokedSellerKey_stillContests_andIsCreditedAtSettle() public {
        uint256 id = _proposeWith(obsAPk, obsBPk);
        uint256 held = cov.getEpisode(id).rebate;
        assertGt(held, 0);

        vm.prank(seller);
        registry.revokeKey(seller); // a compromise report by the key itself
        assertFalse(registry.isValidAt(seller, KeyRegistry.Role.SELLER, block.timestamp));

        vm.prank(seller);
        cov.contest(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONTESTED), "revoked seller key still contests");

        // Every later createCovenant from the revoked key is refused.
        MethaneCovenant.CovenantParams memory p = _params();
        vm.prank(seller);
        vm.expectRevert(MethaneCovenant.NotAuthorized.selector);
        cov.createCovenant{value: 1 ether}(p);

        // Unanswered contest expires: the held rebate returns to the bond.
        vm.warp(block.timestamp + CONTEST_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.EXPIRED));

        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        vm.warp(uint256(c.end) + CHALLENGE_WINDOW);
        uint256 before = cov.withdrawable(seller);
        cov.settle(covId);
        assertEq(cov.withdrawable(seller) - before, BOND, "remaining bond credited to the fixed seller address");
    }

    /// @notice A covenant's buyer is an address fixed at `createCovenant`. Rotating the buyer organisation's key in
    ///         the registry does not move the payee: the rebate is credited to the address named at creation.
    function test_rotatedBuyerKey_rebateStillCreditedToOldAddress() public {
        address newBuyer = makeAddr("buyerRotated");
        vm.prank(buyer); // the buyer org's controller (its first key)
        registry.rotateKey(buyer, newBuyer, 1, 0);
        assertFalse(registry.isValidAt(buyer, KeyRegistry.Role.BUYER, block.timestamp), "old buyer key closed");
        assertTrue(registry.isValidAt(newBuyer, KeyRegistry.Role.BUYER, block.timestamp), "new buyer key active");

        uint256 id = _proposeWith(obsAPk, obsBPk);
        uint256 rebate = cov.getEpisode(id).rebate;
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));

        assertEq(cov.getCovenant(covId).buyer, buyer, "payee fixed at creation");
        assertEq(cov.withdrawable(buyer), rebate, "rebate credited to the creation-time buyer address");
        assertEq(cov.withdrawable(newBuyer), 0, "rotated key receives nothing");
        vm.prank(buyer);
        cov.withdraw();
        assertEq(buyer.balance, rebate);
    }

    // ------------------------------------------------------------------ helpers

    function _proposeWith(uint256 pk1, uint256 pk2) internal returns (uint256) {
        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(pk1, pk2), SRC_TKM, E1, RATE, detected, KIND_DETECT);
        vm.prank(proposer);
        return cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, _proof(sources, SRC_TKM), E1, atts, sigs);
    }
}

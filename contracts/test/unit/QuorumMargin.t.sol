// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Fixture} from "../utils/Fixture.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Attestation, KIND_CONFIRM} from "../../src/lib/Attestation.sol";

/// @notice The production quorum rule n >= k + 2 (docs/PRODUCTION.md) on the unchanged v1 core: a covenant with k = 2
///         whose registry holds n = 4 independent observer organisations (Fixture: OBS_A, OBS_B, OBS_C, OBS_D).
///         A contest needs k + 1 = 3 confirmations, so one organisation can stay silent and the rebate still pays;
///         a challenge needs k = 2, so two can. With only k answering a contest, the episode expires and the held
///         rebate returns to the bond (nobody is slashed): the margin is what closes that gap.
contract QuorumMarginTest is Fixture {
    uint32 internal constant RATE = 2548; // tier 2
    uint8 internal constant REASON_STALE = 5;
    bytes32 internal E1;
    uint64 internal detected;

    function setUp() public override {
        super.setUp();
        E1 = sha256("bundle TKM_S_276 2026-06-11 2548 kg/h");
        detected = uint64(block.timestamp + 10 days);
        vm.warp(block.timestamp + 11 days);
    }

    function _confirmations(uint256 id, uint256[] memory pks)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs)
    {
        MethaneCovenant.Episode memory e = cov.getEpisode(id);
        (atts, sigs) = _batch(pks, e.sourceId, e.evidenceHash, e.emissionKgH, e.detectedAt, KIND_CONFIRM);
    }

    function test_quorumMargin_fourIndependentObserverOrgs_kTwo() public view {
        assertEq(cov.getCovenant(covId).k, 2);
        address[4] memory keys = [obsA, obsB, obsC, obsD];
        bytes32[4] memory orgs;
        uint256 n;
        for (uint256 i; i < 4; ++i) {
            (bytes32 org, KeyRegistry.Role role, bool valid) = registry.keyStatus(keys[i], block.timestamp);
            assertEq(uint8(role), uint8(KeyRegistry.Role.OBSERVER));
            assertTrue(valid);
            assertTrue(org != ORG_SELLER && org != ORG_BUYER);
            for (uint256 j; j < i; ++j) {
                assertTrue(orgs[j] != org); // n = 4 distinct organisations
            }
            orgs[i] = org;
            ++n;
        }
        assertGe(n, uint256(cov.getCovenant(covId).k) + 2); // n >= k + 2
    }

    /// (a) Contested by the seller, OBS_D silent: k + 1 = 3 of the 4 confirm -> CONFIRMED, buyer paid.
    function test_quorumMargin_contested_oneOrgSilent_kPlusOneConfirm_confirmed() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(seller);
        cov.contest(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONTESTED));

        (Attestation[] memory atts, bytes[] memory sigs) = _confirmations(id, _pks(obsAPk, obsBPk, obsCPk));
        cov.submitResolution(id, atts, sigs);

        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));
        assertEq(cov.withdrawable(buyer), TIER2);
        assertEq(cov.withdrawable(proposer), PROPOSER_BOND);
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.remaining, BOND - TIER2);
        assertEq(c.held, 0);
        assertEq(c.paid, TIER2);
    }

    /// (b) Challenged with a deposit, OBS_C and OBS_D silent: k = 2 confirm -> CONFIRMED; the challenger's deposit
    ///     goes to the buyer.
    function test_quorumMargin_challenged_twoOrgsSilent_kConfirm_confirmed() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, REASON_STALE);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CHALLENGED));

        (Attestation[] memory atts, bytes[] memory sigs) = _confirmations(id, _pks(obsAPk, obsBPk));
        cov.submitResolution(id, atts, sigs);

        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));
        assertEq(cov.withdrawable(buyer), TIER2 + CHALLENGER_BOND);
        assertEq(cov.withdrawable(challenger), 0);
        assertEq(cov.getCovenant(covId).remaining, BOND - TIER2);
    }

    /// (c) The gap the margin closes: contested, only k = 2 organisations answer. Their confirmation is refused
    ///     (status 0, nothing changes), the contest window runs out, the episode EXPIRES and the held rebate returns
    ///     to the seller's bond; nobody is slashed.
    function test_quorumMargin_contested_onlyKAnswer_expires_rebateBackToBond() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        assertEq(cov.getCovenant(covId).held, TIER2);
        vm.prank(seller);
        cov.contest(id);

        (Attestation[] memory atts, bytes[] memory sigs) = _confirmations(id, _pks(obsAPk, obsBPk));
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.QuorumNotMet.selector, 0, K + 1));
        cov.submitResolution(id, atts, sigs);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONTESTED)); // fail closed: nothing half-written

        vm.warp(block.timestamp + CONTEST_WINDOW);
        cov.finalize(id);

        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.EXPIRED));
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.remaining, BOND);
        assertEq(c.held, 0);
        assertEq(c.paid, 0);
        assertEq(cov.withdrawable(buyer), 0);
        assertEq(cov.withdrawable(proposer), PROPOSER_BOND); // nobody slashed
    }
}

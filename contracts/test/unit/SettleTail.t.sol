// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Fixture} from "../utils/Fixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Attestation, KIND_DETECT} from "../../src/lib/Attestation.sol";

/// @notice The settlement tail: a detection whose scene time falls inside the term but that is published (and so
///         proposed) after the term plus the challenge window. Backs docs/SECURITY.md section 15.
contract SettleTailTest is Fixture {
    bytes32 internal E_LATE;
    uint32 internal constant RATE = 2548; // tier 2
    uint64 internal sceneTime;

    function setUp() public override {
        super.setUp();
        E_LATE = sha256("bundle TKM_S_276 scene inside the term, published after the window");
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        sceneTime = c.end - 2 days; // inside the term
        vm.warp(uint256(c.end) + CHALLENGE_WINDOW); // the provider publishes only now
    }

    function _proposeLate(bytes32 covenantId) internal returns (uint256) {
        covId = covenantId; // _att binds the covenant id held in `covId`
        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(obsAPk, obsBPk), SRC_TKM, E_LATE, RATE, sceneTime, KIND_DETECT);
        bytes32[] memory proof = _proof(sources, SRC_TKM);
        vm.prank(proposer);
        return cov.proposeEpisode{value: PROPOSER_BOND}(covenantId, SRC_TKM, proof, E_LATE, atts, sigs);
    }

    /// @notice After `settle`, a detection dated inside the term but proposed later is outside the bond:
    ///         `proposeEpisode` reverts `WindowClosed` and nothing moves.
    function test_settle_thenLatePublishedDetection_reverts_WindowClosed() public {
        bytes32 id = covId;
        cov.settle(id);
        assertTrue(cov.getCovenant(id).settled);
        uint256 sellerCredit = cov.withdrawable(seller);
        assertEq(sellerCredit, BOND, "whole bond back to the seller at settle");

        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(obsAPk, obsBPk), SRC_TKM, E_LATE, RATE, sceneTime, KIND_DETECT);
        bytes32[] memory proof = _proof(sources, SRC_TKM);
        vm.prank(proposer);
        vm.expectRevert(MethaneCovenant.WindowClosed.selector);
        cov.proposeEpisode{value: PROPOSER_BOND}(id, SRC_TKM, proof, E_LATE, atts, sigs);

        assertEq(cov.episodeCount(), 0, "no episode recorded");
        assertFalse(cov.evidenceSeen(E_LATE), "evidence not consumed");
        assertEq(cov.withdrawable(seller), sellerCredit, "seller credit unchanged");
        assertEq(cov.withdrawable(buyer), 0, "nothing to the buyer");
    }

    /// @notice The same late detection before anyone calls `settle` is still inside the bond: the scene time is in
    ///         the term, so it is proposed and the rebate is held (settle then waits with `WindowOpen`).
    function test_lateDetection_beforeSettle_isHeld() public {
        uint256 id = _proposeLate(covId);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.PROPOSED));
        vm.expectRevert(MethaneCovenant.WindowOpen.selector);
        cov.settle(covId);
    }

    /// @notice The remedy for the tail: a tail covenant whose start is backdated (up to MAX_LOOKBACK) covers a
    ///         detection from the earlier term that was published after that term was settled.
    function test_tailCovenant_backdatedStart_coversLatePublishedDetection() public {
        cov.settle(covId);
        assertLe(block.timestamp - (sceneTime - 1 days), cov.MAX_LOOKBACK());

        MethaneCovenant.CovenantParams memory p = _params();
        p.start = sceneTime - 1 days; // backdated: covers the tail of the settled term
        vm.prank(seller);
        bytes32 tail = cov.createCovenant{value: BOND}(p);
        assertEq(cov.getCovenant(tail).createdAt, block.timestamp, "backdating is visible on chain");

        uint256 id = _proposeLate(tail);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.PROPOSED));
        assertGt(cov.getEpisode(id).rebate, 0, "rebate held from the tail bond");
    }
}

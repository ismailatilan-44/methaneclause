// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {StdInvariant} from "forge-std/StdInvariant.sol";
import {Fixture} from "../utils/Fixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Handler} from "./Handler.sol";

/// @notice Stateful invariants I1-I7 (docs/spec.md §6).
contract MethaneCovenantInvariants is StdInvariant, Fixture {
    Handler internal handler;
    bytes32 internal fuzzCov;
    uint256 internal adminBalance0;

    function setUp() public override {
        super.setUp();

        // A short-term covenant so the fuzzer reaches settlement.
        MethaneCovenant.CovenantParams memory p = _params();
        p.term = 30 days;
        p.linkWindow = 5 days;
        vm.prank(seller);
        fuzzCov = cov.createCovenant{value: 4 ether}(p);
        vm.warp(block.timestamp + 1 days); // detections can exist from the first call

        bytes32[][] memory proofs = new bytes32[][](sources.length);
        for (uint256 i; i < sources.length; ++i) {
            proofs[i] = _proof(sources, sources[i]);
        }
        uint256[] memory pks = new uint256[](6);
        bytes32[] memory orgs = new bytes32[](6);
        bool[] memory party = new bool[](6);
        (pks[0], orgs[0]) = (obsAPk, ORG_A);
        (pks[1], orgs[1]) = (obsA2Pk, ORG_A);
        (pks[2], orgs[2]) = (obsBPk, ORG_B);
        (pks[3], orgs[3]) = (obsCPk, ORG_C);
        (pks[4], orgs[4], party[4]) = (sellerObsPk, ORG_SELLER, true);
        (pks[5], orgs[5], party[5]) = (buyerObsPk, ORG_BUYER, true);

        handler = new Handler(cov, fuzzCov, seller, buyer, admin, sources, proofs, pks, orgs, party);
        adminBalance0 = admin.balance;

        bytes4[] memory selectors = new bytes4[](12);
        selectors[0] = Handler.propose.selector;
        selectors[1] = Handler.propose.selector; // weighted: proposals drive everything else
        selectors[2] = Handler.challenge.selector;
        selectors[3] = Handler.resolve.selector;
        selectors[4] = Handler.contest.selector;
        selectors[5] = Handler.finalize.selector;
        selectors[6] = Handler.warp.selector;
        selectors[7] = Handler.withdraw.selector;
        selectors[8] = Handler.fund.selector;
        selectors[9] = Handler.settle.selector;
        selectors[10] = Handler.mitigationClaim.selector;
        selectors[11] = Handler.adminAttack.selector;
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
        targetContract(address(handler));
    }

    function _isOpen(MethaneCovenant.State s) internal pure returns (bool) {
        return s == MethaneCovenant.State.PROPOSED || s == MethaneCovenant.State.CHALLENGED
            || s == MethaneCovenant.State.CONTESTED;
    }

    /// I1: no episode is live or CONFIRMED with fewer than k distinct independent orgs; a contested episode is only
    ///     CONFIRMED by >= k+1.
    function invariant_I1_quorum() public view {
        uint256 n = handler.episodeCountGhost();
        for (uint256 i; i < n; ++i) {
            uint256 id = handler.episodeIds(i);
            MethaneCovenant.Episode memory e = cov.getEpisode(id);
            assertGe(handler.ghostProposalOrgs(id), K, "I1: accepted with < k distinct orgs");
            assertGe(e.quorumOrgs, K, "I1: stored quorum < k");
            uint256 contestOrgs = handler.ghostContestConfirmOrgs(id);
            if (contestOrgs != 0) assertGe(contestOrgs, K + 1, "I1: contest confirmed with < k+1");
        }
    }

    /// I2: no attestation from a seller- or buyer-org key was ever accepted.
    function invariant_I2_noSelfAttestation() public view {
        assertEq(handler.ghostPartySignaturesAccepted(), 0, "I2: party-org attestation counted");
    }

    /// I3: at most one payout per episode; linked detections never pay. The buyer's total credit equals the sum of
    ///     confirmed rebates plus forfeited challenger bonds, and per covenant paid == sum of CONFIRMED rebates.
    function invariant_I3_onePayoutPerEpisode() public view {
        uint256 paidFromEpisodes;
        uint256 forfeitedChallengerBonds;
        uint256 n = cov.episodeCount();
        for (uint256 id = 1; id <= n; ++id) {
            MethaneCovenant.Episode memory e = cov.getEpisode(id);
            if (e.state == MethaneCovenant.State.LINKED) assertEq(e.rebate, 0, "I3: linked detection carries rebate");
            if (e.state == MethaneCovenant.State.CONFIRMED) {
                paidFromEpisodes += e.rebate;
                if (e.challenger != address(0)) forfeitedChallengerBonds += CHALLENGER_BOND;
            }
        }
        uint256 paid;
        for (uint256 c = 1; c <= cov.covenantCount(); ++c) {
            paid += cov.getCovenant(bytes32(c)).paid;
        }
        assertEq(paid, paidFromEpisodes, "I3: paid != sum of confirmed rebates");
        assertEq(
            cov.withdrawable(buyer) + handler.ghostWithdrawn(buyer),
            paidFromEpisodes + forfeitedChallengerBonds,
            "I3: buyer credited more than once"
        );
    }

    /// I4: per covenant funded == paid + held + remaining + refunded and held == sum of open rebates; the contract
    ///     balance equals bonds held + open proposer/challenger bonds + withdrawable balances.
    function invariant_I4_conservation() public view {
        uint256 bondsHeld;
        uint256 nCov = cov.covenantCount();
        for (uint256 c = 1; c <= nCov; ++c) {
            MethaneCovenant.Covenant memory cv = cov.getCovenant(bytes32(c));
            assertEq(cv.funded, cv.paid + cv.held + cv.remaining + cv.refunded, "I4: covenant accounting");
            bondsHeld += cv.remaining + cv.held;
        }

        uint256 openBonds;
        uint256 n = cov.episodeCount();
        uint256[] memory heldBy = new uint256[](nCov + 1);
        uint256[] memory openBy = new uint256[](nCov + 1);
        for (uint256 id = 1; id <= n; ++id) {
            MethaneCovenant.Episode memory e = cov.getEpisode(id);
            openBonds += e.proposerBond + e.challengerBond;
            if (_isOpen(e.state)) {
                heldBy[uint256(e.covenantId)] += e.rebate;
                openBy[uint256(e.covenantId)] += 1;
            } else {
                assertEq(e.proposerBond + e.challengerBond, 0, "I4: bond left on terminal episode");
            }
        }
        for (uint256 c = 1; c <= nCov; ++c) {
            MethaneCovenant.Covenant memory cv = cov.getCovenant(bytes32(c));
            assertEq(cv.held, heldBy[c], "I4: held != open rebates");
            assertEq(cv.openEpisodes, openBy[c], "I4: openEpisodes drift");
        }

        uint256 owed;
        for (uint256 i; i < handler.actorCount(); ++i) {
            owed += cov.withdrawable(handler.actors(i));
        }
        assertEq(address(cov).balance, bondsHeld + openBonds + owed, "I4: balance != liabilities");
    }

    /// I5: every open episode reaches a terminal state via finalize once its deadline passes.
    function invariant_I5_noStuckState() public {
        uint256 n = cov.episodeCount();
        for (uint256 id = 1; id <= n; ++id) {
            MethaneCovenant.Episode memory e = cov.getEpisode(id);
            if (!_isOpen(e.state)) continue;
            uint256 snap = vm.snapshotState();
            vm.warp(e.deadline);
            cov.finalize(id);
            assertFalse(_isOpen(cov.getEpisode(id).state), "I5: still open after finalize");
            vm.revertToState(snap);
        }
    }

    /// I6: recordMitigationClaim never changed covenant or episode state.
    function invariant_I6_mitigationClaimInert() public view {
        assertEq(handler.ghostMitigationStateChanges(), 0, "I6: mitigation claim changed state");
    }

    /// I7: the deployer/admin has no path to funds or keys.
    function invariant_I7_noAdminPath() public view {
        assertEq(registry.admin(), address(0), "I7: registry admin not renounced");
        assertEq(handler.ghostAdminActionsAccepted(), 0, "I7: admin action accepted");
        assertEq(cov.withdrawable(admin), 0, "I7: admin credited");
        assertEq(admin.balance, adminBalance0, "I7: admin balance moved");
    }
}

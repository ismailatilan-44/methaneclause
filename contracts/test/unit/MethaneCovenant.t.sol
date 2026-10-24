// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Fixture} from "../utils/Fixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Attestation, KIND_DETECT, KIND_CONFIRM, KIND_WITHDRAW} from "../../src/lib/Attestation.sol";

contract MethaneCovenantTest is Fixture {
    // Stand-ins for SHA-256(JCS bundle). Stored (not `constant`): a constant sha256 re-runs the precompile at each use,
    // which would consume vm.prank / vm.expectRevert.
    bytes32 internal E1;
    bytes32 internal E2;
    uint8 internal constant HASH_MISMATCH = 1;
    uint8 internal constant DUPLICATE_PLUME = 3;
    uint8 internal constant STALE = 5;
    uint32 internal constant RATE = 2548; // tier 2

    uint64 internal detected;

    function setUp() public override {
        super.setUp();
        E1 = sha256("bundle TKM_S_276 2026-06-11 2548 kg/h");
        E2 = sha256("bundle TKM_S_276 2026-06-20");
        detected = uint64(block.timestamp + 10 days);
        vm.warp(block.timestamp + 11 days);
    }

    function _proposeArgs(uint256[] memory pks, bytes32 src, bytes32 ev, uint32 kgh, uint64 when)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof)
    {
        (atts, sigs) = _batch(pks, src, ev, kgh, when, KIND_DETECT);
        proof = _proof(sources, src);
    }

    function _expectProposeRevert(
        bytes memory err,
        bytes32 src,
        bytes32 ev,
        Attestation[] memory atts,
        bytes[] memory sigs
    ) internal {
        bytes32[] memory proof = _proof(sources, src);
        vm.prank(proposer);
        vm.expectRevert(err);
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, src, proof, ev, atts, sigs);
    }

    // ------------------------------------------------------------------ happy path

    function test_happyPath_2of3_confirmed_buyerWithdraws() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);

        MethaneCovenant.Episode memory e = cov.getEpisode(id);
        assertEq(uint8(e.state), uint8(MethaneCovenant.State.PROPOSED));
        assertEq(e.tier, 2);
        assertEq(e.rebate, TIER2);
        assertEq(e.quorumOrgs, 2);
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.held, TIER2);
        assertEq(c.remaining, BOND - TIER2);

        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id); // anyone
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));

        c = cov.getCovenant(covId);
        assertEq(c.paid, TIER2);
        assertEq(c.held, 0);
        assertEq(c.funded, c.paid + c.held + c.remaining + c.refunded);

        uint256 before = buyer.balance;
        vm.prank(buyer);
        cov.withdraw();
        assertEq(buyer.balance - before, TIER2);

        uint256 pBefore = proposer.balance;
        vm.prank(proposer);
        cov.withdraw();
        assertEq(proposer.balance - pBefore, PROPOSER_BOND);
        assertEq(address(cov).balance, c.remaining);
    }

    function test_tiers_and_rebateCappedAtRemaining() public {
        uint256 id1 = _propose(SRC_TKM, E1, 5000, detected); // tier1 = 3 ether
        uint256 id2 = _propose(SRC_DZA, E2, 100, detected); // tier3 = 1 ether
        uint256 id3 = _propose(SRC_USA, keccak256("e3"), 13_290, detected); // tier1, only 1 ether left
        assertEq(cov.getEpisode(id1).rebate, TIER1);
        assertEq(cov.getEpisode(id2).tier, 3);
        assertEq(cov.getEpisode(id2).rebate, TIER3);
        assertEq(cov.getEpisode(id3).rebate, BOND - TIER1 - TIER3);
        assertEq(cov.getCovenant(covId).remaining, 0);
        uint256 id4 = _propose(SRC_LBY, keccak256("e4"), 9000, detected);
        assertEq(cov.getEpisode(id4).rebate, 0); // recorded as evidence even when the bond is exhausted
    }

    function test_digestMatchesContract() public {
        Attestation memory a = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(obsAPk, cov.attestationDigest(a));
        assertEq(abi.encodePacked(r, s, v), _sign(obsAPk, a));
    }

    // ------------------------------------------------------------------ tamper

    function test_tamperedEvidenceHash_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        bytes32 tampered = keccak256("bundle TKM_S_276 2026-06-11 548 kg/h");
        atts[0].evidenceHash = tampered;
        atts[1].evidenceHash = tampered;
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, tampered, atts, sigs
        );
        // Only the submitted evidenceHash changed: attestations no longer match it.
        (atts, sigs,) = _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.AttestationMismatch.selector, 0), SRC_TKM, tampered, atts, sigs
        );
        assertEq(cov.getCovenant(covId).remaining, BOND); // bond did not move
    }

    function test_tamperedEmission_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        atts[0].emissionKgH = 548;
        atts[1].emissionKgH = 548;
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, E1, atts, sigs);
    }

    function test_observerDisagreement_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        atts[1].emissionKgH = RATE + 1;
        sigs[1] = _sign(obsBPk, atts[1]);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.AttestationMismatch.selector, 1), SRC_TKM, E1, atts, sigs
        );
    }

    // ------------------------------------------------------------------ independence

    function test_selfAttestation_sellerOrgObserver_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, sellerObsPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.SelfAttestation.selector, sellerObs), SRC_TKM, E1, atts, sigs
        );
    }

    function test_selfAttestation_sellerKey_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(sellerPk, obsAPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.SelfAttestation.selector, seller), SRC_TKM, E1, atts, sigs
        );
    }

    function test_selfAttestation_buyerOrg_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, buyerObsPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.SelfAttestation.selector, buyerObs), SRC_TKM, E1, atts, sigs
        );
    }

    function test_attestorNotIndependent_nonObserverRole_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, otherSellerPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.AttestorNotIndependent.selector, otherSeller),
            SRC_TKM,
            E1,
            atts,
            sigs
        );
    }

    function test_quorumNotMet_twoKeysSameOrg_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsA2Pk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.QuorumNotMet.selector, 1, K), SRC_TKM, E1, atts, sigs
        );
    }

    function test_quorumNotMet_singleAttestation_reverts() public {
        uint256[] memory one = new uint256[](1);
        one[0] = obsAPk;
        (Attestation[] memory atts, bytes[] memory sigs,) = _proposeArgs(one, SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.QuorumNotMet.selector, 0, K), SRC_TKM, E1, atts, sigs
        );
    }

    function test_threeOfThree_countsDistinctOrgs() public {
        (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof) =
            _proposeArgs(_pks(obsAPk, obsA2Pk, obsBPk), SRC_TKM, E1, RATE, detected);
        vm.prank(proposer);
        uint256 id = cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, E1, atts, sigs);
        assertEq(cov.getEpisode(id).quorumOrgs, 2);
    }

    // ------------------------------------------------------------------ replay / expiry / keys

    function test_replay_reusedNonce_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        vm.prank(proposer);
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, E1, atts, sigs);
        uint64 usedNonce = atts[0].nonce;

        // Same observer key signs a different detection with the nonce it already spent.
        (atts, sigs, proof) = _proposeArgs(_pks(obsAPk, obsCPk), SRC_DZA, E2, RATE, detected);
        atts[0].nonce = usedNonce;
        sigs[0] = _sign(obsAPk, atts[0]);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.Replay.selector, obsA, usedNonce), SRC_DZA, E2, atts, sigs
        );
    }

    function test_replay_sameAttestationTwiceInBatch_reverts() public {
        Attestation[] memory atts = new Attestation[](3);
        bytes[] memory sigs = new bytes[](3);
        atts[0] = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        sigs[0] = _sign(obsAPk, atts[0]);
        atts[1] = atts[0];
        sigs[1] = sigs[0];
        atts[2] = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        sigs[2] = _sign(obsBPk, atts[2]);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.Replay.selector, obsA, atts[0].nonce), SRC_TKM, E1, atts, sigs
        );
    }

    function test_duplicateEvidence_isIdempotent() public {
        _propose(SRC_TKM, E1, RATE, detected);
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.DuplicateEvidence.selector, E1), SRC_TKM, E1, atts, sigs
        );
        assertEq(cov.episodeCount(), 1);
    }

    function test_crossChainReplay_reverts() public {
        vm.chainId(84532); // Base Sepolia
        // Observers signed for Ethereum Sepolia (11155111): rejected on Base Sepolia.
        Attestation[] memory atts = new Attestation[](2);
        bytes[] memory sigs = new bytes[](2);
        atts[0] = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        atts[1] = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        sigs[0] = _signOn(obsAPk, atts[0], 11155111, address(cov));
        sigs[1] = _signOn(obsBPk, atts[1], 11155111, address(cov));
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, E1, atts, sigs);

        // Control: the same attestations signed for 84532 are accepted.
        sigs[0] = _signOn(obsAPk, atts[0], 84532, address(cov));
        sigs[1] = _signOn(obsBPk, atts[1], 84532, address(cov));
        bytes32[] memory proof = _proof(sources, SRC_TKM);
        vm.prank(proposer);
        uint256 id = cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, E1, atts, sigs);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.PROPOSED));
    }

    function test_crossContractReplay_reverts() public {
        Attestation[] memory atts = new Attestation[](2);
        bytes[] memory sigs = new bytes[](2);
        atts[0] = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        atts[1] = _att(SRC_TKM, E1, RATE, detected, KIND_DETECT);
        sigs[0] = _signOn(obsAPk, atts[0], block.chainid, address(0xBEEF));
        sigs[1] = _signOn(obsBPk, atts[1], block.chainid, address(0xBEEF));
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, E1, atts, sigs);
    }

    function test_expired_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        vm.warp(block.timestamp + 1 hours + 1);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.Expired.selector, atts[0].expiry), SRC_TKM, E1, atts, sigs
        );
    }

    function test_expiryTooFar_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        uint64 far = uint64(block.timestamp + 1 days + 1);
        atts[0].expiry = far;
        sigs[0] = _sign(obsAPk, atts[0]);
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.Expired.selector, far), SRC_TKM, E1, atts, sigs);
    }

    function test_revokedKey_reverts() public {
        vm.prank(obsB); // a key may revoke itself (compromise report)
        registry.revokeKey(obsB);
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.RevokedKey.selector, obsB), SRC_TKM, E1, atts, sigs);
    }

    function test_rotatedOldKey_reverts_newKeyAccepted() public {
        (address obsDNew, uint256 obsDNewPk) = makeAddrAndKey("obsD-new");
        vm.prank(admin); // org D controller (admin renounced the registry admin role, kept org D control)
        registry.rotateKey(obsD, obsDNew, 1, 0);

        (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof) =
            _proposeArgs(_pks(obsAPk, obsDPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.RevokedKey.selector, obsD), SRC_TKM, E1, atts, sigs);

        (atts, sigs, proof) = _proposeArgs(_pks(obsAPk, obsDNewPk), SRC_TKM, E1, RATE, detected);
        vm.prank(proposer);
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, E1, atts, sigs);
    }

    function test_unregisteredSigner_reverts() public {
        (, uint256 strangerPk) = makeAddrAndKey("stranger");
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, strangerPk), SRC_TKM, E1, RATE, detected);
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, E1, atts, sigs);
    }

    function test_malformedAndHighSSignature_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(obsAPk, cov.attestationDigest(atts[0]));
        uint256 n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
        sigs[0] = abi.encodePacked(r, bytes32(n - uint256(s)), v == 27 ? uint8(28) : uint8(27)); // malleated twin
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, E1, atts, sigs);
        sigs[0] = abi.encodePacked(r, s); // 64 bytes
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.InvalidSignature.selector), SRC_TKM, E1, atts, sigs);
    }

    // ------------------------------------------------------------------ coverage / term / bond

    function test_notCovered_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_UNCOVERED, E1, RATE, detected);
        bytes32[] memory proof = _proof(sources, SRC_TKM); // a valid proof for another source
        vm.prank(proposer);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.NotCovered.selector, SRC_UNCOVERED));
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_UNCOVERED, proof, E1, atts, sigs);
    }

    function test_detectionOutOfTerm_reverts() public {
        uint64 before = uint64(T0 - 1);
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, before);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.DetectionOutOfTerm.selector, before), SRC_TKM, E1, atts, sigs
        );
        uint64 future = uint64(block.timestamp + 1);
        (atts, sigs,) = _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, future);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.DetectionOutOfTerm.selector, future), SRC_TKM, E1, atts, sigs
        );
    }

    function test_belowMinRate_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs,) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, 99, detected);
        _expectProposeRevert(abi.encodeWithSelector(MethaneCovenant.BelowMinRate.selector, 99), SRC_TKM, E1, atts, sigs);
    }

    function test_badProposerBond_reverts() public {
        (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        vm.prank(proposer);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.BadBond.selector, 0, PROPOSER_BOND));
        cov.proposeEpisode(covId, SRC_TKM, proof, E1, atts, sigs);
    }

    // ------------------------------------------------------------------ mitigation claim (I6)

    function test_recordMitigationClaim_hasNoStateEffect() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        bytes memory covBefore = abi.encode(cov.getCovenant(covId));
        bytes memory epBefore = abi.encode(cov.getEpisode(id));

        vm.record();
        vm.prank(seller);
        cov.recordMitigationClaim(covId, SRC_TKM, keccak256("LDAR repair report"));
        (, bytes32[] memory writes) = vm.accesses(address(cov));
        assertEq(writes.length, 0, "mitigation claim wrote storage");

        assertEq(abi.encode(cov.getCovenant(covId)), covBefore);
        assertEq(abi.encode(cov.getEpisode(id)), epBefore);

        // The rebate still flows after the claim.
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
        assertEq(cov.withdrawable(buyer), TIER2);
    }

    function test_recordMitigationClaim_onlySeller() public {
        vm.prank(buyer);
        vm.expectRevert(MethaneCovenant.NotAuthorized.selector);
        cov.recordMitigationClaim(covId, SRC_TKM, keccak256("x"));
    }

    // ------------------------------------------------------------------ linking

    function test_linkedDetection_paysNothing() public {
        uint256 parent = _propose(SRC_TKM, E1, RATE, detected);
        vm.warp(block.timestamp + 10 days);
        uint256 remainingBefore = cov.getCovenant(covId).remaining;

        uint256 linked = _propose(SRC_TKM, E2, 9000, detected + 9 days); // inside 30-day linkWindow
        MethaneCovenant.Episode memory e = cov.getEpisode(linked);
        assertEq(uint8(e.state), uint8(MethaneCovenant.State.LINKED));
        assertEq(e.parentEpisode, parent);
        assertEq(e.rebate, 0);
        assertEq(cov.getCovenant(covId).remaining, remainingBefore);
        assertEq(cov.latestEpisode(covId, SRC_TKM), parent);
        assertEq(cov.withdrawable(proposer), PROPOSER_BOND); // linked bond returned at once

        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.EpisodeLinked.selector, linked));
        cov.challenge{value: CHALLENGER_BOND}(linked, 1);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.NothingToFinalize.selector, linked));
        cov.finalize(linked);

        cov.finalize(parent);
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.paid, TIER2); // one payout only
        assertEq(cov.withdrawable(buyer), TIER2);
    }

    function test_detectionAfterLinkWindow_opensNewEpisodeWithParent() public {
        uint256 first = _propose(SRC_TKM, E1, RATE, detected);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(first);

        vm.warp(block.timestamp + 40 days);
        uint256 second = _propose(SRC_TKM, E2, 694, detected + LINK_WINDOW + 1);
        MethaneCovenant.Episode memory e = cov.getEpisode(second);
        assertEq(uint8(e.state), uint8(MethaneCovenant.State.PROPOSED));
        assertEq(e.parentEpisode, first);
        assertEq(e.rebate, TIER3);
        assertEq(uint8(_state(first)), uint8(MethaneCovenant.State.CONFIRMED)); // past acceptance stands
        assertEq(cov.latestEpisode(covId, SRC_TKM), second);
    }

    // ------------------------------------------------------------------ challenge

    function _resolution(uint256 id, uint256[] memory pks, uint8 kind)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs)
    {
        MethaneCovenant.Episode memory e = cov.getEpisode(id);
        (atts, sigs) = _batch(pks, e.sourceId, e.evidenceHash, e.emissionKgH, e.detectedAt, kind);
    }

    function test_challengeHashMismatch_withdraw_void_challengerGetsProposerBond() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, HASH_MISMATCH);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CHALLENGED));

        (Attestation[] memory atts, bytes[] memory sigs) = _resolution(id, _pks(obsBPk, obsCPk), KIND_WITHDRAW);
        cov.submitResolution(id, atts, sigs);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.VOID));

        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.remaining, BOND);
        assertEq(c.held, 0);
        assertEq(c.paid, 0);
        assertEq(cov.withdrawable(challenger), PROPOSER_BOND + CHALLENGER_BOND);
        assertEq(cov.withdrawable(proposer), 0);
        assertEq(cov.withdrawable(buyer), 0);

        uint256 before = challenger.balance;
        vm.prank(challenger);
        cov.withdraw();
        assertEq(challenger.balance - before, PROPOSER_BOND + CHALLENGER_BOND);
    }

    function test_challenge_confirm_challengerBondToBuyer() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, STALE);
        (Attestation[] memory atts, bytes[] memory sigs) = _resolution(id, _pks(obsAPk, obsCPk), KIND_CONFIRM);
        cov.submitResolution(id, atts, sigs);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));
        assertEq(cov.withdrawable(buyer), TIER2 + CHALLENGER_BOND);
        assertEq(cov.withdrawable(proposer), PROPOSER_BOND);
        assertEq(cov.withdrawable(challenger), 0);
    }

    function test_challenge_unresolved_expires_bondsReturned() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, DUPLICATE_PLUME);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.EXPIRED));
        assertEq(cov.withdrawable(challenger), CHALLENGER_BOND);
        assertEq(cov.withdrawable(proposer), PROPOSER_BOND);
        assertEq(cov.getCovenant(covId).remaining, BOND);
    }

    function test_challenge_guards() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.startPrank(challenger);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.InvalidReason.selector, 0));
        cov.challenge{value: CHALLENGER_BOND}(id, 0);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.InvalidReason.selector, 6));
        cov.challenge{value: CHALLENGER_BOND}(id, 6);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.BadBond.selector, 1, CHALLENGER_BOND));
        cov.challenge{value: 1}(id, 1);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        vm.expectRevert(MethaneCovenant.WindowClosed.selector);
        cov.challenge{value: CHALLENGER_BOND}(id, 1);
        vm.stopPrank();
    }

    function test_resolution_selfAttestation_and_quorum_enforced() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, 1);
        (Attestation[] memory atts, bytes[] memory sigs) = _resolution(id, _pks(obsAPk, sellerObsPk), KIND_WITHDRAW);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.SelfAttestation.selector, sellerObs));
        cov.submitResolution(id, atts, sigs);
        (atts, sigs) = _resolution(id, _pks(obsAPk, obsA2Pk), KIND_WITHDRAW);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.QuorumNotMet.selector, 1, K));
        cov.submitResolution(id, atts, sigs);
        // DETECT attestations cannot resolve a challenge.
        (atts, sigs) = _resolution(id, _pks(obsAPk, obsBPk), KIND_DETECT);
        vm.expectRevert(MethaneCovenant.InvalidParams.selector);
        cov.submitResolution(id, atts, sigs);
    }

    // ------------------------------------------------------------------ contest (hold, never slash)

    function test_contest_expires_noSlash() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(buyer);
        vm.expectRevert(MethaneCovenant.NotAuthorized.selector);
        cov.contest(id);

        vm.prank(seller);
        cov.contest(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONTESTED));

        vm.warp(block.timestamp + CHALLENGE_WINDOW); // original window passed, contest still holds
        vm.expectRevert(MethaneCovenant.WindowOpen.selector);
        cov.finalize(id);

        vm.warp(block.timestamp + CONTEST_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.EXPIRED));
        assertEq(cov.getCovenant(covId).remaining, BOND);
        assertEq(cov.withdrawable(proposer), PROPOSER_BOND); // nobody slashed
    }

    function test_contest_confirmedByKPlusOne() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.prank(seller);
        cov.contest(id);
        (Attestation[] memory atts, bytes[] memory sigs) = _resolution(id, _pks(obsAPk, obsBPk), KIND_CONFIRM);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.QuorumNotMet.selector, 0, K + 1));
        cov.submitResolution(id, atts, sigs);
        (atts, sigs) = _resolution(id, _pks(obsAPk, obsBPk, obsCPk), KIND_CONFIRM);
        cov.submitResolution(id, atts, sigs);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));
        assertEq(cov.withdrawable(buyer), TIER2);
    }

    // ------------------------------------------------------------------ finalize / settle / fund

    function test_finalize_guards() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.expectRevert(MethaneCovenant.WindowOpen.selector);
        cov.finalize(id);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.NothingToFinalize.selector, id));
        cov.finalize(id);
        vm.expectRevert(abi.encodeWithSelector(MethaneCovenant.NothingToFinalize.selector, 999));
        cov.finalize(999);
    }

    function test_settle_afterTerm_refundsSeller() public {
        uint256 id = _propose(SRC_TKM, E1, RATE, detected);
        vm.expectRevert(MethaneCovenant.WindowOpen.selector);
        cov.settle(covId);

        vm.warp(T0 + TERM + CHALLENGE_WINDOW);
        vm.expectRevert(MethaneCovenant.WindowOpen.selector); // episode still open
        cov.settle(covId);
        cov.finalize(id);
        cov.settle(covId);

        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertTrue(c.settled);
        assertEq(c.refunded, BOND - TIER2);
        assertEq(c.funded, c.paid + c.held + c.remaining + c.refunded);
        assertEq(cov.withdrawable(seller), BOND - TIER2);

        vm.expectRevert(MethaneCovenant.WindowClosed.selector);
        cov.settle(covId);
        (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_DZA, E2, RATE, detected);
        vm.prank(proposer);
        vm.expectRevert(MethaneCovenant.WindowClosed.selector);
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_DZA, proof, E2, atts, sigs);
    }

    function test_fund_topUp_and_afterTerm() public {
        cov.fund{value: 1 ether}(covId);
        assertEq(cov.getCovenant(covId).funded, BOND + 1 ether);
        vm.warp(T0 + TERM);
        vm.expectRevert(MethaneCovenant.WindowClosed.selector);
        cov.fund{value: 1 ether}(covId);
    }

    function test_createCovenant_requiresRegisteredParties() public {
        MethaneCovenant.CovenantParams memory p = _params();
        vm.prank(buyer);
        vm.expectRevert(MethaneCovenant.NotAuthorized.selector);
        cov.createCovenant(p);

        p.buyer = obsA;
        vm.prank(seller);
        vm.expectRevert(MethaneCovenant.InvalidParams.selector);
        cov.createCovenant(p);

        p = _params();
        p.k = 0;
        vm.prank(seller);
        vm.expectRevert(MethaneCovenant.InvalidParams.selector);
        cov.createCovenant(p);
    }

    // ------------------------------------------------------------------ backdated start (MAX_LOOKBACK)

    function _createBackdated(uint64 start) internal returns (bytes32 id) {
        MethaneCovenant.CovenantParams memory p = _params();
        p.start = start;
        vm.prank(seller);
        id = cov.createCovenant{value: BOND}(p);
    }

    function test_createCovenant_startZero_isNow() public view {
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.start, c.createdAt);
        assertEq(c.end, c.start + TERM);
    }

    function test_backdatedCovenant_coversDetectionPublishedBeforeCreation() public {
        uint64 start = uint64(block.timestamp - 100 days);
        covId = _createBackdated(start);
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        assertEq(c.start, start);
        assertEq(c.createdAt, block.timestamp); // on-chain record that the start was backdated
        assertEq(c.end, start + TERM);

        uint256 id = _propose(SRC_TKM, E1, RATE, uint64(block.timestamp - 50 days)); // start < detectedAt < createdAt
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.PROPOSED));
    }

    function test_backdatedCovenant_rejectsDetectionBeforeStart() public {
        covId = _createBackdated(uint64(block.timestamp - 100 days));
        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, uint64(block.timestamp - 101 days), KIND_DETECT);
        _expectProposeRevert(
            abi.encodeWithSelector(MethaneCovenant.DetectionOutOfTerm.selector, uint64(block.timestamp - 101 days)),
            SRC_TKM,
            E1,
            atts,
            sigs
        );
    }

    function test_createCovenant_badStart_reverts() public {
        MethaneCovenant.CovenantParams memory p = _params();
        uint256 lookback = cov.MAX_LOOKBACK();

        p.start = uint64(block.timestamp + 1); // future
        vm.prank(seller);
        vm.expectRevert(MethaneCovenant.InvalidParams.selector);
        cov.createCovenant(p);

        p.start = uint64(block.timestamp - lookback - 1); // older than MAX_LOOKBACK
        vm.prank(seller);
        vm.expectRevert(MethaneCovenant.InvalidParams.selector);
        cov.createCovenant(p);

        p.start = uint64(block.timestamp - 30 days);
        p.term = 30 days; // term already over at creation
        vm.prank(seller);
        vm.expectRevert(MethaneCovenant.InvalidParams.selector);
        cov.createCovenant(p);

        p.start = uint64(block.timestamp - lookback); // exactly at the bound, term still running: accepted
        p.term = uint64(lookback) + 30 days;
        vm.prank(seller);
        cov.createCovenant(p);
    }

    function test_withdraw_nothing_reverts() public {
        vm.expectRevert(MethaneCovenant.NothingToWithdraw.selector);
        cov.withdraw();
    }

    function test_eip712Domain() public view {
        (, string memory name, string memory version, uint256 chainId, address verifying,,) = cov.eip712Domain();
        assertEq(name, "MethaneClause");
        assertEq(version, "1");
        assertEq(chainId, block.chainid);
        assertEq(verifying, address(cov));
    }

    // ------------------------------------------------------------------ gas

    function test_gas_proposeEpisode_2of3() public {
        (Attestation[] memory atts, bytes[] memory sigs, bytes32[] memory proof) =
            _proposeArgs(_pks(obsAPk, obsBPk), SRC_TKM, E1, RATE, detected);
        vm.prank(proposer);
        uint256 g = gasleft();
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, E1, atts, sigs);
        emit log_named_uint("gas proposeEpisode (2 attestations, 4-source set)", g - gasleft());
    }
}

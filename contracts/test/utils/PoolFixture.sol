// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {stdStorage, StdStorage} from "forge-std/Test.sol";
import {Fixture} from "./Fixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {Attestation, AttestationLib, KIND_DETECT} from "../../src/lib/Attestation.sol";
import {CleanPass, Corroboration, CleanPassLib, KIND_CLEAN, KIND_CORROBORATE} from "../../src/lib/CleanPass.sol";

/// @notice Fixture + one IncentivePool over the fixture covenant, a valid PoolParams set (spec-v2 §4 values on the
///         fixture covenant's windows; amounts are test values [SIM]) and helpers that drive real core episodes to
///         CONFIRMED / EXPIRED / LINKED through the core's own functions.
abstract contract PoolFixture is Fixture {
    using stdStorage for StdStorage;

    uint32 internal constant PUBLICATION_LAG = 60 days;
    uint32 internal constant CLEAN_WINDOW = 1 days;
    uint32 internal constant RESOLVE_WINDOW = 7 days;
    // holdWindow >= publicationLag + challengeWindow + contestWindow (63 d here); §4 adds cleanWindow -> 64 d
    uint32 internal constant HOLD_WINDOW = 64 days;
    uint32 internal constant CURE_TERM = 540 days;
    uint96 internal constant PASS_BOND = 0.001 ether;
    uint96 internal constant POOL_CHALLENGER_BOND = 0.002 ether;
    uint96 internal constant OBSERVER_FEE = 0.0001 ether;
    bytes32 internal constant POLICY_V2_HASH = keccak256("policy-v2 [SIM test value]");
    bytes32 internal constant PROVIDER_MARS = "unep-mars"; // as pipeline/tests/vectors/cleanpass_v1.json

    IncentivePool internal pool;
    address internal sponsor = makeAddr("sponsor");
    address internal sponsor2 = makeAddr("sponsor2");
    address internal stranger = makeAddr("stranger");
    address internal relayer = makeAddr("relayer");
    address internal poolChallenger = makeAddr("poolChallenger");
    // Org controllers receive the observer fee. The fixture registry sets only ORG_D's (admin) before renouncing, so
    // A, B and C get theirs written into the registry's `orgController` mapping here (test setup, not a code path).
    address internal ctrlA = makeAddr("ctrlA");
    address internal ctrlB = makeAddr("ctrlB");
    address internal ctrlC = makeAddr("ctrlC");
    uint256 private _evidenceNonce;
    uint256 private _sceneNonce;

    function setUp() public virtual override {
        super.setUp();
        pool = new IncentivePool(cov);
        vm.deal(buyer, 100 ether);
        vm.deal(sponsor, 100 ether);
        vm.deal(sponsor2, 100 ether);
        vm.deal(stranger, 100 ether);
        vm.deal(relayer, 100 ether);
        vm.deal(poolChallenger, 100 ether);
        _setController(ORG_A, ctrlA);
        _setController(ORG_B, ctrlB);
        _setController(ORG_C, ctrlC);
    }

    function _setController(bytes32 org, address ctrl) internal {
        stdstore.target(address(registry)).sig("orgController(bytes32)").with_key(org).checked_write(ctrl);
    }

    // ------------------------------------------------------------------ params

    /// @dev u(N) = 1 - 0.025^(1/N) in basis points (rounded down), N = 1..32 (spec-v2 §2). `pipeline/cleanruns.py` is
    ///      the source for deployments; this copy only has to satisfy the on-chain shape rule (non-increasing).
    function _upperBounds() internal pure returns (uint16[32] memory u) {
        u = [
            uint16(9750),
            8418,
            7075,
            6023,
            5218,
            4592,
            4096,
            3694,
            3362,
            3084,
            2849,
            2646,
            2470,
            2316,
            2180,
            2059,
            1950,
            1853,
            1764,
            1684,
            1610,
            1543,
            1481,
            1424,
            1371,
            1322,
            1277,
            1234,
            1194,
            1157,
            1121,
            1088
        ];
    }

    function _validParams() internal pure returns (IncentivePool.PoolParams memory p) {
        p.publicationLag = PUBLICATION_LAG;
        p.cleanWindow = CLEAN_WINDOW;
        p.resolveWindow = RESOLVE_WINDOW;
        p.holdWindow = HOLD_WINDOW;
        p.cureTerm = CURE_TERM;
        p.passBond = PASS_BOND;
        p.challengerBond = POOL_CHALLENGER_BOND;
        p.observerFeeWei = OBSERVER_FEE;
        p.unitTierAmounts = [uint128(500), 300, 100]; // policy units x100 (spec-v2 §15, 2026-10-16)
        p.installments[0] = IncentivePool.InstallmentRule(3, 3000, false, false);
        p.installments[1] = IncentivePool.InstallmentRule(6, 3000, false, false);
        p.installments[2] = IncentivePool.InstallmentRule(10, 4000, true, true);
        p.upperBoundBp = _upperBounds();
    }

    // ------------------------------------------------------------------ pool helpers

    function _open(IncentivePool.Mode mode, uint256 value) internal {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        pool.fund{value: value}(covId, POLICY_V2_HASH, mode, p);
    }

    function _openOnchain(uint256 value) internal {
        _open(IncentivePool.Mode.ONCHAIN, value);
    }

    function _closingAt() internal view returns (uint256) {
        return uint256(cov.getCovenant(covId).end) + PUBLICATION_LAG + CLEAN_WINDOW;
    }

    function _poolState() internal view returns (IncentivePool.PoolState) {
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        return pl.state;
    }

    function _claimIdOf(uint256 episodeId) internal view returns (bytes32) {
        return keccak256(abi.encode(address(pool), episodeId));
    }

    // ------------------------------------------------------------------ core episode helpers

    function _nextEvidence() internal returns (bytes32) {
        return sha256(abi.encode("pool-test-evidence", ++_evidenceNonce));
    }

    /// @notice Proposes (orgs A + B) and finalizes a detection at `src` on the fixture covenant: CONFIRMED.
    function _confirmed(bytes32 src, uint32 kgh, uint64 detectedAt) internal returns (uint256 id) {
        id = _propose(src, _nextEvidence(), kgh, detectedAt);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
    }

    /// @notice Proposes (orgs A + B) on an arbitrary covenant of `cov` (the fixture's `_propose` is bound to covId).
    function _proposeOn(bytes32 cId, bytes32 src, uint32 kgh, uint64 detectedAt) internal returns (uint256) {
        bytes32 evidence = _nextEvidence();
        uint256[] memory pks = _pks(obsAPk, obsBPk);
        Attestation[] memory atts = new Attestation[](2);
        bytes[] memory sigs = new bytes[](2);
        for (uint256 i; i < 2; ++i) {
            atts[i] = _att(src, evidence, kgh, detectedAt, KIND_DETECT);
            atts[i].covenantId = cId;
            sigs[i] = _sign(pks[i], atts[i]);
        }
        bytes32[] memory proof = _proof(sources, src);
        vm.prank(proposer);
        return cov.proposeEpisode{value: PROPOSER_BOND}(cId, src, proof, evidence, atts, sigs);
    }

    /// @notice A second covenant between the same parties with zero bond: every rebate is clipped to 0 by the core.
    function _zeroBondCovenant() internal returns (bytes32 cId) {
        MethaneCovenant.CovenantParams memory p = _params();
        vm.prank(seller);
        cId = cov.createCovenant(p);
    }

    // ------------------------------------------------------------------ clean passes

    function _pks(uint256 a) internal pure returns (uint256[] memory p) {
        p = new uint256[](1);
        p[0] = a;
    }

    /// @notice A qualified-looking clean pass for `claimId` at `sourceId`: scene acquired at `sceneAt`, published five
    ///         days later; Tanager-like floor 100 kg/h; Carbon Mapper's `not_cloudy` true. Fresh scene id per call.
    function _cp(bytes32 claimId, bytes32 sourceId, uint64 sceneAt) internal returns (CleanPass memory a) {
        uint256 n = ++_sceneNonce;
        a.claimId = claimId;
        a.sourceId = sourceId;
        a.sceneId = keccak256(abi.encode("scene", n));
        a.sceneHash = keccak256(abi.encode("bundle", n));
        a.sceneAt = sceneAt;
        a.publishedAt = sceneAt + 5 days;
        a.instrumentId = 1; // "tan" (pipeline/observer/bundle.py INSTRUMENTS)
        a.providerId = PROVIDER_CM;
        a.cloudBp = 500;
        a.notCloudy = true;
        a.floorKgH = 100;
        a.rateFloorKgH = 100;
        a.kind = KIND_CLEAN;
        a.expiry = uint64(block.timestamp + 1 hours);
    }

    function _signCp(uint256 pk, CleanPass memory a) internal view returns (bytes memory) {
        return _signCpOn(pk, a, block.chainid, address(pool));
    }

    function _signCpOn(uint256 pk, CleanPass memory a, uint256 chainId, address verifying)
        internal
        pure
        returns (bytes memory)
    {
        bytes32 digest = AttestationLib.toTypedDataHash(
            AttestationLib.domainSeparator(chainId, verifying), CleanPassLib.hash(a)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    /// @notice One copy of `t` per key, each with a fresh nonce (and `kind`), signed by that key for `pool`.
    function _cpBatch(uint256[] memory pks, CleanPass memory t, uint8 kind)
        internal
        returns (CleanPass[] memory, bytes[] memory)
    {
        return _cpBatchFor(pool, pks, t, kind);
    }

    /// @notice As `_cpBatch`, signed under the EIP-712 domain of `target`.
    function _cpBatchFor(IncentivePool target, uint256[] memory pks, CleanPass memory t, uint8 kind)
        internal
        returns (CleanPass[] memory atts, bytes[] memory sigs)
    {
        atts = new CleanPass[](pks.length);
        sigs = new bytes[](pks.length);
        for (uint256 i; i < pks.length; ++i) {
            atts[i] = _copy(t); // memory structs assign by reference: each attestation needs its own copy
            atts[i].kind = kind;
            atts[i].nonce = nextNonce++;
            atts[i].expiry = uint64(block.timestamp + 1 hours);
            sigs[i] = _signCpOn(pks[i], atts[i], block.chainid, address(target));
        }
    }

    function _copy(CleanPass memory t) internal pure returns (CleanPass memory a) {
        a = CleanPass({
            claimId: t.claimId,
            sourceId: t.sourceId,
            sceneId: t.sceneId,
            sceneHash: t.sceneHash,
            sceneAt: t.sceneAt,
            publishedAt: t.publishedAt,
            instrumentId: t.instrumentId,
            providerId: t.providerId,
            cloudBp: t.cloudBp,
            notCloudy: t.notCloudy,
            floorKgH: t.floorKgH,
            rateFloorKgH: t.rateFloorKgH,
            kind: t.kind,
            nonce: t.nonce,
            expiry: t.expiry
        });
    }

    /// @notice The relayer attests `t` signed by orgs A + B (k = 2) with the pass bond.
    function _attestPass(CleanPass memory t) internal returns (uint256) {
        return _attestPassOn(pool, t, _pks(obsAPk, obsBPk));
    }

    function _attestPassOn(IncentivePool target, CleanPass memory t, uint256[] memory pks) internal returns (uint256) {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatchFor(target, pks, t, KIND_CLEAN);
        vm.prank(relayer);
        return target.attestCleanPass{value: PASS_BOND}(t.claimId, atts, sigs);
    }

    // ------------------------------------------------------------------ MARS corroborations

    /// @notice k orgs say the pinned MARS snapshot shows no plume at the source in [from, to].
    function _corr(bytes32 claimId, bytes32 sourceId, uint64 from, uint64 to)
        internal
        view
        returns (Corroboration memory a)
    {
        a.claimId = claimId;
        a.sourceId = sourceId;
        a.providerId = PROVIDER_MARS;
        a.marsSnapshotHash = keccak256("mars snapshot [SIM test value]");
        a.windowFrom = from;
        a.windowTo = to;
        a.marsPlumesInWindow = 0;
        a.kind = KIND_CORROBORATE;
        a.expiry = uint64(block.timestamp + 1 hours);
    }

    function _signCorrOn(uint256 pk, Corroboration memory a, address verifying) internal view returns (bytes memory) {
        bytes32 digest = AttestationLib.toTypedDataHash(
            AttestationLib.domainSeparator(block.chainid, verifying), CleanPassLib.hash(a)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    function _copyCorr(Corroboration memory t) internal pure returns (Corroboration memory) {
        return Corroboration({
            claimId: t.claimId,
            sourceId: t.sourceId,
            providerId: t.providerId,
            marsSnapshotHash: t.marsSnapshotHash,
            windowFrom: t.windowFrom,
            windowTo: t.windowTo,
            marsPlumesInWindow: t.marsPlumesInWindow,
            kind: t.kind,
            nonce: t.nonce,
            expiry: t.expiry
        });
    }

    /// @notice One copy of `t` per key, fresh nonces, signed for `target`.
    function _corrBatchFor(IncentivePool target, uint256[] memory pks, Corroboration memory t)
        internal
        returns (Corroboration[] memory atts, bytes[] memory sigs)
    {
        atts = new Corroboration[](pks.length);
        sigs = new bytes[](pks.length);
        for (uint256 i; i < pks.length; ++i) {
            atts[i] = _copyCorr(t);
            atts[i].nonce = nextNonce++;
            sigs[i] = _signCorrOn(pks[i], atts[i], address(target));
        }
    }

    /// @notice Orgs A + B attest `t`: a PENDING corroboration (spec-v2 §15 A3 audit (3)).
    function _corroborate(IncentivePool target, Corroboration memory t) internal {
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(target, _pks(obsAPk, obsBPk), t);
        target.attestCorroboration(t.claimId, atts, sigs);
    }

    /// @notice Attest, wait the unchallenged clean window, finalize: an effective corroboration.
    function _corroborateEffective(IncentivePool target, Corroboration memory t) internal {
        _corroborate(target, t);
        vm.warp(block.timestamp + CLEAN_WINDOW);
        target.finalizeCorroboration(t.claimId);
    }
}

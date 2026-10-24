// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {Attestation, AttestationLib, KIND_DETECT, KIND_CONFIRM, KIND_WITHDRAW} from "../../src/lib/Attestation.sol";
import {
    CleanPass,
    Corroboration,
    CleanPassLib,
    KIND_CLEAN,
    KIND_CONFIRM_CLEAN,
    KIND_WITHDRAW_CLEAN,
    KIND_CORROBORATE
} from "../../src/lib/CleanPass.sol";

/// @notice Stateful fuzz handler over the core and two IncentivePools on one covenant (index 0: ONCHAIN, funded short
///         so installments go UNFUNDED; index 1: VERDICT_ONLY). Every action is a real, signed transaction; the
///         contracts' expected refusals are caught inside each action, so the suite runs with `fail-on-revert = true`
///         and any revert of the handler itself fails the campaign. Ghost records feed the invariants (spec-v2 §3).
contract PoolHandler is Test {
    MethaneCovenant public immutable cov;
    bytes32 public immutable covId;
    IncentivePool public immutable poolOn;
    IncentivePool public immutable poolVo;

    bytes32[] internal sources;
    mapping(bytes32 => bytes32[]) internal proofOf;
    uint256[3] internal obsPks; // orgs A, B, C (independent observers)
    uint256 internal k;

    address public seller;
    address public buyer;
    address public sponsor = makeAddr("ph.sponsor");
    address public proposer = makeAddr("ph.proposer");
    address public coreChallenger = makeAddr("ph.coreChallenger");
    address public relayer = makeAddr("ph.relayer");
    address public poolChallenger = makeAddr("ph.poolChallenger");
    address[] public credited; // every address the pools can ever credit

    uint64 internal nonce = 5_000_000;
    uint256 internal evidenceCounter;
    uint256 internal sceneCounter;

    // ------------------------------------------------------------------ ghost records (per pool index)
    mapping(uint256 => bytes32[]) internal claimsOf;
    mapping(uint256 => uint256[]) internal passesOf;
    mapping(uint256 => uint256) public installmentCount; // highest installment id seen
    // corroboration cycle as observed from successful calls: 0 none, 1 pending, 2 challenged
    mapping(uint256 => mapping(bytes32 => uint8)) public corrState;
    mapping(uint256 => mapping(bytes32 => Corroboration)) internal corrTemplate;
    mapping(uint256 => uint256) public corrBondHeld;
    // sceneAt of the pass whose count first met the final installment's rules (count + two instruments), per claim
    mapping(uint256 => mapping(bytes32 => uint64)) public finalTrigger;

    // violations (each must stay 0)
    uint256 public vP3ReleaseOnClosedClaim;
    uint256 public vP6CoreChanged;
    uint256 public vP7ReplayAccepted;
    uint256 public vP9PastLiveDetection;

    // reachability counters (successful calls)
    mapping(bytes32 => uint256) public ok;

    constructor(
        MethaneCovenant cov_,
        bytes32 covId_,
        IncentivePool poolOn_,
        IncentivePool poolVo_,
        bytes32[] memory sources_,
        bytes32[][] memory proofs_,
        uint256[3] memory pks_,
        address[] memory controllers_
    ) {
        cov = cov_;
        covId = covId_;
        poolOn = poolOn_;
        poolVo = poolVo_;
        for (uint256 i; i < sources_.length; ++i) {
            sources.push(sources_[i]);
            proofOf[sources_[i]] = proofs_[i];
        }
        obsPks = pks_;
        MethaneCovenant.Covenant memory c = cov_.getCovenant(covId_);
        seller = c.seller;
        buyer = c.buyer;
        k = c.k;
        credited.push(seller);
        credited.push(buyer);
        credited.push(sponsor);
        credited.push(relayer);
        credited.push(poolChallenger);
        for (uint256 i; i < controllers_.length; ++i) {
            credited.push(controllers_[i]);
        }
        vm.deal(proposer, 1000 ether);
        vm.deal(coreChallenger, 1000 ether);
        vm.deal(relayer, 1000 ether);
        vm.deal(poolChallenger, 1000 ether);
        vm.deal(sponsor, 1000 ether);
    }

    // ------------------------------------------------------------------ views for the invariants

    function pool(uint256 i) public view returns (IncentivePool) {
        return i % 2 == 0 ? poolOn : poolVo;
    }

    function claimCount(uint256 p) external view returns (uint256) {
        return claimsOf[p].length;
    }

    function claimAt(uint256 p, uint256 i) external view returns (bytes32) {
        return claimsOf[p][i];
    }

    function passCount(uint256 p) external view returns (uint256) {
        return passesOf[p].length;
    }

    function passAt(uint256 p, uint256 i) external view returns (uint256) {
        return passesOf[p][i];
    }

    function creditedCount() external view returns (uint256) {
        return credited.length;
    }

    // ------------------------------------------------------------------ helpers

    /// @dev The core's money and episode bookkeeping as one hash: the covenant's accounting (funded, held, paid,
    ///      remaining, refunded, openEpisodes, settled), the episode count, the latest episode's record and the core's
    ///      ETH balance. Any episode transition or fund movement changes it.
    function _coreHash() internal view returns (bytes32) {
        uint256 n = cov.episodeCount();
        return keccak256(
            abi.encode(
                cov.getCovenant(covId),
                n,
                n == 0 ? bytes32(0) : keccak256(abi.encode(cov.getEpisode(n))),
                address(cov).balance
            )
        );
    }

    function _signCore(uint256 pk, Attestation memory a) internal view returns (bytes memory) {
        bytes32 d = AttestationLib.toTypedDataHash(cov.DOMAIN_SEPARATOR(), AttestationLib.hashMemory(a));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, d);
        return abi.encodePacked(r, s, v);
    }

    function _signPass(IncentivePool p, uint256 pk, CleanPass memory a) internal view returns (bytes memory) {
        bytes32 d = AttestationLib.toTypedDataHash(p.DOMAIN_SEPARATOR(), CleanPassLib.hash(a));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, d);
        return abi.encodePacked(r, s, v);
    }

    function _signCorr(IncentivePool p, uint256 pk, Corroboration memory a) internal view returns (bytes memory) {
        bytes32 d = AttestationLib.toTypedDataHash(p.DOMAIN_SEPARATOR(), CleanPassLib.hash(a));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, d);
        return abi.encodePacked(r, s, v);
    }

    function _coreAtt(bytes32 src, bytes32 ev, uint32 rate, uint64 detectedAt, uint8 kind)
        internal
        returns (Attestation memory a)
    {
        a.covenantId = covId;
        a.sourceId = src;
        a.evidenceHash = ev;
        a.emissionKgH = rate;
        a.uncertaintyKgH = 100;
        a.detectedAt = detectedAt;
        a.instrumentId = 1;
        a.providerId = "carbonmapper";
        a.kind = kind;
        a.nonce = nonce++;
        a.expiry = uint64(block.timestamp + 1 hours);
    }

    /// @dev Two attestations of one detection, signed by two distinct observer orgs.
    function _coreBatch(bytes32 src, bytes32 ev, uint32 rate, uint64 detectedAt, uint8 kind, uint256 pairSeed)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs)
    {
        (uint256 pa, uint256 pb) = _pair(pairSeed);
        atts = new Attestation[](2);
        sigs = new bytes[](2);
        atts[0] = _coreAtt(src, ev, rate, detectedAt, kind);
        atts[1] = _coreAtt(src, ev, rate, detectedAt, kind);
        sigs[0] = _signCore(pa, atts[0]);
        sigs[1] = _signCore(pb, atts[1]);
    }

    /// @dev Two distinct observer orgs out of A, B, C.
    function _pair(uint256 seed) internal view returns (uint256 a, uint256 b) {
        uint256 i = seed % 3;
        return (obsPks[i], obsPks[(i + 1) % 3]);
    }

    function _scanInstallments(uint256 p) internal {
        IncentivePool target = pool(p);
        uint256 n = installmentCount[p];
        while (target.getInstallment(n + 1).claimId != bytes32(0)) {
            ++n;
        }
        installmentCount[p] = n;
    }

    /// @dev Spec-v2 P9 condition, computed from the core directly.
    function _blockedByLaterDetection(IncentivePool.Claim memory c) internal view returns (bool) {
        uint256 id = cov.latestEpisode(c.covenantId, c.sourceId);
        if (id == 0 || id == c.episodeId) return false;
        MethaneCovenant.Episode memory e = cov.getEpisode(id);
        if (e.detectedAt <= c.since) return false;
        return e.state != MethaneCovenant.State.VOID;
    }

    /// @dev Prefers an OPEN claim (scanning from a seeded start) so random walks reach deep paths; one call in eight
    ///      takes any claim, terminal ones included, to keep exercising the refusals.
    function _claim(uint256 p, uint256 seed) internal view returns (bytes32 id, bool found) {
        uint256 n = claimsOf[p].length;
        if (n == 0) return (bytes32(0), false);
        uint256 start = seed % n;
        if (seed % 8 == 0) return (claimsOf[p][start], true);
        IncentivePool target = pool(p);
        for (uint256 i; i < n; ++i) {
            bytes32 c = claimsOf[p][(start + i) % n];
            if (target.getClaim(c).state == IncentivePool.ClaimState.OPEN) return (c, true);
        }
        return (claimsOf[p][start], true);
    }

    /// @dev A claim whose corroboration is in `state` (watchers react to `CorroborationPending` / `...Challenged`),
    ///      else `_claim`.
    function _claimWithCorr(uint256 p, uint256 seed, uint8 state) internal view returns (bytes32, bool) {
        uint256 n = claimsOf[p].length;
        if (n != 0 && seed % 8 != 0) {
            for (uint256 i; i < n; ++i) {
                bytes32 c = claimsOf[p][(seed % n + i) % n];
                if (corrState[p][c] == state) return (c, true);
            }
        }
        return _claim(p, seed);
    }

    function _challengedPass(uint256 p, uint256 seed) internal view returns (uint256, bool) {
        uint256 n = passesOf[p].length;
        if (n != 0 && seed % 8 != 0) {
            IncentivePool target = pool(p);
            for (uint256 i; i < n; ++i) {
                uint256 id_ = passesOf[p][(seed % n + i) % n];
                if (target.getPass(id_).state == IncentivePool.PassState.CHALLENGED) return (id_, true);
            }
        }
        return _pass(p, seed);
    }

    /// @dev Prefers a PENDING or CHALLENGED pass (same rule as `_claim`).
    function _pass(uint256 p, uint256 seed) internal view returns (uint256 id, bool found) {
        uint256 n = passesOf[p].length;
        if (n == 0) return (0, false);
        uint256 start = seed % n;
        if (seed % 8 == 0) return (passesOf[p][start], true);
        IncentivePool target = pool(p);
        for (uint256 i; i < n; ++i) {
            uint256 id_ = passesOf[p][(start + i) % n];
            IncentivePool.PassState st = target.getPass(id_).state;
            if (st == IncentivePool.PassState.PENDING || st == IncentivePool.PassState.CHALLENGED) return (id_, true);
        }
        return (passesOf[p][start], true);
    }

    modifier poolAction() {
        bytes32 before = _coreHash();
        _;
        if (_coreHash() != before) ++vP6CoreChanged; // P6: no pool action changes the core
        _scanInstallments(0);
        _scanInstallments(1);
        (IncentivePool.Pool memory pl,) = poolOn.getPool(covId);
        if (pl.state == IncentivePool.PoolState.CLOSING) ++ok["poolClosingSeen"];
        if (pl.state == IncentivePool.PoolState.CLOSED) ++ok["poolClosedSeen"];
    }

    /// @dev After a pass of `claimId` was counted: record the final installment's triggering scene the first time the
    ///      claim meets the final rule (count threshold and two instruments), as `PoolInstallments` does.
    function _noteCounted(uint256 p, bytes32 claimId, uint64 sceneAt) internal {
        if (finalTrigger[p][claimId] != 0) return;
        IncentivePool target = pool(p);
        IncentivePool.Claim memory c = target.getClaim(claimId);
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        uint8 bits = c.instrumentsSeen;
        uint256 instruments;
        while (bits != 0) {
            instruments += bits & 1;
            bits >>= 1;
        }
        if (c.counted >= params.installments[2].afterPasses && instruments >= 2) finalTrigger[p][claimId] = sceneAt;
    }

    // ------------------------------------------------------------------ core actions

    function propose(uint256 srcSeed, uint256 dtSeed, uint256 rateSeed, uint256 pairSeed) external {
        // half of the detections go to one noisy source, so the others see quiet runs long enough to count
        bytes32 src = srcSeed % 2 == 0 ? sources[sources.length - 1] : sources[(srcSeed >> 1) % sources.length];
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        uint256 dt = bound(dtSeed, 1 hours, 20 days);
        if (block.timestamp <= uint256(c.start) + dt) return;
        uint64 detectedAt = uint64(block.timestamp - dt);
        if (detectedAt >= c.end) return;
        uint32 rate = rateSeed % 3 == 0 ? 694 : (rateSeed % 3 == 1 ? 2548 : 6000);
        bytes32 ev = keccak256(abi.encode("ph.evidence", ++evidenceCounter));
        (Attestation[] memory atts, bytes[] memory sigs) = _coreBatch(src, ev, rate, detectedAt, KIND_DETECT, pairSeed);
        uint96 bond = cov.proposerBond();
        vm.prank(proposer);
        try cov.proposeEpisode{value: bond}(covId, src, proofOf[src], ev, atts, sigs) {
            ++ok["propose"];
        } catch {}
    }

    function challengeEpisode(uint256 epSeed) external {
        uint256 n = cov.episodeCount();
        if (n == 0) return;
        uint96 bond = cov.challengerBond();
        vm.prank(coreChallenger);
        try cov.challenge{value: bond}(1 + epSeed % n, 1) {
            ++ok["challengeEpisode"];
        } catch {}
    }

    function contestEpisode(uint256 epSeed) external {
        uint256 n = cov.episodeCount();
        if (n == 0) return;
        vm.prank(seller);
        try cov.contest(1 + epSeed % n) {
            ++ok["contestEpisode"];
        } catch {}
    }

    function resolveEpisode(uint256 epSeed, bool confirm) external {
        uint256 n = cov.episodeCount();
        if (n == 0) return;
        uint256 id = 1 + epSeed % n;
        MethaneCovenant.Episode memory e = cov.getEpisode(id);
        uint256 signers = e.state == MethaneCovenant.State.CONTESTED ? 3 : 2;
        uint8 kind = e.state == MethaneCovenant.State.CONTESTED || confirm ? KIND_CONFIRM : KIND_WITHDRAW;
        Attestation[] memory atts = new Attestation[](signers);
        bytes[] memory sigs = new bytes[](signers);
        for (uint256 i; i < signers; ++i) {
            atts[i] = _coreAtt(e.sourceId, e.evidenceHash, e.emissionKgH, e.detectedAt, kind);
            sigs[i] = _signCore(obsPks[i], atts[i]);
        }
        try cov.submitResolution(id, atts, sigs) {
            ++ok["resolveEpisode"];
        } catch {}
    }

    /// @dev Prefers an open episode whose deadline has passed (one call in eight takes any id).
    function finalizeEpisode(uint256 epSeed) external {
        uint256 n = cov.episodeCount();
        if (n == 0) return;
        uint256 id = 1 + epSeed % n;
        if (epSeed % 8 != 0) {
            for (uint256 i; i < n; ++i) {
                uint256 cand = 1 + (epSeed % n + i) % n;
                MethaneCovenant.Episode memory e = cov.getEpisode(cand);
                bool live = e.state == MethaneCovenant.State.PROPOSED || e.state == MethaneCovenant.State.CHALLENGED
                    || e.state == MethaneCovenant.State.CONTESTED;
                if (live && block.timestamp >= e.deadline) {
                    id = cand;
                    break;
                }
            }
        }
        try cov.finalize(id) {
            ++ok["finalizeEpisode"];
        } catch {}
    }

    function warp(uint256 secondsAhead) external {
        vm.warp(block.timestamp + bound(secondsAhead, 1 hours, 2 days));
    }

    /// Rare long jumps, so runs reach the covenant's end, CLOSING, CLOSED and the late installments.
    function warpFar(uint256 secondsAhead) external {
        vm.warp(block.timestamp + bound(secondsAhead, 2 days, 10 days));
    }

    // ------------------------------------------------------------------ pool actions

    /// A sponsor reacts to `InstallmentUnfunded` by topping up exactly what the queue needs; otherwise only dust
    /// arrives, so the pool stays short and later installments keep going UNFUNDED.
    function fund(uint256 amountSeed) external poolAction {
        (IncentivePool.Pool memory pl, IncentivePool.PoolParams memory params) = poolOn.getPool(covId);
        uint256 waitingBefore = _countUnfunded(0);
        uint256 amount =
            waitingBefore != 0 && amountSeed % 4 == 0 ? _unfundedSum(0) + 1 : bound(amountSeed, 1, 0.02 ether);
        vm.prank(sponsor);
        try poolOn.fund{value: amount}(covId, pl.policyHash, IncentivePool.Mode.ONCHAIN, params) {
            ++ok["fund"];
            if (_countUnfunded(0) < waitingBefore) ++ok["topUpScheduledUnfunded"];
        } catch {}
    }

    function openClaim(uint256 p, uint256 epSeed) external poolAction {
        uint256 n = cov.episodeCount();
        if (n == 0) return;
        try pool(p).openClaim(1 + epSeed % n) returns (bytes32 id) {
            claimsOf[p % 2].push(id);
            ++ok["openClaim"];
        } catch {}
    }

    function attestPass(uint256 p, uint256 claimSeed, uint256 sceneSeed, uint256 pairSeed, bool emi)
        external
        poolAction
    {
        p = p % 2;
        (bytes32 claimId, bool found) = _claim(p, claimSeed);
        if (!found) return;
        IncentivePool target = pool(p);
        (bool possible, uint64 sceneAt) = _sceneTime(target, claimId, sceneSeed);
        if (!possible) return;
        bool blocked = _blockedByLaterDetection(target.getClaim(claimId));
        (CleanPass[] memory atts, bytes[] memory sigs) =
            _passBatch(target, _newPass(claimId, target.getClaim(claimId).sourceId, sceneAt, emi), KIND_CLEAN, pairSeed);
        _submitPass(p, target, claimId, atts, sigs, blocked);
    }

    /// P8 probe: a scene still inside the publication lag must be refused (if accepted, P8 sees it).
    function attestEarlyPass(uint256 p, uint256 claimSeed, uint256 pairSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claim(p, claimSeed);
        if (!found) return;
        IncentivePool target = pool(p);
        IncentivePool.Claim memory c = target.getClaim(claimId);
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        uint64 sceneAt = uint64(block.timestamp - params.publicationLag / 2);
        if (sceneAt <= c.since || sceneAt >= cov.getCovenant(covId).end) return;
        (CleanPass[] memory atts, bytes[] memory sigs) =
            _passBatch(target, _newPass(claimId, c.sourceId, sceneAt, false), KIND_CLEAN, pairSeed);
        _submitPass(p, target, claimId, atts, sigs, _blockedByLaterDetection(c));
    }

    function _submitPass(
        uint256 p,
        IncentivePool target,
        bytes32 claimId,
        CleanPass[] memory atts,
        bytes[] memory sigs,
        bool blocked
    ) internal {
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        vm.prank(relayer);
        try target.attestCleanPass{value: params.passBond}(claimId, atts, sigs) returns (uint256 passId) {
            passesOf[p].push(passId);
            ++ok["attestPass"];
            if (blocked) ++vP9PastLiveDetection;
        } catch {}
    }

    /// @dev A scene time that the pool's time rules can accept: after `since`, older than the publication lag, before
    ///      the covenant's end.
    function _sceneTime(IncentivePool target, bytes32 claimId, uint256 seed) internal view returns (bool, uint64) {
        IncentivePool.Claim memory c = target.getClaim(claimId);
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        uint256 lo = uint256(c.since) + 1;
        if (block.timestamp < params.publicationLag) return (false, 0);
        uint256 hi = block.timestamp - params.publicationLag;
        uint256 end = cov.getCovenant(covId).end;
        if (hi >= end) hi = end - 1;
        if (hi < lo) return (false, 0);
        return (true, uint64(lo + seed % (hi - lo + 1)));
    }

    function _newPass(bytes32 claimId, bytes32 src, uint64 sceneAt, bool emi) internal returns (CleanPass memory t) {
        t.claimId = claimId;
        t.sourceId = src;
        t.sceneId = keccak256(abi.encode("ph.scene", ++sceneCounter));
        t.sceneHash = keccak256(abi.encode(sceneCounter));
        t.sceneAt = sceneAt;
        t.publishedAt = sceneAt;
        t.instrumentId = emi ? 2 : 1;
        t.providerId = "carbonmapper";
        t.notCloudy = true;
        t.floorKgH = 100;
        t.rateFloorKgH = 100;
        t.kind = KIND_CLEAN;
    }

    function _passBatch(IncentivePool target, CleanPass memory t, uint8 kind, uint256 pairSeed)
        internal
        returns (CleanPass[] memory atts, bytes[] memory sigs)
    {
        (uint256 pa, uint256 pb) = _pair(pairSeed);
        atts = new CleanPass[](2);
        sigs = new bytes[](2);
        for (uint256 i; i < 2; ++i) {
            atts[i] = CleanPass(
                t.claimId,
                t.sourceId,
                t.sceneId,
                t.sceneHash,
                t.sceneAt,
                t.publishedAt,
                t.instrumentId,
                t.providerId,
                t.cloudBp,
                t.notCloudy,
                t.floorKgH,
                t.rateFloorKgH,
                kind,
                nonce++,
                uint64(block.timestamp + 1 hours)
            );
            sigs[i] = _signPass(target, i == 0 ? pa : pb, atts[i]);
        }
    }

    function challengePass(uint256 p, uint256 passSeed) external poolAction {
        p = p % 2;
        (uint256 passId, bool found) = _pass(p, passSeed);
        if (!found) return;
        IncentivePool target = pool(p);
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        vm.prank(poolChallenger);
        try target.challengePass{value: params.challengerBond}(passId, 3) {
            ++ok["challengePass"];
        } catch {}
    }

    function resolvePass(uint256 p, uint256 passSeed, uint256 pairSeed, bool confirm) external poolAction {
        p = p % 2;
        (uint256 passId, bool found) = _challengedPass(p, passSeed); // observers answer challenges
        if (!found) return;
        IncentivePool target = pool(p);
        IncentivePool.Pass memory ps = target.getPass(passId);
        IncentivePool.Claim memory c = target.getClaim(ps.claimId);
        bool blocked = _blockedByLaterDetection(c);
        CleanPass memory t = CleanPass(
            ps.claimId,
            c.sourceId,
            ps.sceneId,
            ps.sceneHash,
            ps.sceneAt,
            ps.publishedAt,
            ps.instrumentId,
            "carbonmapper",
            ps.cloudBp,
            true,
            100,
            100,
            0,
            0,
            0
        );
        (CleanPass[] memory atts, bytes[] memory sigs) =
            _passBatch(target, t, confirm ? KIND_CONFIRM_CLEAN : KIND_WITHDRAW_CLEAN, pairSeed);
        try target.resolvePass(passId, atts, sigs) {
            ++ok[confirm ? bytes32("resolvePassConfirm") : bytes32("resolvePassWithdraw")];
            if (confirm && blocked) ++vP9PastLiveDetection;
            if (confirm) _noteCounted(p, ps.claimId, ps.sceneAt);
        } catch {}
    }

    function countPass(uint256 p, uint256 passSeed) external poolAction {
        p = p % 2;
        (uint256 passId, bool found) = _pass(p, passSeed);
        if (!found) return;
        IncentivePool target = pool(p);
        bool blocked = _blockedByLaterDetection(target.getClaim(target.getPass(passId).claimId));
        try target.countPass(passId) {
            ++ok["countPass"];
            if (blocked) ++vP9PastLiveDetection;
            IncentivePool.Pass memory ps = target.getPass(passId);
            _noteCounted(p, ps.claimId, ps.sceneAt);
        } catch {}
    }

    function finalizePass(uint256 p, uint256 passSeed) external poolAction {
        p = p % 2;
        (uint256 passId, bool found) = _challengedPass(p, passSeed);
        if (!found) return;
        try pool(p).finalizePass(passId) {
            ++ok["finalizePass"];
        } catch {}
    }

    function voidPass(uint256 p, uint256 passSeed) external poolAction {
        p = p % 2;
        (uint256 passId, bool found) = _pass(p, passSeed);
        if (!found) return;
        try pool(p).voidPass(passId) {
            ++ok["voidPass"];
        } catch {}
    }

    function attestCorroboration(uint256 p, uint256 claimSeed, uint256 pairSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claimWithCorr(p, claimSeed, 2); // answer a challenge when there is one
        if (!found) return;
        IncentivePool target = pool(p);
        uint8 st = corrState[p][claimId];
        Corroboration memory t = st == 2 ? corrTemplate[p][claimId] : _newCorr(target, claimId); // an answer: same
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatch(target, t, pairSeed);
        try target.attestCorroboration(claimId, atts, sigs) {
            _afterCorrAttested(p, target, claimId, st, t);
        } catch {}
    }

    function _newCorr(IncentivePool target, bytes32 claimId) internal view returns (Corroboration memory t) {
        IncentivePool.Claim memory c = target.getClaim(claimId);
        t.claimId = claimId;
        t.sourceId = c.sourceId;
        t.providerId = "unep-mars";
        t.marsSnapshotHash = keccak256(abi.encode("ph.snapshot", block.timestamp));
        t.windowFrom = c.since;
        t.windowTo = uint64(block.timestamp);
        t.kind = KIND_CORROBORATE;
    }

    function _corrBatch(IncentivePool target, Corroboration memory t, uint256 pairSeed)
        internal
        returns (Corroboration[] memory atts, bytes[] memory sigs)
    {
        (uint256 pa, uint256 pb) = _pair(pairSeed);
        atts = new Corroboration[](2);
        sigs = new bytes[](2);
        for (uint256 i; i < 2; ++i) {
            atts[i] = Corroboration(
                t.claimId,
                t.sourceId,
                t.providerId,
                t.marsSnapshotHash,
                t.windowFrom,
                t.windowTo,
                0,
                KIND_CORROBORATE,
                nonce++,
                uint64(block.timestamp + 1 hours)
            );
            sigs[i] = _signCorr(target, i == 0 ? pa : pb, atts[i]);
        }
    }

    function _afterCorrAttested(uint256 p, IncentivePool target, bytes32 claimId, uint8 st, Corroboration memory t)
        internal
    {
        if (st == 2) {
            (, IncentivePool.PoolParams memory params) = target.getPool(covId);
            corrBondHeld[p] -= params.challengerBond; // answered: the bond went to the seller
            corrState[p][claimId] = 0;
            ++ok["corroborationAnswered"];
        } else {
            corrState[p][claimId] = 1;
            corrTemplate[p][claimId] = t;
            ++ok["corroborationPending"];
        }
    }

    function challengeCorroboration(uint256 p, uint256 claimSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claimWithCorr(p, claimSeed, 1);
        if (!found) return;
        IncentivePool target = pool(p);
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        vm.prank(poolChallenger);
        try target.challengeCorroboration{value: params.challengerBond}(claimId, 5) {
            corrState[p][claimId] = 2;
            corrBondHeld[p] += params.challengerBond;
            ++ok["corroborationChallenged"];
        } catch {}
    }

    function finalizeCorroboration(uint256 p, uint256 claimSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claimWithCorr(p, claimSeed, claimSeed % 2 == 0 ? 1 : 2);
        if (!found) return;
        IncentivePool target = pool(p);
        uint8 st = corrState[p][claimId];
        try target.finalizeCorroboration(claimId) {
            if (st == 2) {
                (, IncentivePool.PoolParams memory params) = target.getPool(covId);
                corrBondHeld[p] -= params.challengerBond; // dropped: refunded to the challenger
                ++ok["corroborationDropped"];
            } else {
                ++ok["corroborationEffective"];
            }
            corrState[p][claimId] = 0;
        } catch {}
    }

    function release(uint256 p, uint256 instSeed) external poolAction {
        p = p % 2;
        uint256 n = installmentCount[p];
        if (n == 0) return;
        IncentivePool target = pool(p);
        uint256 id = 1 + instSeed % n;
        if (instSeed % 8 != 0) {
            // prefer a PENDING installment
            for (uint256 i; i < n; ++i) {
                uint256 cand = 1 + (instSeed % n + i) % n;
                if (target.getInstallment(cand).state == IncentivePool.InstState.PENDING) {
                    id = cand;
                    break;
                }
            }
        }
        IncentivePool.Installment memory it = target.getInstallment(id);
        IncentivePool.Claim memory c = target.getClaim(it.claimId);
        bool blocked = _blockedByLaterDetection(c);
        try target.release(id) {
            ++ok[it.index == 2 ? bytes32("releaseFinal") : bytes32("release")];
            if (c.state != IncentivePool.ClaimState.OPEN) ++vP3ReleaseOnClosedClaim;
            if (blocked) ++vP9PastLiveDetection;
        } catch {}
    }

    function relapse(uint256 p, uint256 claimSeed, uint256 epSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claim(p, claimSeed);
        uint256 n = cov.episodeCount();
        if (!found || n == 0) return;
        if (claimSeed % 8 != 0) {
            // a sponsor's watcher first looks at claims carrying an UNFUNDED installment
            uint256 nc = claimsOf[p].length;
            for (uint256 i; i < nc; ++i) {
                bytes32 cand = claimsOf[p][(claimSeed % nc + i) % nc];
                if (pool(p).getClaim(cand).state == IncentivePool.ClaimState.OPEN && _hasUnfunded(p, cand)) {
                    claimId = cand;
                    break;
                }
            }
        }
        IncentivePool target = pool(p);
        bool hadUnfunded = _hasUnfunded(p, claimId);
        IncentivePool.Claim memory c = target.getClaim(claimId);
        uint256 latest = cov.latestEpisode(covId, c.sourceId);
        uint256 ep = epSeed % 4 != 0 && latest != 0 ? latest : 1 + epSeed % n; // usually the source's newest episode
        try target.relapse(claimId, ep) {
            ++ok["relapse"];
            if (hadUnfunded) ++ok["relapseOverUnfunded"];
        } catch {}
    }

    function _countUnfunded(uint256 p) internal view returns (uint256 n) {
        IncentivePool target = pool(p);
        uint256 m = installmentCount[p];
        for (uint256 i = 1; i <= m; ++i) {
            if (target.getInstallment(i).state == IncentivePool.InstState.UNFUNDED) ++n;
        }
    }

    function _unfundedSum(uint256 p) internal view returns (uint256 sum) {
        IncentivePool target = pool(p);
        uint256 m = installmentCount[p];
        for (uint256 i = 1; i <= m; ++i) {
            IncentivePool.Installment memory it = target.getInstallment(i);
            if (it.state == IncentivePool.InstState.UNFUNDED) sum += it.amount;
        }
    }

    function _hasUnfunded(uint256 p, bytes32 claimId) internal view returns (bool) {
        IncentivePool target = pool(p);
        uint256 n = installmentCount[p];
        for (uint256 i = 1; i <= n; ++i) {
            IncentivePool.Installment memory it = target.getInstallment(i);
            if (it.claimId == claimId && it.state == IncentivePool.InstState.UNFUNDED) return true;
        }
        return false;
    }

    function closeClaim(uint256 p, uint256 claimSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claim(p, claimSeed);
        if (!found) return;
        try pool(p).closeClaim(claimId) {
            ++ok["closeClaim"];
        } catch {}
    }

    function sponsorWithdraw(uint256 p, bool bySponsor) external poolAction {
        address who = bySponsor ? sponsor : buyer;
        vm.prank(who);
        try pool(p).sponsorWithdraw(covId) {
            ++ok["sponsorWithdraw"];
        } catch {}
    }

    function withdraw(uint256 p, uint256 actorSeed) external poolAction {
        address who = credited[actorSeed % credited.length];
        vm.prank(who);
        try pool(p).withdraw() {
            ++ok["withdraw"];
        } catch {}
    }

    // ------------------------------------------------------------------ P7: replay attempts (must always fail)

    /// A core detection signed for the core's domain, submitted to the pool as a clean pass.
    function replayCoreIntoPool(uint256 p, uint256 claimSeed, uint256 pairSeed) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claim(p, claimSeed);
        if (!found) return;
        IncentivePool target = pool(p);
        IncentivePool.Claim memory c = target.getClaim(claimId);
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        if (block.timestamp < uint256(c.since) + params.publicationLag + 2) return;
        uint64 sceneAt = uint64(block.timestamp - params.publicationLag - 1);
        CleanPass memory t = CleanPass(
            claimId,
            c.sourceId,
            keccak256(abi.encode("ph.replay", ++sceneCounter)),
            bytes32(sceneCounter),
            sceneAt,
            sceneAt,
            1,
            "carbonmapper",
            0,
            true,
            100,
            100,
            KIND_CLEAN,
            0,
            0
        );
        (CleanPass[] memory atts, bytes[] memory sigs) = _passBatch(target, t, KIND_CLEAN, pairSeed);
        (uint256 pa, uint256 pb) = _pair(pairSeed);
        for (uint256 i; i < 2; ++i) {
            Attestation memory a = _coreAtt(c.sourceId, t.sceneHash, c.rateKgH, sceneAt, KIND_DETECT);
            a.nonce = atts[i].nonce;
            a.expiry = atts[i].expiry;
            sigs[i] = _signCore(i == 0 ? pa : pb, a);
        }
        vm.prank(relayer);
        try target.attestCleanPass{value: params.passBond}(claimId, atts, sigs) {
            ++vP7ReplayAccepted;
        } catch {}
    }

    function _cpLike(bytes32 src, bytes32 ev, uint64 at_, uint64 n, uint64 expiry)
        internal
        pure
        returns (CleanPass memory cp)
    {
        cp.sourceId = src;
        cp.sceneHash = ev;
        cp.sceneAt = at_;
        cp.publishedAt = at_;
        cp.instrumentId = 1;
        cp.providerId = "carbonmapper";
        cp.notCloudy = true;
        cp.floorKgH = 100;
        cp.rateFloorKgH = 100;
        cp.kind = KIND_CLEAN;
        cp.nonce = n;
        cp.expiry = expiry;
    }

    /// The same clean pass signed under a foreign EIP-712 domain (the core as verifying contract, or another chain).
    function replayForeignDomain(uint256 p, uint256 claimSeed, uint256 pairSeed, bool otherChain) external poolAction {
        p = p % 2;
        (bytes32 claimId, bool found) = _claim(p, claimSeed);
        if (!found) return;
        IncentivePool target = pool(p);
        (bool possible, uint64 sceneAt) = _sceneTime(target, claimId, claimSeed);
        if (!possible) return;
        bytes32 foreign = otherChain
            ? AttestationLib.domainSeparator(84_532, address(target))
            : AttestationLib.domainSeparator(block.chainid, address(cov));
        (CleanPass[] memory atts, bytes[] memory sigs) =
            _foreignBatch(_newPass(claimId, target.getClaim(claimId).sourceId, sceneAt, false), foreign, pairSeed);
        _submitReplay(p, target, claimId, atts, sigs);
    }

    function _foreignBatch(CleanPass memory t, bytes32 foreign, uint256 pairSeed)
        internal
        returns (CleanPass[] memory atts, bytes[] memory sigs)
    {
        (uint256 pa, uint256 pb) = _pair(pairSeed);
        atts = new CleanPass[](2);
        sigs = new bytes[](2);
        for (uint256 i; i < 2; ++i) {
            atts[i] = CleanPass(
                t.claimId,
                t.sourceId,
                t.sceneId,
                t.sceneHash,
                t.sceneAt,
                t.publishedAt,
                t.instrumentId,
                t.providerId,
                t.cloudBp,
                t.notCloudy,
                t.floorKgH,
                t.rateFloorKgH,
                KIND_CLEAN,
                nonce++,
                uint64(block.timestamp + 1 hours)
            );
            bytes32 d = AttestationLib.toTypedDataHash(foreign, CleanPassLib.hash(atts[i]));
            (uint8 v, bytes32 r, bytes32 sg) = vm.sign(i == 0 ? pa : pb, d);
            sigs[i] = abi.encodePacked(r, sg, v);
        }
    }

    function _submitReplay(
        uint256 p,
        IncentivePool target,
        bytes32 claimId,
        CleanPass[] memory atts,
        bytes[] memory sigs
    ) internal {
        (, IncentivePool.PoolParams memory params) = target.getPool(covId);
        vm.prank(relayer);
        try target.attestCleanPass{value: params.passBond}(claimId, atts, sigs) returns (uint256 passId) {
            passesOf[p].push(passId); // keep the books whole for P4 even if the guard broke
            ++vP7ReplayAccepted;
        } catch {}
    }

    /// A clean pass signed for the pool's domain, submitted to the core as a detection.
    function replayPoolIntoCore(uint256 srcSeed, uint256 pairSeed) external {
        bytes32 src = sources[srcSeed % sources.length];
        MethaneCovenant.Covenant memory cv = cov.getCovenant(covId);
        if (block.timestamp <= uint256(cv.start) + 1 hours || block.timestamp - 1 hours >= cv.end) return;
        uint64 detectedAt = uint64(block.timestamp - 1 hours);
        bytes32 ev = keccak256(abi.encode("ph.replayEvidence", ++evidenceCounter));
        (Attestation[] memory atts, bytes[] memory sigs) = _coreBatch(src, ev, 2548, detectedAt, KIND_DETECT, pairSeed);
        (uint256 pa, uint256 pb) = _pair(pairSeed);
        for (uint256 i; i < 2; ++i) {
            CleanPass memory cp = _cpLike(src, ev, detectedAt, atts[i].nonce, atts[i].expiry);
            sigs[i] = _signPass(poolOn, i == 0 ? pa : pb, cp); // a clean-pass signature on a core detection
        }
        uint96 bond = cov.proposerBond();
        vm.prank(proposer);
        try cov.proposeEpisode{value: bond}(covId, src, proofOf[src], ev, atts, sigs) {
            ++vP7ReplayAccepted;
        } catch {}
    }
}

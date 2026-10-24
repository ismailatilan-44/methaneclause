// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Attestation, AttestationLib, KIND_DETECT, KIND_CONFIRM, KIND_WITHDRAW} from "../../src/lib/Attestation.sol";

/// @notice Stateful fuzz handler. Drives every external entry point with random (often invalid) inputs and keeps
///         ghost records that the invariants compare against contract state.
contract Handler is Test {
    struct Signer {
        uint256 pk;
        bytes32 org;
        bool party; // key belongs to the seller or buyer org
    }

    MethaneCovenant public immutable cov;
    KeyRegistry public immutable registry;
    bytes32 public immutable covId;
    address public immutable seller;
    address public immutable buyer;
    address public immutable admin;
    uint96 public immutable proposerBond;
    uint96 public immutable challengerBond;
    uint8 public immutable k;

    bytes32[] internal sources;
    mapping(bytes32 => bytes32[]) internal proofOf;
    Signer[] internal signers;
    address[] public actors; // everyone who can ever be credited

    uint64 internal nonce = 1_000_000;
    uint256 internal evidenceCounter;

    // ghost state
    uint256[] public episodeIds;
    mapping(uint256 => uint256) public ghostProposalOrgs; // distinct independent orgs among signers
    mapping(uint256 => uint256) public ghostContestConfirmOrgs;
    uint256 public ghostPartySignaturesAccepted;
    uint256 public ghostMitigationStateChanges;
    uint256 public ghostAdminActionsAccepted;
    mapping(address => uint256) public ghostWithdrawn;
    mapping(bytes32 => uint256) public calls;
    mapping(bytes32 => uint256) public succeeded;

    constructor(
        MethaneCovenant cov_,
        bytes32 covId_,
        address seller_,
        address buyer_,
        address admin_,
        bytes32[] memory sources_,
        bytes32[][] memory proofs_,
        uint256[] memory pks_,
        bytes32[] memory orgs_,
        bool[] memory party_
    ) {
        cov = cov_;
        registry = cov_.registry();
        covId = covId_;
        seller = seller_;
        buyer = buyer_;
        admin = admin_;
        proposerBond = cov_.proposerBond();
        challengerBond = cov_.challengerBond();
        k = cov_.getCovenant(covId_).k;
        for (uint256 i; i < sources_.length; ++i) {
            sources.push(sources_[i]);
            proofOf[sources_[i]] = proofs_[i];
        }
        for (uint256 i; i < pks_.length; ++i) {
            signers.push(Signer(pks_[i], orgs_[i], party_[i]));
        }
        actors.push(seller_);
        actors.push(buyer_);
        actors.push(makeAddr("proposer1"));
        actors.push(makeAddr("proposer2"));
        actors.push(makeAddr("challenger1"));
        actors.push(makeAddr("challenger2"));
        for (uint256 i = 2; i < actors.length; ++i) {
            vm.deal(actors[i], 100 ether);
        }
    }

    // ------------------------------------------------------------------ actions

    function propose(uint256 srcSeed, uint256 mask, uint32 rate, uint256 detSeed, uint256 actorSeed) external {
        calls["propose"]++;
        bytes32 src = srcSeed % 10 == 9 ? keccak256("UNCOVERED") : sources[srcSeed % sources.length];
        MethaneCovenant.Covenant memory c = cov.getCovenant(covId);
        if (block.timestamp <= uint256(c.start) + 1) return;
        uint64 detectedAt = uint64(bound(detSeed, uint256(c.start) + 1, block.timestamp));
        rate = uint32(bound(rate, 50, 20_000));
        uint256 m = _mask(mask);
        bytes32 ev = keccak256(abi.encode("evidence", evidenceCounter++));
        (Attestation[] memory atts, bytes[] memory sigs) = _build(m, src, ev, rate, detectedAt, KIND_DETECT);

        address p = actors[2 + (actorSeed % 2)];
        vm.prank(p);
        try cov.proposeEpisode{value: proposerBond}(covId, src, proofOf[src], ev, atts, sigs) returns (uint256 id) {
            succeeded["propose"]++;
            episodeIds.push(id);
            (uint256 distinct, uint256 party) = _count(m);
            ghostProposalOrgs[id] = distinct;
            ghostPartySignaturesAccepted += party;
        } catch {}
    }

    function challenge(uint256 epSeed, uint8 reason, uint256 actorSeed) external {
        calls["challenge"]++;
        if (episodeIds.length == 0) return;
        uint256 id = _pick(epSeed);
        reason = uint8(bound(reason, 0, 6));
        vm.prank(actors[4 + (actorSeed % 2)]);
        try cov.challenge{value: challengerBond}(id, reason) {
            succeeded["challenge"]++;
        } catch {}
    }

    function resolve(uint256 epSeed, bool confirm, uint256 mask) external {
        calls["resolve"]++;
        if (episodeIds.length == 0) return;
        uint256 id = _pick(epSeed);
        MethaneCovenant.Episode memory e = cov.getEpisode(id);
        uint256 m = _mask(mask);
        (Attestation[] memory atts, bytes[] memory sigs) =
            _build(m, e.sourceId, e.evidenceHash, e.emissionKgH, e.detectedAt, confirm ? KIND_CONFIRM : KIND_WITHDRAW);
        bool wasContested = e.state == MethaneCovenant.State.CONTESTED;
        if (e.state == MethaneCovenant.State.CHALLENGED || wasContested) calls["resolve-open"]++;
        try cov.submitResolution(id, atts, sigs) {
            succeeded[confirm ? bytes32("res-confirm") : bytes32("res-withdraw")]++;
            (uint256 distinct, uint256 party) = _count(m);
            ghostPartySignaturesAccepted += party;
            if (wasContested) ghostContestConfirmOrgs[id] = distinct;
        } catch {}
    }

    function contest(uint256 epSeed) external {
        calls["contest"]++;
        if (episodeIds.length == 0) return;
        vm.prank(seller);
        try cov.contest(_pick(epSeed)) {
            succeeded["contest"]++;
        } catch {}
    }

    function finalize(uint256 epSeed) external {
        calls["finalize"]++;
        if (episodeIds.length == 0) return;
        try cov.finalize(_pick(epSeed)) {
            succeeded["finalize"]++;
        } catch {}
    }

    function warp(uint256 secs) external {
        calls["warp"]++;
        // Mostly short steps (inside windows), sometimes a long jump (past term, to reach settlement).
        vm.warp(block.timestamp + (secs % 16 == 0 ? 10 days : bound(secs, 1 minutes, 1 days)));
    }

    function withdraw(uint256 actorSeed) external {
        calls["withdraw"]++;
        address a = actors[actorSeed % actors.length];
        uint256 amount = cov.withdrawable(a);
        vm.prank(a);
        try cov.withdraw() {
            ghostWithdrawn[a] += amount;
            succeeded["withdraw"]++;
        } catch {}
    }

    function fund(uint256 amount) external {
        calls["fund"]++;
        amount = bound(amount, 1, 2 ether);
        vm.deal(seller, seller.balance + amount);
        vm.prank(seller);
        try cov.fund{value: amount}(covId) {} catch {}
    }

    function settle() external {
        calls["settle"]++;
        try cov.settle(covId) {
            succeeded["settle"]++;
        } catch {}
    }

    /// @notice I6 probe: a mitigation claim must not change any covenant or episode state.
    function mitigationClaim(uint256 srcSeed, bytes32 claimHash) external {
        calls["mitigation"]++;
        bytes32 before = _stateDigest();
        vm.prank(seller);
        try cov.recordMitigationClaim(covId, sources[srcSeed % sources.length], claimHash) {} catch {}
        if (_stateDigest() != before) ghostMitigationStateChanges++;
    }

    /// @notice I7 probe: the deployer/admin has no path to funds or keys after renouncing.
    function adminAttack(uint256 which, address target) external {
        calls["admin"]++;
        vm.startPrank(admin);
        bool ok;
        if (which % 3 == 0) {
            try registry.registerKey("EVIL", KeyRegistry.Role.OBSERVER, target, 1, 0) {
                ok = true;
            } catch {}
        } else if (which % 3 == 1) {
            try registry.setOrgController("OBS_B", admin) {
                ok = true;
            } catch {}
        } else {
            try cov.withdraw() {
                ok = true;
            } catch {}
        }
        vm.stopPrank();
        if (ok) ghostAdminActionsAccepted++;
    }

    // ------------------------------------------------------------------ helpers

    function episodeCountGhost() external view returns (uint256) {
        return episodeIds.length;
    }

    function actorCount() external view returns (uint256) {
        return actors.length;
    }

    /// @dev Half the time pick a random subset (often invalid); otherwise a valid 2-3 independent-org subset.
    /// @dev Half the time the most recent episode (likely still open), otherwise any episode.
    function _pick(uint256 seed) internal view returns (uint256) {
        uint256 n = episodeIds.length;
        return seed % 2 == 0 ? episodeIds[n - 1] : episodeIds[(seed >> 1) % n];
    }

    function _mask(uint256 seed) internal view returns (uint256 m) {
        uint256 n = signers.length;
        if (seed % 2 == 0) {
            m = (seed >> 1) % (1 << n);
            if (m == 0) m = 1;
        } else {
            for (uint256 i; i < n; ++i) {
                if (!signers[i].party && ((seed >> (i + 1)) & 1 == 1)) m |= 1 << i;
            }
            // ensure at least orgs B and C (indices 2, 3 by construction)
            m |= (1 << 2) | (1 << 3);
        }
    }

    function _build(uint256 m, bytes32 src, bytes32 ev, uint32 rate, uint64 detectedAt, uint8 kind)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs)
    {
        uint256 count;
        for (uint256 i; i < signers.length; ++i) {
            if (m & (1 << i) != 0) count++;
        }
        atts = new Attestation[](count);
        sigs = new bytes[](count);
        uint256 j;
        bytes32 ds = AttestationLib.domainSeparator(block.chainid, address(cov));
        for (uint256 i; i < signers.length; ++i) {
            if (m & (1 << i) == 0) continue;
            Attestation memory a;
            a.covenantId = covId;
            a.sourceId = src;
            a.evidenceHash = ev;
            a.emissionKgH = rate;
            a.uncertaintyKgH = rate / 10;
            a.detectedAt = detectedAt;
            a.instrumentId = 1;
            a.providerId = "carbonmapper";
            a.kind = kind;
            a.nonce = nonce++;
            a.expiry = uint64(block.timestamp + 1 hours);
            (uint8 v, bytes32 r, bytes32 s) =
                vm.sign(signers[i].pk, AttestationLib.toTypedDataHash(ds, AttestationLib.hashMemory(a)));
            atts[j] = a;
            sigs[j++] = abi.encodePacked(r, s, v);
        }
    }

    function _count(uint256 m) internal view returns (uint256 distinct, uint256 party) {
        bytes32[] memory seen = new bytes32[](signers.length);
        for (uint256 i; i < signers.length; ++i) {
            if (m & (1 << i) == 0) continue;
            if (signers[i].party) {
                party++;
                continue;
            }
            bool dup;
            for (uint256 j; j < distinct; ++j) {
                if (seen[j] == signers[i].org) dup = true;
            }
            if (!dup) seen[distinct++] = signers[i].org;
        }
    }

    function _stateDigest() internal view returns (bytes32 h) {
        h = keccak256(abi.encode(cov.getCovenant(covId), cov.episodeCount(), address(cov).balance));
        for (uint256 i; i < episodeIds.length; ++i) {
            h = keccak256(abi.encode(h, cov.getEpisode(episodeIds[i])));
        }
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {KeyRegistry} from "../KeyRegistry.sol";
import {AttestationLib} from "./Attestation.sol";
import {SigLib} from "./SigLib.sol";
import {CleanPass, Corroboration, CleanPassLib, KIND_CORROBORATE} from "./CleanPass.sol";

/// @title PoolVerify — the IncentivePool's attestation checks, as an external (linked) library
/// @notice Moved out of IncentivePool so the pool stays under the EIP-170 code-size limit (spec-v2 §15 A2 audit (4)).
///         It runs by DELEGATECALL in the pool's context: the nonce bitmap it writes is the pool's storage, the domain
///         separator is the pool's, and its errors carry the same selectors the pool declares (spec-v2 §3.1), so a
///         revert reads exactly as if the pool had raised it. Rules mirror the core's `_verifyQuorum` /
///         `_checkAttestation`: fail closed, one bad attestation reverts the whole call.
library PoolVerify {
    /// @dev Values every attestation in one batch must carry (the core's `Expect` pattern).
    struct Expect {
        bytes32 claimId;
        bytes32 sourceId;
        bytes32 sceneId; // clean passes only
        bytes32 sceneHash; // clean passes only
        uint8 kind;
        uint256 required;
        bytes32 sellerOrg;
        bytes32 buyerOrg;
    }

    uint256 internal constant MAX_EXPIRY_HORIZON = 1 days;
    bytes32 internal constant PROVIDER_MARS = "unep-mars";

    // Same signatures (hence selectors) as IncentivePool's errors.
    error QuorumNotMet(uint256 have, uint256 need);
    error AttestorNotIndependent(address signer);
    error SelfAttestation(address signer);
    error RevokedKey(address signer);
    error InvalidSignature();
    error AttestationMismatch(uint256 index);
    error Replay(address signer, uint64 nonce);
    error Expired(uint64 expiry);
    error BadKind();
    error Cloudy();
    error FloorAboveRate();
    error BadParams();
    error SceneOutOfRange();
    error BeforePublicationLag();

    // Same signatures (hence topics) as IncentivePool's events.
    event ObserverFeePaid(uint256 indexed passId, address indexed observer, uint256 amount);
    event Credited(address indexed account, uint256 amount);

    /// @notice k distinct observer orgs signed the same clean pass (claim, source, scene, bundle hash and every scene
    ///         field), of the expected kind, unexpired. Returns the distinct signing orgs (observer-fee recipients).
    function cleanQuorum(
        Expect memory ex,
        CleanPass[] calldata atts,
        bytes[] calldata sigs,
        bytes32 ds,
        KeyRegistry registry,
        mapping(address => mapping(uint256 => uint256)) storage nonces
    ) external returns (bytes32[] memory orgs) {
        uint256 n = atts.length;
        orgs = new bytes32[](n);
        uint256 distinct = 0;
        for (uint256 i; i < n; ++i) {
            CleanPass calldata a = atts[i];
            if (
                a.claimId != ex.claimId || a.sourceId != ex.sourceId || a.sceneId != ex.sceneId
                    || a.sceneHash != ex.sceneHash || !_sameScene(a, atts[0])
            ) revert AttestationMismatch(i);
            if (a.kind != ex.kind) revert BadKind();
            _checkExpiry(a.expiry);
            bytes32 org = _checkSigner(
                ex, AttestationLib.toTypedDataHash(ds, CleanPassLib.hash(a)), a.nonce, sigs[i], registry, nonces
            );
            distinct = _addDistinct(orgs, distinct, org);
        }
        if (distinct < ex.required) revert QuorumNotMet(distinct, ex.required);
        assembly ("memory-safe") {
            mstore(orgs, distinct) // shrink to the distinct orgs
        }
    }

    /// @notice k distinct observer orgs signed the same MARS corroboration (claim, source, provider, snapshot, window,
    ///         plume count), kind 7 from provider "unep-mars", unexpired.
    function corroborationQuorum(
        Expect memory ex,
        Corroboration[] calldata atts,
        bytes[] calldata sigs,
        bytes32 ds,
        KeyRegistry registry,
        mapping(address => mapping(uint256 => uint256)) storage nonces
    ) external {
        uint256 n = atts.length;
        bytes32[] memory orgs = new bytes32[](n);
        uint256 distinct = 0;
        for (uint256 i; i < n; ++i) {
            Corroboration calldata t = atts[i];
            if (t.claimId != ex.claimId || t.sourceId != ex.sourceId || !_sameWindow(t, atts[0])) {
                revert AttestationMismatch(i);
            }
            if (t.kind != KIND_CORROBORATE || t.providerId != PROVIDER_MARS) revert BadKind();
            _checkExpiry(t.expiry);
            bytes32 org = _checkSigner(
                ex, AttestationLib.toTypedDataHash(ds, CleanPassLib.hash(t)), t.nonce, sigs[i], registry, nonces
            );
            distinct = _addDistinct(orgs, distinct, org);
        }
        if (distinct < ex.required) revert QuorumNotMet(distinct, ex.required);
    }

    /// @notice spec-v2 §2 rules 5-6 and §3 on the first attestation of a clean-pass batch (the others must equal it):
    ///         provider's `not_cloudy` flag; instrument floor <= the episode's rate; instrument id in the claim's 8-bit
    ///         set (observer ids are 1..5, pipeline/observer/bundle.py); since < sceneAt <= publishedAt <= now;
    ///         sceneAt < end; now >= sceneAt + publicationLag (P8).
    function checkScene(CleanPass calldata a0, uint64 since, uint32 rateKgH, uint64 end, uint32 publicationLag)
        external
        view
    {
        if (!a0.notCloudy) revert Cloudy();
        if (a0.rateFloorKgH > rateKgH) revert FloorAboveRate();
        if (a0.instrumentId == 0 || a0.instrumentId > 7) revert BadParams();
        if (a0.sceneAt <= since || a0.publishedAt < a0.sceneAt || a0.publishedAt > block.timestamp || a0.sceneAt >= end)
        {
            revert SceneOutOfRange();
        }
        if (block.timestamp < uint256(a0.sceneAt) + publicationLag) revert BeforePublicationLag();
    }

    /// @notice Splits `fee` equally among the controllers of the distinct orgs that signed pass `passId`, crediting the
    ///         pool's `withdrawable` ledger (pull). An org without a controller forfeits its share; division dust is
    ///         not paid. Returns what was paid (the pool deducts it from the sponsors' balance).
    function payObserverFee(
        uint256 passId,
        bytes32[] storage orgs,
        uint256 fee,
        KeyRegistry registry,
        mapping(address => uint256) storage withdrawable
    ) external returns (uint256 paid) {
        uint256 n = orgs.length;
        uint256 share = fee / n;
        for (uint256 i; i < n; ++i) {
            address controller = registry.orgController(orgs[i]);
            if (controller == address(0) || share == 0) continue;
            paid += share;
            withdrawable[controller] += share;
            emit Credited(controller, share);
            emit ObserverFeePaid(passId, controller, share);
        }
    }

    function cleanPassDigest(bytes32 ds, CleanPass calldata a) external pure returns (bytes32) {
        return AttestationLib.toTypedDataHash(ds, CleanPassLib.hash(a));
    }

    function corroborationDigest(bytes32 ds, Corroboration calldata a) external pure returns (bytes32) {
        return AttestationLib.toTypedDataHash(ds, CleanPassLib.hash(a));
    }

    // ------------------------------------------------------------------ internal

    function _checkExpiry(uint64 expiry) private view {
        if (expiry < block.timestamp || expiry > block.timestamp + MAX_EXPIRY_HORIZON) revert Expired(expiry);
    }

    function _addDistinct(bytes32[] memory orgs, uint256 distinct, bytes32 org) private pure returns (uint256) {
        for (uint256 j; j < distinct; ++j) {
            if (orgs[j] == org) return distinct;
        }
        orgs[distinct] = org;
        return distinct + 1;
    }

    function _sameScene(CleanPass calldata a, CleanPass calldata a0) private pure returns (bool) {
        return a.sceneHash == a0.sceneHash && a.sceneAt == a0.sceneAt && a.publishedAt == a0.publishedAt
            && a.instrumentId == a0.instrumentId && a.providerId == a0.providerId && a.cloudBp == a0.cloudBp
            && a.notCloudy == a0.notCloudy && a.floorKgH == a0.floorKgH && a.rateFloorKgH == a0.rateFloorKgH;
    }

    function _sameWindow(Corroboration calldata t, Corroboration calldata t0) private pure returns (bool) {
        return t.providerId == t0.providerId && t.marsSnapshotHash == t0.marsSnapshotHash
            && t.windowFrom == t0.windowFrom && t.windowTo == t0.windowTo
            && t.marsPlumesInWindow == t0.marsPlumesInWindow;
    }

    /// @dev The core's signer rules on the pool's domain and the pool's own nonce bitmap.
    function _checkSigner(
        Expect memory ex,
        bytes32 digest,
        uint64 nonce,
        bytes calldata sig,
        KeyRegistry registry,
        mapping(address => mapping(uint256 => uint256)) storage nonces
    ) private returns (bytes32 org) {
        address signer = SigLib.recover(digest, sig);
        if (signer == address(0)) revert InvalidSignature();
        KeyRegistry.Role role;
        bool valid;
        (org, role, valid) = registry.keyStatus(signer, block.timestamp);
        if (org == bytes32(0)) revert InvalidSignature(); // recovers to an unregistered address: data was edited
        if (org == ex.sellerOrg || org == ex.buyerOrg) revert SelfAttestation(signer);
        if (role != KeyRegistry.Role.OBSERVER) revert AttestorNotIndependent(signer);
        if (!valid) revert RevokedKey(signer);
        uint256 word = nonce >> 8;
        uint256 bit = 1 << (nonce & 0xff);
        uint256 bits = nonces[signer][word];
        if (bits & bit != 0) revert Replay(signer, nonce);
        nonces[signer][word] = bits | bit;
    }
}

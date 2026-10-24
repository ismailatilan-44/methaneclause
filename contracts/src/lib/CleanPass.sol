// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

// v2 attestation kinds for the IncentivePool (spec-v2 §3.1). Disjoint from the core's 1-3 (belt-and-braces: the type
// hash and the verifying contract already separate pool and core signatures).
uint8 constant KIND_CLEAN = 4;
uint8 constant KIND_CONFIRM_CLEAN = 5;
uint8 constant KIND_WITHDRAW_CLEAN = 6;
uint8 constant KIND_CORROBORATE = 7;

/// @notice A qualified clean pass: k observer orgs attest that a provider scene over the facility shows no plume at its
///         linked sources (spec-v2 §2). `sceneHash` is the SHA-256 of the RFC 8785 clean-pass bundle (spec-v2 §4).
struct CleanPass {
    bytes32 claimId;
    bytes32 sourceId;
    bytes32 sceneId; // keccak256 of the provider scene id string
    bytes32 sceneHash;
    uint64 sceneAt;
    uint64 publishedAt;
    uint8 instrumentId;
    bytes32 providerId;
    uint16 cloudBp; // 100 x max(cloud_cover_pct, cloud_cover_pct_assessed)
    bool notCloudy; // Carbon Mapper's own flag (D3)
    uint32 floorKgH;
    uint32 rateFloorKgH; // published detection limit of the instrument; must be <= the episode's attested rate
    uint8 kind; // KIND_CLEAN | KIND_CONFIRM_CLEAN | KIND_WITHDRAW_CLEAN
    uint64 nonce;
    uint64 expiry;
}

/// @notice k observer orgs attest that the pinned UNEP MARS snapshot shows no plume at the facility in the window (D5).
struct Corroboration {
    bytes32 claimId;
    bytes32 sourceId;
    bytes32 providerId; // "unep-mars"
    bytes32 marsSnapshotHash;
    uint64 windowFrom;
    uint64 windowTo;
    uint16 marsPlumesInWindow; // must be 0
    uint8 kind; // KIND_CORROBORATE
    uint64 nonce;
    uint64 expiry;
}

/// @title CleanPassLib
/// @notice EIP-712 `hashStruct` for the pool's two attestation types. Domain = the core's (name "MethaneClause",
///         version "1") bound to the pool's address; use `AttestationLib.domainSeparator` / `toTypedDataHash`.
///         Off-chain signers (pipeline/observer/attest.py) must produce byte-identical digests (test vector
///         pipeline/tests/vectors/cleanpass_v1.json).
library CleanPassLib {
    bytes32 internal constant CLEANPASS_TYPEHASH = keccak256(
        "CleanPass(bytes32 claimId,bytes32 sourceId,bytes32 sceneId,bytes32 sceneHash,uint64 sceneAt,uint64 publishedAt,"
        "uint8 instrumentId,bytes32 providerId,uint16 cloudBp,bool notCloudy,uint32 floorKgH,uint32 rateFloorKgH,"
        "uint8 kind,uint64 nonce,uint64 expiry)"
    );

    bytes32 internal constant CORROBORATION_TYPEHASH = keccak256(
        "Corroboration(bytes32 claimId,bytes32 sourceId,bytes32 providerId,bytes32 marsSnapshotHash,uint64 windowFrom,"
        "uint64 windowTo,uint16 marsPlumesInWindow,uint8 kind,uint64 nonce,uint64 expiry)"
    );

    /// @dev Two abi.encode halves of static words, concatenated: identical bytes to one abi.encode (stack depth).
    function hash(CleanPass memory a) internal pure returns (bytes32) {
        return keccak256(
            bytes.concat(
                abi.encode(
                    CLEANPASS_TYPEHASH, a.claimId, a.sourceId, a.sceneId, a.sceneHash, a.sceneAt, a.publishedAt,
                    a.instrumentId
                ),
                abi.encode(a.providerId, a.cloudBp, a.notCloudy, a.floorKgH, a.rateFloorKgH, a.kind, a.nonce, a.expiry)
            )
        );
    }

    function hash(Corroboration memory a) internal pure returns (bytes32) {
        return keccak256(
            bytes.concat(
                abi.encode(CORROBORATION_TYPEHASH, a.claimId, a.sourceId, a.providerId, a.marsSnapshotHash),
                abi.encode(a.windowFrom, a.windowTo, a.marsPlumesInWindow, a.kind, a.nonce, a.expiry)
            )
        );
    }
}

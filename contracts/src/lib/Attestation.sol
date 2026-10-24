// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

// Attestation kinds. Only positive detections exist as evidence: there is no "no plume" kind.
uint8 constant KIND_DETECT = 1;
uint8 constant KIND_CONFIRM = 2;
uint8 constant KIND_WITHDRAW = 3;

/// @notice EIP-712 typed attestation signed by an observer key (spec §6).
/// @dev `evidenceHash` is the SHA-256 of the RFC 8785 (JCS) canonical bundle; the contract treats it as opaque.
struct Attestation {
    bytes32 covenantId;
    bytes32 sourceId;
    bytes32 evidenceHash;
    bytes32 modelHash; // 0x0 for detection attestations
    uint32 emissionKgH;
    uint32 uncertaintyKgH;
    uint64 detectedAt;
    uint8 instrumentId;
    bytes32 providerId;
    uint8 kind; // KIND_DETECT | KIND_CONFIRM | KIND_WITHDRAW
    uint64 nonce;
    uint64 expiry;
}

/// @title AttestationLib
/// @notice EIP-712 hashing for `Attestation` under the domain {name: "MethaneClause", version: "1", chainId,
///         verifyingContract}. Off-chain signers (pipeline/observer) must produce byte-identical digests.
library AttestationLib {
    string internal constant NAME = "MethaneClause";
    string internal constant VERSION = "1";

    bytes32 internal constant TYPEHASH = keccak256(
        "Attestation(bytes32 covenantId,bytes32 sourceId,bytes32 evidenceHash,bytes32 modelHash,uint32 emissionKgH,"
        "uint32 uncertaintyKgH,uint64 detectedAt,uint8 instrumentId,bytes32 providerId,uint8 kind,uint64 nonce,"
        "uint64 expiry)"
    );

    bytes32 internal constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");

    /// @notice EIP-712 `hashStruct(Attestation)`.
    function hash(Attestation calldata a) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(
                TYPEHASH,
                a.covenantId,
                a.sourceId,
                a.evidenceHash,
                a.modelHash,
                a.emissionKgH,
                a.uncertaintyKgH,
                a.detectedAt,
                a.instrumentId,
                a.providerId,
                a.kind,
                a.nonce,
                a.expiry
            )
        );
    }

    /// @notice Same as `hash` for memory structs (used by tests and tooling).
    function hashMemory(Attestation memory a) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(
                TYPEHASH,
                a.covenantId,
                a.sourceId,
                a.evidenceHash,
                a.modelHash,
                a.emissionKgH,
                a.uncertaintyKgH,
                a.detectedAt,
                a.instrumentId,
                a.providerId,
                a.kind,
                a.nonce,
                a.expiry
            )
        );
    }

    /// @notice Domain separator bound to `chainId` and `verifyingContract` (cross-chain / cross-contract replay guard).
    function domainSeparator(uint256 chainId, address verifyingContract) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(DOMAIN_TYPEHASH, keccak256(bytes(NAME)), keccak256(bytes(VERSION)), chainId, verifyingContract)
        );
    }

    /// @notice `keccak256("\x19\x01" ‖ domainSeparator ‖ structHash)`.
    function toTypedDataHash(bytes32 domainSep, bytes32 structHash) internal pure returns (bytes32 digest) {
        assembly ("memory-safe") {
            let ptr := mload(0x40)
            mstore(ptr, hex"1901")
            mstore(add(ptr, 0x02), domainSep)
            mstore(add(ptr, 0x22), structHash)
            digest := keccak256(ptr, 0x42)
        }
    }
}

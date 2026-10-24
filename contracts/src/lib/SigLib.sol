// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

/// @title SigLib
/// @notice Minimal secp256k1 ECDSA recovery (65-byte r‖s‖v, low-s only) and sorted-pair Merkle proof verification
///         (OpenZeppelin-compatible). Returns address(0) / false instead of reverting so callers choose the error.
library SigLib {
    /// @dev secp256k1n / 2. Signatures with s above this are malleable and rejected.
    uint256 private constant HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    function recover(bytes32 digest, bytes calldata sig) internal pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly ("memory-safe") {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 0x20))
            v := byte(0, calldataload(add(sig.offset, 0x40)))
        }
        if (uint256(s) > HALF_N) return address(0);
        if (v != 27 && v != 28) return address(0);
        return ecrecover(digest, v, r, s);
    }

    /// @notice Leaf encoding for the covered-source Merkle set: `keccak256(bytes.concat(keccak256(abi.encode(sourceId))))`
    ///         (double hash, as in OpenZeppelin StandardMerkleTree with leaf type `bytes32`).
    function sourceLeaf(bytes32 sourceId) internal pure returns (bytes32) {
        return keccak256(bytes.concat(keccak256(abi.encode(sourceId))));
    }

    function verifyProof(bytes32[] calldata proof, bytes32 root, bytes32 leaf) internal pure returns (bool) {
        bytes32 h = leaf;
        for (uint256 i; i < proof.length; ++i) {
            bytes32 p = proof[i];
            h = h < p ? keccak256(abi.encode(h, p)) : keccak256(abi.encode(p, h));
        }
        return h == root;
    }
}

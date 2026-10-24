// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

/// @title KeyRegistry
/// @notice Org-scoped key registry with full history. A key address is registered once and never reused; rotation
///         closes the old key's validity interval and opens a new one, revocation closes it. Nothing is deleted.
/// @dev The registry holds no funds. The admin onboards orgs (sets an org controller) and may register keys during
///      setup, then calls `renounceAdmin()`. After that only each org's controller can manage that org's keys.
contract KeyRegistry {
    enum Role {
        NONE,
        OBSERVER,
        SELLER,
        BUYER
    }

    /// @notice 1 = secp256k1 ECDSA (verified on-chain); 2 = hybrid ECDSA + ML-DSA-44 (ML-DSA verified off-chain).
    uint8 public constant ALG_ECDSA = 1;
    uint8 public constant ALG_HYBRID_MLDSA44 = 2;

    struct Key {
        bytes32 orgId;
        Role role;
        uint8 algId;
        uint64 validFrom;
        uint64 validUntil; // 0 = open-ended; otherwise exclusive end
        bytes32 pqPubKeyHash; // 0 unless algId == 2
        address replacedBy; // set on rotation
    }

    address public admin;
    mapping(bytes32 orgId => address) public orgController;
    mapping(address key => Key) private _keys;
    mapping(bytes32 orgId => address[]) private _orgKeys;

    event AdminRenounced(address indexed admin);
    event OrgControllerSet(bytes32 indexed orgId, address indexed controller);
    event KeyRegistered(
        bytes32 indexed orgId, address indexed key, Role role, uint8 algId, bytes32 pqPubKeyHash, uint64 validFrom
    );
    event KeyRotated(bytes32 indexed orgId, address indexed oldKey, address indexed newKey, uint64 timestamp);
    event KeyRevoked(bytes32 indexed orgId, address indexed key, uint64 timestamp);

    error NotAuthorized();
    error InvalidParams();
    error KeyExists(address key);
    error UnknownKey(address key);
    error KeyNotActive(address key);

    constructor(address admin_) {
        if (admin_ == address(0)) revert InvalidParams();
        admin = admin_;
    }

    // ---------------------------------------------------------------- admin (setup only)

    function setOrgController(bytes32 orgId, address controller) external {
        if (msg.sender != admin) revert NotAuthorized();
        if (orgId == bytes32(0) || controller == address(0)) revert InvalidParams();
        orgController[orgId] = controller;
        emit OrgControllerSet(orgId, controller);
    }

    /// @notice Irreversibly gives up the admin role. The registry never holds or moves funds.
    function renounceAdmin() external {
        if (msg.sender != admin) revert NotAuthorized();
        emit AdminRenounced(admin);
        admin = address(0);
    }

    // ---------------------------------------------------------------- key lifecycle

    function registerKey(bytes32 orgId, Role role, address addr, uint8 algId, bytes32 pqPubKeyHash) external {
        _onlyAdminOrController(orgId);
        _register(orgId, role, addr, algId, pqPubKeyHash);
    }

    /// @notice Closes `oldKey` now and registers `newKey` for the same org and role.
    function rotateKey(address oldKey, address newKey, uint8 algId, bytes32 pqPubKeyHash) external {
        Key storage k = _keys[oldKey];
        if (k.role == Role.NONE) revert UnknownKey(oldKey);
        _onlyAdminOrController(k.orgId);
        if (!_validAt(k, block.timestamp)) revert KeyNotActive(oldKey);
        k.validUntil = uint64(block.timestamp);
        k.replacedBy = newKey;
        _register(k.orgId, k.role, newKey, algId, pqPubKeyHash);
        emit KeyRotated(k.orgId, oldKey, newKey, uint64(block.timestamp));
    }

    /// @notice Closes `key` now. Callable by the admin, the org controller, or the key itself (compromise report).
    function revokeKey(address key) external {
        Key storage k = _keys[key];
        if (k.role == Role.NONE) revert UnknownKey(key);
        if (msg.sender != key) _onlyAdminOrController(k.orgId);
        if (!_validAt(k, block.timestamp)) revert KeyNotActive(key);
        k.validUntil = uint64(block.timestamp);
        emit KeyRevoked(k.orgId, key, uint64(block.timestamp));
    }

    // ---------------------------------------------------------------- views

    function isValidAt(address addr, Role role, uint256 ts) external view returns (bool) {
        Key storage k = _keys[addr];
        return k.role == role && role != Role.NONE && _validAt(k, ts);
    }

    function orgOf(address addr) external view returns (bytes32) {
        return _keys[addr].orgId;
    }

    /// @notice One-call lookup used by MethaneCovenant: org, role and validity at `ts`.
    function keyStatus(address addr, uint256 ts) external view returns (bytes32 orgId, Role role, bool valid) {
        Key storage k = _keys[addr];
        return (k.orgId, k.role, k.role != Role.NONE && _validAt(k, ts));
    }

    function getKey(address addr) external view returns (Key memory) {
        return _keys[addr];
    }

    function orgKeys(bytes32 orgId) external view returns (address[] memory) {
        return _orgKeys[orgId];
    }

    // ---------------------------------------------------------------- internal

    function _register(bytes32 orgId, Role role, address addr, uint8 algId, bytes32 pqPubKeyHash) private {
        if (orgId == bytes32(0) || addr == address(0) || role == Role.NONE) revert InvalidParams();
        if (algId == ALG_ECDSA) {
            if (pqPubKeyHash != bytes32(0)) revert InvalidParams();
        } else if (algId == ALG_HYBRID_MLDSA44) {
            if (pqPubKeyHash == bytes32(0)) revert InvalidParams();
        } else {
            revert InvalidParams();
        }
        if (_keys[addr].role != Role.NONE) revert KeyExists(addr);
        _keys[addr] = Key({
            orgId: orgId,
            role: role,
            algId: algId,
            validFrom: uint64(block.timestamp),
            validUntil: 0,
            pqPubKeyHash: pqPubKeyHash,
            replacedBy: address(0)
        });
        _orgKeys[orgId].push(addr);
        emit KeyRegistered(orgId, addr, role, algId, pqPubKeyHash, uint64(block.timestamp));
    }

    function _onlyAdminOrController(bytes32 orgId) private view {
        if (msg.sender == address(0)) revert NotAuthorized();
        if (msg.sender != admin && msg.sender != orgController[orgId]) revert NotAuthorized();
    }

    function _validAt(Key storage k, uint256 ts) private view returns (bool) {
        return ts >= k.validFrom && (k.validUntil == 0 || ts < k.validUntil);
    }
}

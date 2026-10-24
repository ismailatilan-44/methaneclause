// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Corroboration} from "./CleanPass.sol";

/// @title PoolCorroboration — the pending / challenge / effective cycle of a MARS corroboration (spec-v2 §15 A3 audit (3))
/// @notice A stateless external library: it runs by DELEGATECALL on the pool's own storage (the `Pending` record and the
///         `withdrawable` ledger passed in), emits with the pool's event signatures and reverts with the pool's error
///         selectors. Cycle, mirroring a clean pass:
///         - an attested corroboration is PENDING for `cleanWindow`, then anyone makes it effective (`finalize`);
///         - inside that window anyone may challenge it on reason 5 (MARS_PLUME_NEARBY, re-checkable against the pinned
///           snapshot) with exactly `challengerBond`; it is then CHALLENGED;
///         - k orgs answering with the same snapshot and window before `resolveBy` make it effective at once and the
///           failed challenger's bond goes to the covenant seller;
///         - unanswered by `resolveBy`, `finalize` drops it and refunds the challenger: nobody is slashed (D11).
library PoolCorroboration {
    uint8 internal constant NONE = 0;
    uint8 internal constant PENDING = 1;
    uint8 internal constant CHALLENGED = 2;
    uint8 internal constant REASON_MARS_PLUME_NEARBY = 5;

    /// @dev One per claim, in the pool's storage.
    struct Pending {
        bytes32 marsSnapshotHash;
        uint64 windowFrom;
        uint64 windowTo;
        uint64 effectiveAt; // attestation time + cleanWindow
        uint64 resolveBy; // challenge time + resolveWindow
        address challenger;
        uint96 challengerBond;
        uint8 state;
    }

    // Same signatures (hence selectors / topics) as IncentivePool's.
    error InvalidState();
    error WindowOpen();
    error WindowClosed();
    error InvalidReason();
    error BadBond();
    error AttestationMismatch(uint256 index);

    event ClaimCorroborated(bytes32 indexed claimId, bytes32 marsSnapshotHash, uint64 windowFrom, uint64 windowTo);
    event Credited(address indexed account, uint256 amount);
    event CorroborationPending(bytes32 indexed claimId, bytes32 marsSnapshotHash, uint64 effectiveAt);
    event CorroborationChallenged(bytes32 indexed claimId, address indexed challenger, uint64 resolveBy);

    /// @notice After the pool verified a k-org corroboration batch whose first attestation is `a0`. With nothing
    ///         pending it starts a PENDING one (returns 0). With a CHALLENGED one it is the answer: same snapshot and
    ///         window, before `resolveBy` -> effective now, challenger's bond to `seller` (returns `windowTo`).
    function record(
        Pending storage pc,
        bytes32 claimId,
        Corroboration calldata a0,
        uint32 cleanWindow,
        address seller,
        mapping(address => uint256) storage withdrawable
    ) external returns (uint64 effectiveTo) {
        uint8 state = pc.state;
        if (state == NONE) {
            pc.marsSnapshotHash = a0.marsSnapshotHash;
            pc.windowFrom = a0.windowFrom;
            pc.windowTo = a0.windowTo;
            uint64 effectiveAt = uint64(block.timestamp) + cleanWindow;
            pc.effectiveAt = effectiveAt;
            pc.state = PENDING;
            emit CorroborationPending(claimId, a0.marsSnapshotHash, effectiveAt);
            return 0;
        }
        if (state != CHALLENGED) revert InvalidState(); // one pending corroboration at a time
        if (block.timestamp >= pc.resolveBy) revert WindowClosed();
        if (a0.marsSnapshotHash != pc.marsSnapshotHash || a0.windowFrom != pc.windowFrom || a0.windowTo != pc.windowTo) revert AttestationMismatch(0);
        _credit(withdrawable, seller, pc.challengerBond);
        return _effective(pc, claimId);
    }

    /// @notice Reason 5 only, exactly `bond`, while PENDING and before it takes effect.
    function challenge(
        Pending storage pc,
        bytes32 claimId,
        uint8 reason,
        uint256 value,
        uint96 bond,
        uint32 resolveWindow
    ) external {
        if (pc.state != PENDING) revert InvalidState();
        if (block.timestamp >= pc.effectiveAt) revert WindowClosed();
        if (reason != REASON_MARS_PLUME_NEARBY) revert InvalidReason();
        if (value != bond) revert BadBond();
        pc.state = CHALLENGED;
        pc.challenger = msg.sender; // DELEGATECALL: the pool's caller
        pc.challengerBond = bond;
        uint64 resolveBy = uint64(block.timestamp) + resolveWindow;
        pc.resolveBy = resolveBy;
        emit CorroborationChallenged(claimId, msg.sender, resolveBy);
    }

    /// @notice Permissionless, O(1). PENDING after `effectiveAt` -> effective (returns `windowTo`); CHALLENGED after
    ///         `resolveBy` -> dropped, challenger refunded (returns 0).
    function finalize(Pending storage pc, bytes32 claimId, mapping(address => uint256) storage withdrawable)
        external
        returns (uint64 effectiveTo)
    {
        uint8 state = pc.state;
        if (state == PENDING) {
            if (block.timestamp < pc.effectiveAt) revert WindowOpen();
            return _effective(pc, claimId);
        }
        if (state != CHALLENGED) revert InvalidState();
        if (block.timestamp < pc.resolveBy) revert WindowOpen();
        address challenger = pc.challenger;
        uint256 refund = pc.challengerBond;
        _clear(pc);
        _credit(withdrawable, challenger, refund);
        return 0;
    }

    function _effective(Pending storage pc, bytes32 claimId) private returns (uint64 windowTo) {
        windowTo = pc.windowTo;
        emit ClaimCorroborated(claimId, pc.marsSnapshotHash, pc.windowFrom, windowTo);
        _clear(pc);
    }

    /// @dev `delete` cannot take a storage-pointer parameter; clear field by field (4 slots).
    function _clear(Pending storage pc) private {
        pc.marsSnapshotHash = bytes32(0);
        pc.windowFrom = 0;
        pc.windowTo = 0;
        pc.effectiveAt = 0;
        pc.resolveBy = 0;
        pc.challenger = address(0);
        pc.challengerBond = 0;
        pc.state = NONE;
    }

    function _credit(mapping(address => uint256) storage withdrawable, address to, uint256 amount) private {
        if (amount == 0) return;
        withdrawable[to] += amount;
        emit Credited(to, amount);
    }
}

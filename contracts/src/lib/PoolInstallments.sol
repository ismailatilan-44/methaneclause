// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IncentivePool} from "../IncentivePool.sol";

/// @title PoolInstallments — scheduling of a claim's refund installments, as an external (linked) library
/// @notice Moved out of IncentivePool to keep it under the EIP-170 code-size limit (spec-v2 §15 A2 audit (4)). Runs by
///         DELEGATECALL on the pool's own storage (the `Book` and the pool's claims, pool and params passed in), emits
///         with the pool's event signatures. Rules (spec-v2 §3 "Installments", §15 A1-A3 audits):
///         - an installment is created when the claim's count reaches its threshold (and, if required, two instruments);
///           amount = cap x shareBp / 10000;
///         - ONCHAIN, it is PENDING with its reserve only if the unreserved balance covers it and no older installment
///           of the pool is still waiting; otherwise UNFUNDED, queued first in, first out for the next `fund`;
///         - claimableAt = now + holdWindow; the final one not before since + cureTerm + relapseGrace;
///         - the final one is never scheduled if that cure end cannot fit `end + publicationLag` (R27, `FinalOutOfTerm`
///           once) and only once an effective MARS corroboration reaches its triggering pass (R28);
///         - a relapse voids what is PENDING or UNFUNDED and frees its reserve; RELEASED stays released (D1).
library PoolInstallments {
    uint256 internal constant MAX_SCHEDULE_PER_FUND = 8;

    /// @dev Per-claim bookkeeping (the frozen `Claim` struct is not extended).
    struct ClaimBook {
        uint256[3] ids; // installment ids by index; 0 = not created
        uint8 open; // PENDING or UNFUNDED: each keeps the claim from closing
        bool finalOutOfTerm; // R27
        uint64 latestCountedSceneAt; // a corroboration must reach at least this scene
        uint64 finalTriggerSceneAt; // sceneAt of the pass that met the final's count / instrument rule (0 = not yet)
    }

    /// @dev Everything about installments, in the pool's storage.
    struct Book {
        uint256 count;
        mapping(uint256 installmentId => IncentivePool.Installment) installments;
        mapping(bytes32 claimId => ClaimBook) claims;
        mapping(bytes32 covenantId => uint256[]) unfunded; // FIFO of UNFUNDED installment ids per pool
        mapping(bytes32 covenantId => uint256) unfundedHead;
    }

    // Same signatures (hence topics) as IncentivePool's events.
    event InstallmentScheduled(
        bytes32 indexed claimId, uint256 indexed installmentId, uint8 index, uint256 amount, uint64 claimableAt
    );
    event InstallmentUnfunded(bytes32 indexed claimId, uint256 indexed installmentId, uint256 amount);
    event FinalOutOfTerm(bytes32 indexed claimId);

    /// @notice After a pass of `claimId` was counted (its scene acquired at `sceneAt`): create every installment whose
    ///         rule the count now meets (thresholds strictly increase, so the first unmet one ends the scan).
    function afterCount(
        Book storage b,
        mapping(bytes32 => IncentivePool.Claim) storage claims,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a,
        bytes32 claimId,
        uint64 sceneAt
    ) external {
        IncentivePool.Claim storage c = claims[claimId];
        ClaimBook storage x = b.claims[claimId];
        if (sceneAt > x.latestCountedSceneAt) x.latestCountedSceneAt = sceneAt;
        for (uint256 i; i < 3; ++i) {
            if (x.ids[i] != 0) continue;
            IncentivePool.InstallmentRule storage r = p.installments[i];
            if (c.counted < r.afterPasses) break;
            if (r.twoInstruments && _popcount(c.instrumentsSeen) < 2) continue;
            if (r.isFinal) _tryFinal(b, claims, pl, p, a, claimId, sceneAt);
            else _create(b, claims, pl, p, a, claimId, uint8(i));
        }
    }

    /// @notice After a corroboration of `claimId` became effective: schedule the final share if it is now covered.
    function afterCorroboration(
        Book storage b,
        mapping(bytes32 => IncentivePool.Claim) storage claims,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a,
        bytes32 claimId
    ) external {
        ClaimBook storage x = b.claims[claimId];
        if (claims[claimId].state != IncentivePool.ClaimState.OPEN || x.finalTriggerSceneAt == 0 || x.ids[2] != 0) {
            return;
        }
        _tryFinal(b, claims, pl, p, a, claimId, 0);
    }

    /// @notice On a top-up: schedule queued UNFUNDED installments in order while the unreserved balance covers them;
    ///         skip entries a relapse voided; at most MAX_SCHEDULE_PER_FUND per call (another top-up continues).
    function scheduleUnfunded(
        Book storage b,
        mapping(bytes32 => IncentivePool.Claim) storage claims,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a,
        bytes32 covenantId
    ) external {
        _schedule(b, claims, pl, p, a, covenantId);
    }

    /// @notice A relapse cancelled `claimId`: void its PENDING / UNFUNDED installments and free their reserve; RELEASED
    ///         ones stay released (D1). O(1): at most three.
    function cancel(Book storage b, IncentivePool.Claim storage c, IncentivePool.Pool storage pl, bytes32 claimId)
        external
        returns (uint256 releasedReserve)
    {
        ClaimBook storage x = b.claims[claimId];
        bool onchain = pl.mode == IncentivePool.Mode.ONCHAIN;
        for (uint256 i; i < 3; ++i) {
            uint256 id = x.ids[i];
            if (id == 0) continue;
            IncentivePool.Installment storage it = b.installments[id];
            IncentivePool.InstState st = it.state;
            if (st == IncentivePool.InstState.PENDING) {
                uint256 amount = it.amount;
                c.reserved -= amount;
                if (onchain) pl.reserved -= amount;
                releasedReserve += amount;
            } else if (st != IncentivePool.InstState.UNFUNDED) {
                continue; // RELEASED stays released (D1)
            }
            it.state = IncentivePool.InstState.VOID;
            x.open -= 1;
        }
    }

    // ------------------------------------------------------------------ internal

    function _tryFinal(
        Book storage b,
        mapping(bytes32 => IncentivePool.Claim) storage claims,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a,
        bytes32 claimId,
        uint64 sceneAt
    ) private {
        ClaimBook storage x = b.claims[claimId];
        if (x.finalOutOfTerm) return;
        IncentivePool.Claim storage c = claims[claimId];
        if (x.finalTriggerSceneAt == 0) {
            if (uint256(c.since) + p.cureTerm + a.relapseGrace > uint256(a.end) + p.publicationLag) {
                x.finalOutOfTerm = true;
                emit FinalOutOfTerm(claimId);
                return;
            }
            x.finalTriggerSceneAt = sceneAt;
        }
        if (c.corroboratedTo < x.finalTriggerSceneAt) return;
        _create(b, claims, pl, p, a, claimId, 2);
    }

    function _create(
        Book storage b,
        mapping(bytes32 => IncentivePool.Claim) storage claims,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a,
        bytes32 claimId,
        uint8 index
    ) private {
        IncentivePool.Claim storage c = claims[claimId];
        bytes32 covenantId = c.covenantId;
        uint256 id = ++b.count;
        IncentivePool.Installment storage it = b.installments[id];
        it.claimId = claimId;
        it.index = index;
        uint256 amount = c.cap * p.installments[index].shareBp / 10_000;
        it.amount = amount;
        ClaimBook storage x = b.claims[claimId];
        x.ids[index] = id;
        x.open += 1;
        if (pl.mode == IncentivePool.Mode.ONCHAIN) {
            // FIFO: older UNFUNDED installments are served first; a new one queues behind any that still wait
            _schedule(b, claims, pl, p, a, covenantId);
            if (b.unfundedHead[covenantId] < b.unfunded[covenantId].length || pl.balance - pl.reserved < amount) {
                it.state = IncentivePool.InstState.UNFUNDED;
                b.unfunded[covenantId].push(id);
                emit InstallmentUnfunded(claimId, id, amount);
                return;
            }
        }
        _activate(it, id, c, pl, p, a);
    }

    function _schedule(
        Book storage b,
        mapping(bytes32 => IncentivePool.Claim) storage claims,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a,
        bytes32 covenantId
    ) private {
        uint256[] storage queue = b.unfunded[covenantId];
        uint256 head = b.unfundedHead[covenantId];
        for (uint256 n = 0; n < MAX_SCHEDULE_PER_FUND && head < queue.length; ++n) {
            uint256 id = queue[head];
            IncentivePool.Installment storage it = b.installments[id];
            if (it.state == IncentivePool.InstState.UNFUNDED) {
                if (pl.balance - pl.reserved < it.amount) break;
                _activate(it, id, claims[it.claimId], pl, p, a);
            }
            ++head;
        }
        b.unfundedHead[covenantId] = head;
    }

    /// @dev PENDING: reserve (ETH in ONCHAIN; units on the claim only in VERDICT_ONLY) and claimableAt.
    function _activate(
        IncentivePool.Installment storage it,
        uint256 id,
        IncentivePool.Claim storage c,
        IncentivePool.Pool storage pl,
        IncentivePool.PoolParams storage p,
        IncentivePool.PoolAux storage a
    ) private {
        uint256 amount = it.amount;
        uint64 claimableAt = uint64(block.timestamp) + p.holdWindow;
        if (p.installments[it.index].isFinal) {
            uint64 cureEnd = c.since + p.cureTerm + a.relapseGrace;
            if (cureEnd > claimableAt) claimableAt = cureEnd;
        }
        it.state = IncentivePool.InstState.PENDING;
        it.claimableAt = claimableAt;
        c.reserved += amount;
        if (pl.mode == IncentivePool.Mode.ONCHAIN) pl.reserved += amount;
        emit InstallmentScheduled(it.claimId, id, it.index, amount, claimableAt);
    }

    function _popcount(uint8 bits) private pure returns (uint256 n) {
        while (bits != 0) {
            n += bits & 1;
            bits >>= 1;
        }
    }
}

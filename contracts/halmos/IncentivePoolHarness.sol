// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IncentivePool} from "../src/IncentivePool.sol";
import {MethaneCovenant} from "../src/MethaneCovenant.sol";
import {Corroboration} from "../src/lib/CleanPass.sol";
import {PoolInstallments} from "../src/lib/PoolInstallments.sol";
import {PoolCorroboration} from "../src/lib/PoolCorroboration.sol";

/// @title IncentivePoolHarness — test-only subclass for the Halmos proofs (never deployed)
/// @notice The pool's signed entry points need a k-of-n quorum of observer signatures, and opening or cancelling a
///         claim needs a confirmed core episode, which itself needs a signed quorum in the core. Symbolic execution of
///         those signature checks is out of scope here (they stay with the unit tests and the stateful fuzz). This
///         subclass adds stand-ins that do exactly what the real function does AFTER its signature / core-episode
///         checks, with the same pool-side preconditions or weaker ones:
///         - `h_openClaim`: `openClaim` without the core read (`cap` and `since` are free inputs instead of the
///           episode's rebate and link-window end);
///         - `h_attestPass`: `attestCleanPass` without the quorum and the scene rules (any scene, any instrument);
///         - `h_attestCorroboration`: `attestCorroboration` without the quorum and the window rule against `since`;
///         - `h_relapse`: `relapse` without the core's relapse-episode check (any OPEN claim may be cancelled).
///         Everything else (fund, countPass, challengePass, finalizePass, voidPass, challengeCorroboration,
///         finalizeCorroboration, release, closeClaim, sponsorWithdraw, withdraw, all five libraries) is the real code.
///         Weaker preconditions only add behaviours, so a safety property proved over this harness holds for the pool.
contract IncentivePoolHarness is IncentivePool {
    constructor(MethaneCovenant covenant_) IncentivePool(covenant_) {}

    function h_openClaim(bytes32 covenantId, uint256 episodeId, uint256 cap, uint64 since)
        external
        returns (bytes32 claimId)
    {
        PoolState s = _poolState(covenantId);
        if (s == PoolState.NONE) revert WrongCovenant();
        if (s != PoolState.OPEN) revert PoolClosed();
        claimId = keccak256(abi.encode(address(this), episodeId));
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.NONE) revert ClaimExists();
        if (cap == 0) revert ZeroCap();
        PoolAux storage a = _aux[covenantId];
        c.episodeId = episodeId;
        c.covenantId = covenantId;
        c.since = since;
        c.cap = cap;
        c.state = ClaimState.OPEN;
        a.openClaims += 1;
    }

    function h_attestPass(bytes32 claimId, bytes32 sceneId, uint64 sceneAt, uint8 instrumentId, bytes32[] calldata orgs)
        external
        payable
        returns (uint256 passId)
    {
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        bytes32 covenantId = c.covenantId;
        if (_poolState(covenantId) != PoolState.OPEN) revert PoolClosed();
        PoolParams storage p = _params[covenantId];
        if (msg.value != p.passBond) revert BadBond();
        if (c.pending >= MAX_PENDING) revert TooManyPending();
        if (instrumentId > 7) revert BadParams(); // the real path accepts 1..7 (PoolVerify.checkScene); 0..7 here
        if (_seenScene[claimId][sceneId]) revert DuplicateScene();
        passId = ++_passCount;
        Pass storage ps = _passes[passId];
        ps.claimId = claimId;
        ps.sceneId = sceneId;
        ps.sceneAt = sceneAt;
        ps.instrumentId = instrumentId;
        ps.submitter = msg.sender;
        ps.bond = p.passBond;
        ps.orgs = orgs;
        ps.window = uint64(block.timestamp) + p.cleanWindow;
        ps.state = PassState.PENDING;
        _seenScene[claimId][sceneId] = true;
        c.pending += 1;
    }

    function h_attestCorroboration(Corroboration calldata a0) external {
        bytes32 claimId = a0.claimId;
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        if (a0.windowTo > block.timestamp) revert SceneOutOfRange();
        PoolCorroboration.Pending storage pc = _corroborations[claimId];
        if (pc.state == PoolCorroboration.PENDING) revert InvalidState();
        if (pc.state == PoolCorroboration.NONE) {
            if (a0.windowTo < _book.claims[claimId].latestCountedSceneAt) revert NotCorroborated();
            if (a0.windowTo <= c.corroboratedTo) revert InvalidState();
        }
        uint64 effectiveTo = PoolCorroboration.record(
            pc, claimId, a0, _params[c.covenantId].cleanWindow, _aux[c.covenantId].seller, withdrawable
        );
        if (effectiveTo != 0) {
            // as the private `_corroborated`
            c.corroboratedTo = effectiveTo;
            bytes32 covenantId = c.covenantId;
            PoolInstallments.afterCorroboration(
                _book, _claims, _pools[covenantId], _params[covenantId], _aux[covenantId], claimId
            );
        }
    }

    function h_relapse(bytes32 claimId) external {
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        c.state = ClaimState.CANCELLED;
        bytes32 covenantId = c.covenantId;
        _aux[covenantId].openClaims -= 1;
        PoolInstallments.cancel(_book, c, _pools[covenantId], claimId);
    }

    /// @dev For the arithmetic proof only: sets the count and instrument set a run of passes would have reached and
    ///      runs the real scheduling (`PoolInstallments.afterCount`), as `_count` does after a counted pass.
    function h_countTo(bytes32 claimId, uint32 counted, uint8 instrumentsSeen, uint64 sceneAt) external {
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        c.counted = counted;
        c.instrumentsSeen = instrumentsSeen;
        bytes32 covenantId = c.covenantId;
        PoolInstallments.afterCount(
            _book, _claims, _pools[covenantId], _params[covenantId], _aux[covenantId], claimId, sceneAt
        );
    }

    function h_installmentCount() external view returns (uint256) {
        return _book.count;
    }
}

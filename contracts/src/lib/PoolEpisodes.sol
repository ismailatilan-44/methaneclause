// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {MethaneCovenant} from "../MethaneCovenant.sol";

/// @title PoolEpisodes — the IncentivePool's reads of core episodes, as an external (linked) library
/// @notice Moved out of IncentivePool to keep it under the EIP-170 code-size limit (spec-v2 §15 A2 audit (4)). Pure
///         reads of the unchanged core (`getEpisode`, `latestEpisode`); reverts carry the pool's error selectors.
library PoolEpisodes {
    /// @dev What a claim needs from its CONFIRMED episode.
    struct ClaimBasis {
        bytes32 covenantId;
        bytes32 sourceId;
        uint64 detectedAt;
        uint32 emissionKgH;
        uint8 tier;
        uint128 rebate;
    }

    // Same signatures (hence selectors) as IncentivePool's errors.
    error NotConfirmed(uint256 episodeId);
    error SourceUnderReview(uint256 episodeId);
    error RelapsePending(uint256 episodeId);
    error NotARelapse(uint256 episodeId);

    /// @notice The basis of a refund claim: the episode must be CONFIRMED (the core never leaves CONFIRMED).
    function claimBasis(MethaneCovenant covenant, uint256 episodeId) external view returns (ClaimBasis memory b) {
        MethaneCovenant.Episode memory e = covenant.getEpisode(episodeId);
        if (e.state != MethaneCovenant.State.CONFIRMED) revert NotConfirmed(episodeId);
        b = ClaimBasis(e.covenantId, e.sourceId, e.detectedAt, e.emissionKgH, e.tier, e.rebate);
    }

    /// @notice P9 on the unchanged core: `latestEpisode` is the newest non-LINKED proposal at the source. If it is not
    ///         the claim's own episode and was detected after `since`: live -> SourceUnderReview, CONFIRMED / EXPIRED
    ///         -> RelapsePending (anyone may call `relapse`), VOID -> ignored. Residual R23: a newer proposal overwrites
    ///         the slot; `relapse` names the episode directly, so the watcher can still cancel.
    function requireNoLaterDetection(
        MethaneCovenant covenant,
        bytes32 covenantId,
        bytes32 sourceId,
        uint256 claimEpisodeId,
        uint64 since
    ) external view {
        uint256 id = covenant.latestEpisode(covenantId, sourceId);
        if (id == 0 || id == claimEpisodeId) return;
        MethaneCovenant.Episode memory e = covenant.getEpisode(id);
        if (e.detectedAt <= since) return;
        MethaneCovenant.State s = e.state;
        if (_live(s)) revert SourceUnderReview(id);
        if (s == MethaneCovenant.State.CONFIRMED || s == MethaneCovenant.State.EXPIRED) revert RelapsePending(id);
    }

    /// @notice A relapse is a core episode at the claim's (covenant, source), detected after `since`, CONFIRMED or
    ///         EXPIRED. A live one may still end VOID (SourceUnderReview: wait); LINKED, VOID, unknown, elsewhere or
    ///         inside the episode's own window is not a relapse.
    function requireRelapse(
        MethaneCovenant covenant,
        uint256 episodeId,
        bytes32 covenantId,
        bytes32 sourceId,
        uint64 since
    ) external view {
        MethaneCovenant.Episode memory e = covenant.getEpisode(episodeId);
        if (e.covenantId != covenantId || e.sourceId != sourceId || e.detectedAt <= since) {
            revert NotARelapse(episodeId);
        }
        MethaneCovenant.State s = e.state;
        if (_live(s)) revert SourceUnderReview(episodeId);
        if (s != MethaneCovenant.State.CONFIRMED && s != MethaneCovenant.State.EXPIRED) revert NotARelapse(episodeId);
    }

    function _live(MethaneCovenant.State s) private pure returns (bool) {
        return s == MethaneCovenant.State.PROPOSED || s == MethaneCovenant.State.CHALLENGED
            || s == MethaneCovenant.State.CONTESTED;
    }
}

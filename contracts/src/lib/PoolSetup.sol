// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {MethaneCovenant} from "../MethaneCovenant.sol";
import {IncentivePool} from "../IncentivePool.sol";

/// @title PoolSetup — the checks of a pool's opening `fund`, as an external (linked) library
/// @notice Moved out of IncentivePool to keep it under the EIP-170 code-size limit (spec-v2 §15 A2 audit (4)). Runs by
///         DELEGATECALL from `IncentivePool.fund`, so `msg.sender` is the opener; it reads the core and returns the
///         immutable covenant facts the pool caches, or reverts with the pool's own error selectors. It writes nothing.
library PoolSetup {
    /// @dev The covenant facts a pool caches when it opens (all immutable in the core once the covenant exists).
    struct Facts {
        uint64 end;
        uint64 linkWindow;
        uint64 closingAt; // end + publicationLag + cleanWindow
        uint8 k;
        address seller;
        bytes32 sellerOrg;
        bytes32 buyerOrg;
        uint64 relapseGrace; // publicationLag + challengeWindow + contestWindow
    }

    // Same signatures (hence selectors) as IncentivePool's errors.
    error PoolNotOpenedByParty();
    error BadParams();
    error PoolClosed();

    /// @notice Only the covenant's buyer or seller opens its pool (an unknown covenant has no party); `p` must pass the
    ///         spec-v2 §3 rules plus the fail-closed shape rules; ONCHAIN needs ETH, VERDICT_ONLY takes none; a pool
    ///         never opens at or after its closing time.
    function openingFacts(
        MethaneCovenant covenant,
        bytes32 covenantId,
        bytes32 policyHash,
        bool onchain,
        uint256 value,
        IncentivePool.PoolParams calldata p
    ) external view returns (Facts memory f) {
        MethaneCovenant.Covenant memory cv = covenant.getCovenant(covenantId);
        if (msg.sender != cv.buyer && msg.sender != cv.seller) revert PoolNotOpenedByParty();
        if (policyHash == bytes32(0) || !validParams(p, cv.challengeWindow, cv.contestWindow)) revert BadParams();
        if (onchain ? value == 0 : value != 0) revert BadParams();
        f.closingAt = cv.end + p.publicationLag + p.cleanWindow;
        if (block.timestamp >= f.closingAt) revert PoolClosed();
        f.end = cv.end;
        f.linkWindow = cv.linkWindow;
        f.k = cv.k;
        f.seller = cv.seller;
        f.sellerOrg = cv.sellerOrg;
        f.buyerOrg = cv.buyerOrg;
        f.relapseGrace = p.publicationLag + cv.challengeWindow + cv.contestWindow;
    }

    /// @notice spec-v2 §3 `fund` validation (holdWindow covers publication + challenge + contest; resolveWindow >=
    ///         cleanWindow; thresholds strictly increasing; shares sum to 10000; u(N) non-increasing) plus fail-closed
    ///         shape rules: non-zero bonds and clean window, every share non-zero, exactly the last installment final,
    ///         and the final one requires two instruments.
    function validParams(IncentivePool.PoolParams calldata p, uint64 challengeWindow, uint64 contestWindow)
        public
        pure
        returns (bool)
    {
        if (p.cleanWindow == 0 || p.resolveWindow < p.cleanWindow) return false;
        if (uint256(p.holdWindow) < uint256(p.publicationLag) + challengeWindow + contestWindow) return false;
        if (p.passBond == 0 || p.challengerBond == 0) return false;
        uint256 shares = 0;
        uint256 previous = 0;
        for (uint256 i; i < 3; ++i) {
            IncentivePool.InstallmentRule calldata r = p.installments[i];
            if (r.afterPasses <= previous || r.shareBp == 0 || r.isFinal != (i == 2)) return false;
            if (r.isFinal && !r.twoInstruments) return false; // R21: the final share needs two instruments on chain
            previous = r.afterPasses;
            shares += r.shareBp;
        }
        if (shares != 10_000 || p.upperBoundBp[0] > 10_000) return false;
        for (uint256 i = 1; i < 32; ++i) {
            if (p.upperBoundBp[i] > p.upperBoundBp[i - 1]) return false;
        }
        return true;
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IncentivePool} from "../src/IncentivePool.sol";
import {Corroboration, KIND_CORROBORATE} from "../src/lib/CleanPass.sol";
import {HalmosBase} from "./HalmosBase.sol";

/// @notice Halmos: installment arithmetic, code-path half (spec-v2 §3 "Installments"). For every share vector and
///         threshold vector the real `PoolSetup.validParams` accepts (three non-zero shares summing to 10,000 bp,
///         thresholds strictly increasing, final last with two instruments), every claim cap below 2^128 and every
///         funding, the real `PoolInstallments` creates exactly three installments of `cap x share / 10,000` each,
///         PENDING or UNFUNDED, and the claim's reserve equals the pool's. That those three amounts sum to at most the
///         cap and lose at most 2 wei is the rounding lemma: Halmos's 256-bit query for it times out
///         (`RoundingLemma.t.sol`), so it is proved over integers by `rounding_lemma.py` (Z3).
contract InstallmentArithmeticHalmos is HalmosBase {
    uint16[3] internal shares;

    function _open(uint8[3] memory thresholds, uint96 funded) internal {
        IncentivePool.PoolParams memory p = _params();
        for (uint256 i; i < 3; ++i) {
            p.installments[i].shareBp = shares[i];
            p.installments[i].afterPasses = thresholds[i];
        }
        vm.assume(funded > 0);
        vm.deal(BUYER, funded);
        vm.prank(BUYER);
        pool.fund{value: funded}(cid, POLICY, IncentivePool.Mode.ONCHAIN, p);
    }

    /// @dev Count reaches every threshold with two instruments; an effective corroboration covers the trigger.
    function _scheduleAll(uint128 cap, uint8 lastThreshold) internal returns (bytes32 claimId) {
        claimId = pool.h_openClaim(cid, 1, cap, uint64(T0)); // reverts on cap == 0
        vm.warp(T0 + 20 days);
        pool.h_countTo(claimId, lastThreshold, 0x03, uint64(T0 + 10 days));
        Corroboration memory a0;
        a0.claimId = claimId;
        a0.windowFrom = uint64(T0);
        a0.windowTo = uint64(T0 + 15 days);
        a0.kind = KIND_CORROBORATE;
        pool.h_attestCorroboration(a0);
        vm.warp(T0 + 22 days);
        pool.finalizeCorroboration(claimId); // the final share is scheduled now
    }

    /// The code-path half: three installments, each exactly `cap x share / 10,000` as created by the real library,
    /// PENDING or UNFUNDED, and the claim's reserve equals the pool's (one claim).
    function check_installments_amountsAsFormula(
        uint128 cap,
        uint16 s0,
        uint16 s1,
        uint16 s2,
        uint8 t0,
        uint8 t1,
        uint8 t2,
        uint96 funded
    ) public {
        shares = [s0, s1, s2];
        _open([t0, t1, t2], funded);
        bytes32 claimId = _scheduleAll(cap, t2);
        assert(pool.h_installmentCount() == 3);
        uint256 reserved;
        for (uint256 id = 1; id <= 3; ++id) {
            IncentivePool.Installment memory it = pool.getInstallment(id);
            assert(it.index == id - 1);
            assert(it.amount == uint256(cap) * shares[it.index] / 10_000);
            assert(it.state == IncentivePool.InstState.PENDING || it.state == IncentivePool.InstState.UNFUNDED);
            if (it.state == IncentivePool.InstState.PENDING) reserved += it.amount;
        }
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assert(c.reserved == reserved && c.paid == 0);
        (IncentivePool.Pool memory pl,) = pool.getPool(cid);
        assert(pl.reserved == reserved);
    }
}

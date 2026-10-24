// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IncentivePool} from "../src/IncentivePool.sol";
import {PoolActions, PoolHalmosSetup} from "./PoolProperties.t.sol";

/// @notice Halmos: P1 and P3 along short lifecycles. Each starts from the shared state (0.2 ETH pool [SIM], two OPEN
///         claims of 0.6 and 0.5 ETH) with a fixed opening (named per scenario), then runs steps whose arguments are
///         all symbolic and each of which is optional (a step that reverts is skipped, as a failed transaction
///         would be). P1 and P3 are asserted after every step.
contract PoolScenarioHalmos is PoolHalmosSetup {
    function _try(bytes memory call) internal {
        (bool ok,) = address(actions).call(call);
        ok; // a reverted step is a skipped step
        _p1();
        _p3();
    }

    /// Opening: both claims counted to 10 with two instruments (four non-final installments, 0.66 ETH against a
    /// 0.2 ETH pool: one PENDING, the others UNFUNDED). Then, after any gaps: release installment 1, relapse either claim,
    /// release installments 2, 3 and 4 (each step optional).
    function check_P1_P3_releaseRelapse(uint32[2] calldata dts, bool second) public {
        actions.countTo(false, 10, 0x03);
        actions.countTo(true, 10, 0x03);
        _p1();
        _p3();
        _try(abi.encodeCall(PoolActions.release, (1, dts[0])));
        _try(abi.encodeCall(PoolActions.relapse, (second)));
        _try(abi.encodeCall(PoolActions.release, (2, dts[1])));
        _try(abi.encodeCall(PoolActions.release, (3, 0)));
        _try(abi.encodeCall(PoolActions.release, (4, 0)));
    }

    /// Opening: both claims counted to 10 with two instruments. Then: a top-up of any size (the UNFUNDED queue is
    /// served first in, first out), a release after any gap.
    function check_P1_P3_topUpQueue(uint96 topUp, uint8 id, uint32 dt) public {
        actions.countTo(false, 10, 0x03);
        actions.countTo(true, 10, 0x03);
        _try(abi.encodeCall(PoolActions.topUp, (topUp)));
        _try(abi.encodeCall(PoolActions.release, (id, dt)));
    }

    /// Opening: the first claim counted to 10 with two instruments. Then: a MARS corroboration of any window after
    /// any gap, its challenge, its finalization after any gap (the final share may be scheduled), a release.
    function check_P1_P3_corroboratedFinal(uint64 windowTo, uint32[3] calldata dts, uint8 id) public {
        actions.countTo(false, 10, 0x03);
        _try(abi.encodeCall(PoolActions.corroborate, (false, windowTo, dts[0])));
        _try(abi.encodeCall(PoolActions.challengeCorroboration, (false)));
        _try(abi.encodeCall(PoolActions.finalizeCorroboration, (false, dts[1])));
        _try(abi.encodeCall(PoolActions.release, (id, dts[2])));
    }

    /// No opening. Then: one pass counted through the real path (observer fee from the unreserved balance), a relapse
    /// of either claim, closing both claims after any gap, the sponsors' withdrawal.
    function check_P1_P3_closeAndWithdraw(
        uint8 instrumentId,
        bytes32 sceneId,
        bool second,
        uint32[2] calldata dts,
        bool opener
    ) public {
        _try(abi.encodeCall(PoolActions.countPass, (false, instrumentId, sceneId)));
        _try(abi.encodeCall(PoolActions.relapse, (second)));
        _try(abi.encodeCall(PoolActions.closeClaim, (false, dts[0])));
        _try(abi.encodeCall(PoolActions.closeClaim, (true, 0)));
        _try(abi.encodeCall(PoolActions.sponsorWithdraw, (opener, dts[1])));
    }

    // ------------------------------------------------------------------ reachability canaries (expected to FAIL)

    /// The lifecycles above reach a released share, a relapse over UNFUNDED installments and a sponsor withdrawal.
    function check_canary_released(uint32 dt) public {
        actions.countTo(false, 10, 0x03);
        actions.release(1, dt);
        assert(pool.getClaim(actions.claims(0)).paid == 0);
    }

    function check_canary_relapseOverUnfunded(bool second) public {
        actions.countTo(false, 10, 0x03);
        actions.countTo(true, 10, 0x03);
        actions.relapse(second);
        IncentivePool.Installment memory it = pool.getInstallment(second ? 4 : 2);
        assert(it.state != IncentivePool.InstState.VOID);
    }

    function check_canary_sponsorWithdrew(uint32 dt) public {
        actions.relapse(false);
        actions.relapse(true);
        actions.sponsorWithdraw(true, dt);
        (IncentivePool.Pool memory pl,) = pool.getPool(cid);
        assert(pl.balance == 0.2 ether);
    }
}

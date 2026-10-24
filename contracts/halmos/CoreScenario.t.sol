// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {CoreActions, CoreHalmosSetup} from "./CoreI4.t.sol";

/// @notice Halmos: the core's I4 (bond conservation) along short episode lifecycles on the unchanged core, every
///         argument symbolic, signatures over-approximated (CoreActions); the opening steps must succeed, every later
///         step is optional (a step that reverts is skipped). I4 is asserted after every step.
contract CoreScenarioHalmos is CoreHalmosSetup {
    function _try(bytes memory call) internal {
        (bool ok,) = address(actions).call(call);
        ok; // a reverted step is a skipped step
        _i4();
    }

    /// Unchallenged detection: proposed (must succeed), then optionally finalized after any gap and withdrawn.
    function check_I4_rebatePaid(uint32 kgh, uint32 ago, bytes calldata s0, bytes calldata s1, uint32 dt, uint8 who)
        public
    {
        actions.propose(kgh, ago, s0, s1);
        _i4();
        actions.warp(dt);
        _try(abi.encodeCall(CoreActions.finalize, (1)));
        _try(abi.encodeCall(CoreActions.withdraw, (who)));
    }

    /// Challenged detection that nobody resolves: proposed and challenged (both must succeed), then optionally
    /// finalized after any gap and withdrawn by any party. (A resolution signed by k observers is not run here: with
    /// two symbolic signature batches the run did not finish in 15 minutes; I4 on that path rests on the stateful fuzz.)
    function check_I4_challengedUnanswered(uint32 kgh, bytes calldata s0, bytes calldata s1, uint32 dt, uint8 who)
        public
    {
        actions.propose(kgh, 1, s0, s1);
        actions.challenge(1, 1);
        _i4();
        actions.warp(dt);
        _try(abi.encodeCall(CoreActions.finalize, (1)));
        _try(abi.encodeCall(CoreActions.withdraw, (who)));
    }

    /// Contested detection: proposed (must succeed), contested by the seller, finalized after any gap (each optional).
    function check_I4_contested(uint32 kgh, bytes calldata s0, bytes calldata s1, uint32 dt) public {
        actions.propose(kgh, 1, s0, s1);
        _i4();
        _try(abi.encodeCall(CoreActions.contest, (1)));
        actions.warp(dt);
        _try(abi.encodeCall(CoreActions.finalize, (1)));
    }

    /// Settlement: a top-up, the term's end, settle, and the seller's withdrawal.
    function check_I4_settle(uint96 topUp, uint32[2] calldata dts, uint8 who) public {
        _try(abi.encodeCall(CoreActions.fund, (topUp)));
        _try(abi.encodeCall(CoreActions.warp, (dts[0])));
        _try(abi.encodeCall(CoreActions.warp, (dts[1])));
        _try(abi.encodeCall(CoreActions.settle, ()));
        _try(abi.encodeCall(CoreActions.withdraw, (who)));
    }

    // ------------------------------------------------------------------ reachability canaries (expected to FAIL)

    /// The lifecycles above reach a paid rebate and a settlement.
    function check_canary_rebatePaid(uint32 kgh, bytes calldata s0, bytes calldata s1) public {
        actions.propose(kgh, 1, s0, s1);
        actions.warp(2 days);
        actions.finalize(1);
        assert(cov.getCovenant(cid).paid == 0);
    }

    function check_canary_settled(uint32 dt) public {
        actions.warp(dt);
        actions.settle();
        assert(!cov.getCovenant(cid).settled);
    }
}

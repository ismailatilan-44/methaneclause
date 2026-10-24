// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {IncentivePool} from "../src/IncentivePool.sol";
import {Corroboration, KIND_CORROBORATE} from "../src/lib/CleanPass.sol";
import {IncentivePoolHarness} from "./IncentivePoolHarness.sol";
import {HalmosBase, HalmosParams} from "./HalmosBase.sol";

/// @notice The actions Halmos may call in any order with any arguments: the pool's real unsigned functions and the
///         harness stand-ins for the signed ones (IncentivePoolHarness). Two claims on one ONCHAIN pool.
contract PoolActions is Test, HalmosParams {
    IncentivePoolHarness internal immutable pool;
    bytes32 internal immutable cid;
    bytes32 internal immutable policy;
    address internal constant SPONSOR = address(0x5B0750);
    address internal constant BUYER = address(0xB0FE7);
    address internal constant RELAYER = address(0x7E1A7);
    address internal constant CHALLENGER = address(0xC4A11);
    bytes32[2] public claims;
    uint256 public passCount;
    uint96 internal constant PASS_BOND = 0.001 ether; // as _params()
    uint96 internal constant CHALLENGER_BOND = 0.002 ether;
    uint32 internal constant CLEAN_WINDOW = 1 days;

    constructor(IncentivePoolHarness pool_, bytes32 cid_, bytes32 policy_) {
        pool = pool_;
        cid = cid_;
        policy = policy_;
    }

    function topUp(uint96 value) external {
        vm.assume(value > 0);
        vm.deal(SPONSOR, value);
        vm.prank(SPONSOR);
        pool.fund{value: value}(cid, policy, IncentivePool.Mode.ONCHAIN, _params());
    }

    /// @dev Called once by the test's setUp (both claims), not a target: `setClaims` is excluded below.
    function setClaims(bytes32 a, bytes32 b) external {
        claims[0] = a;
        claims[1] = b;
    }

    /// One pass counted at once (attest, clean window, count): the observer fee and the scheduling are the real code.
    function countPass(bool second, uint8 instrumentId, bytes32 sceneId) external {
        bytes32[] memory orgs = new bytes32[](2);
        orgs[0] = "OBS_A";
        orgs[1] = "OBS_B";
        vm.deal(RELAYER, PASS_BOND);
        vm.prank(RELAYER);
        uint256 passId = pool.h_attestPass{value: PASS_BOND}(
            claims[second ? 1 : 0], sceneId, uint64(block.timestamp - 1), instrumentId, orgs
        );
        passCount = passId;
        vm.warp(block.timestamp + CLEAN_WINDOW);
        pool.countPass(passId);
    }

    /// A run of passes (over-approximation: the count may jump to any value; the scheduling is the real code).
    function countTo(bool second, uint32 counted, uint8 instruments) external {
        pool.h_countTo(claims[second ? 1 : 0], counted, instruments, uint64(block.timestamp - 1));
    }

    function corroborate(bool second, uint64 windowTo, uint32 dt) external {
        vm.warp(block.timestamp + dt);
        Corroboration memory a0;
        a0.claimId = claims[second ? 1 : 0];
        a0.windowTo = windowTo;
        a0.kind = KIND_CORROBORATE;
        pool.h_attestCorroboration(a0);
    }

    function finalizeCorroboration(bool second, uint32 dt) external {
        vm.warp(block.timestamp + dt);
        pool.finalizeCorroboration(claims[second ? 1 : 0]);
    }

    function challengeCorroboration(bool second) external {
        vm.deal(CHALLENGER, CHALLENGER_BOND);
        vm.prank(CHALLENGER);
        pool.challengeCorroboration{value: CHALLENGER_BOND}(claims[second ? 1 : 0], 5);
    }

    function release(uint8 installmentId, uint32 dt) external {
        vm.warp(block.timestamp + dt);
        pool.release(installmentId);
    }

    function relapse(bool second) external {
        pool.h_relapse(claims[second ? 1 : 0]);
    }

    function closeClaim(bool second, uint32 dt) external {
        vm.warp(block.timestamp + dt);
        pool.closeClaim(claims[second ? 1 : 0]);
    }

    /// The opener (the buyer [SIM], who funded the pool in setUp) or the top-up sponsor takes back its share.
    function sponsorWithdraw(bool opener, uint32 dt) external {
        vm.warp(block.timestamp + dt);
        vm.prank(opener ? BUYER : SPONSOR);
        pool.sponsorWithdraw(cid);
    }
}

/// @notice Shared state of the pool runs: one ONCHAIN pool funded with 0.2 ETH [SIM] and two OPEN claims; P1 and P3 as
///         assertion helpers.
abstract contract PoolHalmosSetup is HalmosBase {
    PoolActions internal actions;

    function setUp() public override {
        super.setUp();
        vm.deal(BUYER, 0.2 ether);
        vm.prank(BUYER);
        pool.fund{value: 0.2 ether}(cid, POLICY, IncentivePool.Mode.ONCHAIN, _params());
        actions = new PoolActions(pool, cid, POLICY);
        // both claims open from the start (cap 0.6 and 0.5 ETH [SIM]); one claim's two non-final shares (0.36 or
        // 0.30 ETH) already exceed the pool's 0.2 ETH, so installments queue UNFUNDED until a top-up;
        // so the depth budget is spent on counting, corroboration, release, relapse and closing
        actions.setClaims(
            pool.h_openClaim(cid, 1, 0.6 ether, uint64(block.timestamp)),
            pool.h_openClaim(cid, 2, 0.5 ether, uint64(block.timestamp))
        );
        targetContract(address(actions));
        excludeSelector(FuzzSelector({addr: address(actions), selectors: _one(PoolActions.setClaims.selector)}));
    }

    function _one(bytes4 sel) internal pure returns (bytes4[] memory s) {
        s = new bytes4[](1);
        s[0] = sel;
    }

    /// P1: paid + reserved <= cap per claim; the claims' reserves add up to the pool's reserve, which its balance covers.
    function _p1() internal view {
        (IncentivePool.Pool memory pl,) = pool.getPool(cid);
        uint256 sum;
        for (uint256 i; i < 2; ++i) {
            IncentivePool.Claim memory c = pool.getClaim(actions.claims(i));
            assert(c.paid + c.reserved <= c.cap);
            sum += c.reserved;
        }
        assert(sum == pl.reserved);
        assert(pl.reserved <= pl.balance);
    }

    /// P3: a PENDING or UNFUNDED installment belongs to an OPEN claim.
    function _p3() internal view {
        uint256 n = pool.h_installmentCount();
        for (uint256 id = 1; id <= n; ++id) {
            IncentivePool.Installment memory it = pool.getInstallment(id);
            if (it.state == IncentivePool.InstState.PENDING || it.state == IncentivePool.InstState.UNFUNDED) {
                assert(pool.getClaim(it.claimId).state == IncentivePool.ClaimState.OPEN);
            }
        }
    }
}

/// @notice Halmos, bounded: P1 and P3 after every sequence of PoolActions calls up to `--invariant-depth` (any order,
///         every argument symbolic), with reachability canaries in the same run.
contract PoolPropertiesHalmos is PoolHalmosSetup {
    function invariant_P1_capAndSolvency() public view {
        _p1();
    }

    function invariant_P3_pendingImpliesClaimOpen() public view {
        _p3();
    }

    // ------------------------------------------------------------------ reachability canaries (expected to FAIL)

    function invariant_canary_noInstallment() public view {
        assert(pool.h_installmentCount() == 0);
    }

    function invariant_canary_noUnfunded() public view {
        uint256 n = pool.h_installmentCount();
        for (uint256 id = 1; id <= n; ++id) {
            assert(pool.getInstallment(id).state != IncentivePool.InstState.UNFUNDED);
        }
    }

    function invariant_canary_nothingReleased() public view {
        assert(pool.getClaim(actions.claims(0)).paid == 0 && pool.getClaim(actions.claims(1)).paid == 0);
    }

    function invariant_canary_noCancelledWithInstallment() public view {
        if (pool.h_installmentCount() == 0) return;
        assert(
            pool.getClaim(actions.claims(0)).state != IncentivePool.ClaimState.CANCELLED
                && pool.getClaim(actions.claims(1)).state != IncentivePool.ClaimState.CANCELLED
        );
    }
}

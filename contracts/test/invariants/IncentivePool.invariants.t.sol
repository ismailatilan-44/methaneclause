// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {StdInvariant} from "forge-std/StdInvariant.sol";
import {console2} from "forge-std/console2.sol";
import {PoolFixture} from "../utils/PoolFixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {PoolHandler} from "./PoolHandler.sol";

/// @notice Stateful invariants P1-P9 of the IncentivePool (spec-v2 §3) over random sequences of core and pool actions:
///         detections, challenges, contests, resolutions, finalisations; funding and top-ups, claims, clean passes,
///         challenges and answers, counting, MARS corroborations (pending / challenged / answered / dropped),
///         installments (UNFUNDED, scheduled, released, final), relapses, closing, withdrawals, and replay attempts.
///         Deep runs: a claim needs three counted passes, a corroboration and a hold window before its final share.
/// forge-config: default.invariant.runs = 64
/// forge-config: default.invariant.depth = 512
/// forge-config: default.invariant.fail-on-revert = true
contract IncentivePoolInvariants is StdInvariant, PoolFixture {
    PoolHandler internal handler;
    IncentivePool internal poolVo;
    bytes32 internal cId;

    uint32 internal constant LAG = 3 days;
    uint32 internal constant CLEAN = 1 days;
    uint32 internal constant RESOLVE = 2 days;
    uint32 internal constant HOLD = 6 days; // >= LAG + challenge 1 d + contest 2 d
    uint32 internal constant CURE = 10 days;

    function _invParams() internal pure returns (IncentivePool.PoolParams memory p) {
        p = _validParams();
        p.publicationLag = LAG;
        p.cleanWindow = CLEAN;
        p.resolveWindow = RESOLVE;
        p.holdWindow = HOLD;
        p.cureTerm = CURE;
        // low thresholds so random walks reach every installment, the final one included
        p.installments[0] = IncentivePool.InstallmentRule(1, 3000, false, false);
        p.installments[1] = IncentivePool.InstallmentRule(2, 3000, false, false);
        p.installments[2] = IncentivePool.InstallmentRule(3, 4000, true, true);
    }

    function setUp() public override {
        super.setUp();
        MethaneCovenant.CovenantParams memory cp = _params();
        cp.term = 60 days; // short: a 512-call run (~105 simulated days) reaches the end, CLOSING / CLOSED and exits
        cp.linkWindow = 5 days;
        vm.deal(seller, 200 ether);
        vm.prank(seller);
        cId = cov.createCovenant{value: 100 ether}(cp); // a deep bond: rebates (and so claim caps) stay non-zero
        vm.warp(block.timestamp + 1 days);

        IncentivePool.PoolParams memory p = _invParams();
        vm.prank(buyer);
        pool.fund{value: 0.05 ether}(cId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p); // short on purpose: UNFUNDED
        poolVo = new IncentivePool(cov);
        vm.prank(buyer);
        poolVo.fund(cId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);

        bytes32[][] memory proofs = new bytes32[][](sources.length);
        for (uint256 i; i < sources.length; ++i) {
            proofs[i] = _proof(sources, sources[i]);
        }
        address[] memory controllers = new address[](3);
        controllers[0] = ctrlA;
        controllers[1] = ctrlB;
        controllers[2] = ctrlC;
        handler = new PoolHandler(cov, cId, pool, poolVo, sources, proofs, [obsAPk, obsBPk, obsCPk], controllers);

        bytes4[] memory s = new bytes4[](39);
        uint256 n;
        // weights: activity that creates state first
        s[n++] = PoolHandler.propose.selector;
        s[n++] = PoolHandler.propose.selector;
        s[n++] = PoolHandler.finalizeEpisode.selector;
        s[n++] = PoolHandler.finalizeEpisode.selector;
        s[n++] = PoolHandler.challengeEpisode.selector;
        s[n++] = PoolHandler.contestEpisode.selector;
        s[n++] = PoolHandler.resolveEpisode.selector;
        s[n++] = PoolHandler.warp.selector;
        s[n++] = PoolHandler.warp.selector;
        s[n++] = PoolHandler.warp.selector;
        s[n++] = PoolHandler.fund.selector;
        s[n++] = PoolHandler.openClaim.selector;
        s[n++] = PoolHandler.openClaim.selector;
        s[n++] = PoolHandler.attestPass.selector;
        s[n++] = PoolHandler.attestPass.selector;
        s[n++] = PoolHandler.attestPass.selector;
        s[n++] = PoolHandler.attestPass.selector;
        s[n++] = PoolHandler.countPass.selector;
        s[n++] = PoolHandler.countPass.selector;
        s[n++] = PoolHandler.countPass.selector;
        s[n++] = PoolHandler.challengePass.selector;
        s[n++] = PoolHandler.resolvePass.selector;
        s[n++] = PoolHandler.finalizePass.selector;
        s[n++] = PoolHandler.voidPass.selector;
        s[n++] = PoolHandler.attestCorroboration.selector;
        s[n++] = PoolHandler.attestCorroboration.selector;
        s[n++] = PoolHandler.challengeCorroboration.selector;
        s[n++] = PoolHandler.finalizeCorroboration.selector;
        s[n++] = PoolHandler.release.selector;
        s[n++] = PoolHandler.release.selector;
        s[n++] = PoolHandler.relapse.selector;
        s[n++] = PoolHandler.closeClaim.selector;
        s[n++] = PoolHandler.sponsorWithdraw.selector;
        s[n++] = PoolHandler.withdraw.selector;
        s[n++] = PoolHandler.replayCoreIntoPool.selector;
        s[n++] = PoolHandler.replayPoolIntoCore.selector;
        s[n++] = PoolHandler.attestEarlyPass.selector;
        s[n++] = PoolHandler.replayForeignDomain.selector;
        s[n++] = PoolHandler.warpFar.selector;
        targetSelector(FuzzSelector({addr: address(handler), selectors: s}));
        targetContract(address(handler));
    }

    function _popcount(uint8 bits) internal pure returns (uint256 c) {
        while (bits != 0) {
            c += bits & 1;
            bits >>= 1;
        }
    }

    // ------------------------------------------------------------------ P1 cap and solvency

    /// P1: paid + reserved <= cap per claim; ONCHAIN: the claims' reserves add up to the pool's reserve, which the
    ///     pool's balance covers; VERDICT_ONLY: policy units only, no ETH in the sponsor accounting.
    function invariant_P1_capAndSolvency() public view {
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            (IncentivePool.Pool memory pl,) = tp.getPool(cId);
            uint256 sum;
            uint256 n = handler.claimCount(p);
            for (uint256 i; i < n; ++i) {
                IncentivePool.Claim memory c = tp.getClaim(handler.claimAt(p, i));
                assertLe(c.paid + c.reserved, c.cap);
                sum += c.reserved;
            }
            if (p == 0) {
                assertEq(sum, pl.reserved);
                assertLe(pl.reserved, pl.balance);
            } else {
                assertEq(pl.reserved, 0);
                assertEq(pl.balance, 0);
            }
        }
    }

    // ------------------------------------------------------------------ P2 quorum, instruments, cure, corroboration

    /// P2: no counted pass without >= k distinct observer orgs; no final installment without two instruments, the
    ///     cure term plus grace and an effective MARS corroboration.
    function invariant_P2_quorumAndFinalGates() public view {
        uint256 k = cov.getCovenant(cId).k;
        uint64 grace = LAG + CHALLENGE_WINDOW + CONTEST_WINDOW;
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            uint256 n = handler.passCount(p);
            for (uint256 i; i < n; ++i) {
                IncentivePool.Pass memory ps = tp.getPass(handler.passAt(p, i));
                if (ps.state == IncentivePool.PassState.COUNTED) assertGe(ps.orgs.length, k);
            }
            uint256 m = handler.installmentCount(p);
            for (uint256 id = 1; id <= m; ++id) {
                IncentivePool.Installment memory it = tp.getInstallment(id);
                if (it.index != 2) continue;
                if (it.state != IncentivePool.InstState.PENDING && it.state != IncentivePool.InstState.RELEASED) {
                    continue;
                }
                IncentivePool.Claim memory c = tp.getClaim(it.claimId);
                assertGe(_popcount(c.instrumentsSeen), 2);
                assertGt(c.corroboratedTo, 0);
                assertGe(c.corroboratedTo, handler.finalTrigger(p, it.claimId)); // covers its triggering pass
                assertGe(uint256(it.claimableAt), uint256(c.since) + CURE + grace);
            }
        }
    }

    // ------------------------------------------------------------------ count bookkeeping (A4 audit)

    /// Each claim's `counted` equals the number of its passes in state COUNTED.
    function invariant_countedEqualsCountedPasses() public view {
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            uint256 nc = handler.claimCount(p);
            uint256 np = handler.passCount(p);
            for (uint256 i; i < nc; ++i) {
                bytes32 claimId = handler.claimAt(p, i);
                uint256 n;
                for (uint256 j; j < np; ++j) {
                    IncentivePool.Pass memory ps = tp.getPass(handler.passAt(p, j));
                    if (ps.claimId == claimId && ps.state == IncentivePool.PassState.COUNTED) ++n;
                }
                assertEq(tp.getClaim(claimId).counted, n);
            }
        }
    }

    // ------------------------------------------------------------------ P3 cancellation is final

    /// P3: a PENDING or UNFUNDED installment belongs to an OPEN claim (so nothing of a CANCELLED or CLOSED claim is
    ///     scheduled or released), and no release ever succeeded on a claim that was not OPEN.
    function invariant_P3_pendingImpliesClaimOpen() public view {
        assertEq(handler.vP3ReleaseOnClosedClaim(), 0);
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            uint256 m = handler.installmentCount(p);
            for (uint256 id = 1; id <= m; ++id) {
                IncentivePool.Installment memory it = tp.getInstallment(id);
                if (it.state == IncentivePool.InstState.PENDING || it.state == IncentivePool.InstState.UNFUNDED) {
                    assertEq(uint8(tp.getClaim(it.claimId).state), uint8(IncentivePool.ClaimState.OPEN));
                }
            }
        }
    }

    // ------------------------------------------------------------------ P4 ETH conservation

    /// P4: each pool's ETH equals the sponsors' balance + open pass and challenger bonds + held corroboration
    ///     challenger bonds + everything credited and not yet withdrawn (both modes).
    function invariant_P4_ethConservation() public view {
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            (IncentivePool.Pool memory pl,) = tp.getPool(cId);
            uint256 total = pl.balance + handler.corrBondHeld(p);
            uint256 n = handler.passCount(p);
            for (uint256 i; i < n; ++i) {
                IncentivePool.Pass memory ps = tp.getPass(handler.passAt(p, i));
                total += uint256(ps.bond) + ps.challengerBond;
            }
            uint256 a = handler.creditedCount();
            for (uint256 i; i < a; ++i) {
                total += tp.withdrawable(handler.credited(i));
            }
            assertEq(address(tp).balance, total);
        }
    }

    // ------------------------------------------------------------------ P6 the core is only read

    /// P6: no pool action changed the core's covenant, episodes or balance; the pool trusts exactly the covenant's
    ///     registry; there is no admin.
    function invariant_P6_coreUntouched() public view {
        assertEq(handler.vP6CoreChanged(), 0);
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            assertEq(address(tp.covenant()), address(cov));
            assertEq(address(tp.registry()), address(cov.registry()));
        }
    }

    // ------------------------------------------------------------------ P7 no replay either way

    function invariant_P7_noReplay() public view {
        assertEq(handler.vP7ReplayAccepted(), 0);
    }

    // ------------------------------------------------------------------ P8 publication lag

    /// P8: no pass was attested before sceneAt + publicationLag (attestation time = window - cleanWindow).
    function invariant_P8_publicationLag() public view {
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            uint256 n = handler.passCount(p);
            for (uint256 i; i < n; ++i) {
                IncentivePool.Pass memory ps = tp.getPass(handler.passAt(p, i));
                assertGe(uint256(ps.window) - CLEAN, uint256(ps.sceneAt) + LAG);
            }
        }
    }

    // ------------------------------------------------------------------ P9 frozen while a later detection is live

    function invariant_P9_noProgressPastLaterDetection() public view {
        assertEq(handler.vP9PastLiveDetection(), 0);
    }

    // ------------------------------------------------------------------ P5 liveness (probed once per run)

    /// P5: every open pass, challenge, corroboration and installment, and every open core episode, can be ended by
    ///     permissionless single-item calls once their windows have passed. Probed from a snapshot at the end of each
    ///     run (then reverted), so it never alters the fuzzed state.
    function afterInvariant() public {
        _accumulateReach();
        uint256 snap = vm.snapshotState();
        vm.deal(address(this), 1000 ether);
        for (uint256 round; round < 3; ++round) {
            vm.warp(block.timestamp + 400 days);
            _endCoreEpisodes();
            for (uint256 p; p < 2; ++p) {
                _endPool(p);
            }
        }
        uint256 open = _countOpen();
        vm.revertToState(snap);
        assertEq(open, 0, "P5: an item could not be ended by permissionless calls");
    }

    /// @dev Reach per run of the fuzz campaign: environment variables persist across runs in the forge process, so each
    ///      run adds 1 to every path it reached and prints the running totals (the last run's lines are the campaign's).
    function _accumulateReach() internal {
        string[14] memory keys = [
            "attestPass",
            "countPass",
            "release",
            "releaseFinal",
            "relapse",
            "relapseOverUnfunded",
            "topUpScheduledUnfunded",
            "corroborationEffective",
            "corroborationAnswered",
            "corroborationDropped",
            "poolClosingSeen",
            "poolClosedSeen",
            "closeClaim",
            "sponsorWithdraw"
        ];
        uint256 runs = vm.envOr("MC_REACH_RUNS", uint256(0)) + 1;
        vm.setEnv("MC_REACH_RUNS", vm.toString(runs));
        for (uint256 i; i < keys.length; ++i) {
            string memory name = string.concat("MC_REACH_", keys[i]);
            uint256 hits = vm.envOr(name, uint256(0)) + (handler.ok(bytes32(bytes(keys[i]))) > 0 ? 1 : 0);
            vm.setEnv(name, vm.toString(hits));
            console2.log(string.concat("reach ", keys[i]), hits, runs);
        }
    }

    function _endCoreEpisodes() internal {
        uint256 n = cov.episodeCount();
        for (uint256 i = 1; i <= n; ++i) {
            MethaneCovenant.State s = cov.getEpisode(i).state;
            if (
                s == MethaneCovenant.State.PROPOSED || s == MethaneCovenant.State.CHALLENGED
                    || s == MethaneCovenant.State.CONTESTED
            ) cov.finalize(i);
        }
    }

    function _endPool(uint256 p) internal {
        IncentivePool tp = handler.pool(p);
        uint256 nc = handler.claimCount(p);
        uint256 ne = cov.episodeCount();
        for (uint256 i; i < nc; ++i) {
            bytes32 claimId = handler.claimAt(p, i);
            try tp.finalizeCorroboration(claimId) {} catch {}
            if (tp.getClaim(claimId).state != IncentivePool.ClaimState.OPEN) continue;
            for (uint256 e = 1; e <= ne; ++e) {
                try tp.relapse(claimId, e) {
                    break;
                } catch {}
            }
        }
        uint256 np = handler.passCount(p);
        for (uint256 i; i < np; ++i) {
            uint256 id = handler.passAt(p, i);
            IncentivePool.PassState s = tp.getPass(id).state;
            if (s == IncentivePool.PassState.CHALLENGED) {
                try tp.finalizePass(id) {} catch {}
            } else if (s == IncentivePool.PassState.PENDING) {
                try tp.countPass(id) {}
                catch {
                    try tp.voidPass(id) {} catch {}
                }
            }
        }
        for (uint256 id = 1; tp.getInstallment(id).claimId != bytes32(0); ++id) {
            IncentivePool.Installment memory it = tp.getInstallment(id);
            if (it.state == IncentivePool.InstState.UNFUNDED) {
                (IncentivePool.Pool memory pl, IncentivePool.PoolParams memory params) = tp.getPool(cId);
                tp.fund{value: it.amount + 1}(cId, pl.policyHash, pl.mode, params);
            }
        }
        for (uint256 id = 1; tp.getInstallment(id).claimId != bytes32(0); ++id) {
            if (tp.getInstallment(id).state == IncentivePool.InstState.PENDING) {
                try tp.release(id) {} catch {}
            }
        }
    }

    function _countOpen() internal view returns (uint256 open) {
        uint256 n = cov.episodeCount();
        for (uint256 i = 1; i <= n; ++i) {
            MethaneCovenant.State s = cov.getEpisode(i).state;
            if (
                s == MethaneCovenant.State.PROPOSED || s == MethaneCovenant.State.CHALLENGED
                    || s == MethaneCovenant.State.CONTESTED
            ) ++open;
        }
        for (uint256 p; p < 2; ++p) {
            IncentivePool tp = handler.pool(p);
            uint256 np = handler.passCount(p);
            for (uint256 i; i < np; ++i) {
                IncentivePool.PassState s = tp.getPass(handler.passAt(p, i)).state;
                if (s == IncentivePool.PassState.PENDING || s == IncentivePool.PassState.CHALLENGED) ++open;
            }
            for (uint256 id = 1; tp.getInstallment(id).claimId != bytes32(0); ++id) {
                IncentivePool.InstState s = tp.getInstallment(id).state;
                if (s == IncentivePool.InstState.PENDING || s == IncentivePool.InstState.UNFUNDED) ++open;
            }
        }
    }

    // ------------------------------------------------------------------ reachability (the handler really gets there)

    /// A fixed pseudo-random walk over the same handler: every path the invariants are meant to cover is reached at
    /// least once (UNFUNDED top-ups, a relapse over an UNFUNDED installment, the corroboration cycle in all four
    /// outcomes, the release of a final installment), and the invariants hold at the end of it.
    /// Three fixed pseudo-random walks over the same handler, each from the same starting state: every path the
    /// invariants are meant to cover is reached by at least one walk (UNFUNDED top-ups, a relapse over an UNFUNDED
    /// installment, the corroboration cycle in all four outcomes, a final installment's release, CLOSING / CLOSED,
    /// closeClaim, sponsorWithdraw), and every invariant and the liveness probe hold at the end of each walk.
    function test_handler_reachesEveryPath() public {
        string[25] memory keys = [
            "propose",
            "openClaim",
            "attestPass",
            "countPass",
            "challengePass",
            "finalizePass",
            "voidPass",
            "fund",
            "topUpScheduledUnfunded",
            "corroborationPending",
            "corroborationChallenged",
            "corroborationAnswered",
            "corroborationDropped",
            "corroborationEffective",
            "release",
            "releaseFinal",
            "relapse",
            "relapseOverUnfunded",
            "resolvePassConfirm",
            "resolvePassWithdraw",
            "poolClosingSeen",
            "poolClosedSeen",
            "closeClaim",
            "sponsorWithdraw",
            "withdraw"
        ];
        uint256[25] memory best;
        uint256 start = vm.snapshotState();
        for (uint256 w; w < 3; ++w) {
            vm.revertToState(start);
            _walk(w);
            for (uint256 k; k < keys.length; ++k) {
                uint256 n = handler.ok(bytes32(bytes(keys[k])));
                if (n > best[k]) best[k] = n;
            }
            invariant_P1_capAndSolvency();
            invariant_countedEqualsCountedPasses();
            invariant_P2_quorumAndFinalGates();
            invariant_P3_pendingImpliesClaimOpen();
            invariant_P4_ethConservation();
            invariant_P6_coreUntouched();
            invariant_P7_noReplay();
            invariant_P8_publicationLag();
            invariant_P9_noProgressPastLaterDetection();
            afterInvariant();
        }
        for (uint256 k; k < keys.length; ++k) {
            console2.log(keys[k], best[k]);
            assertGt(best[k], 0, keys[k]);
        }
    }

    /// One fixed walk of 3,200 steps (seed `w`): an active phase of short warps, then an end game of far jumps.
    function _walk(uint256 w) internal {
        uint256 steps = 3200;
        for (uint256 i; i < steps; ++i) {
            uint256 r = uint256(keccak256(abi.encode("walk", w, i)));
            uint256 a = uint256(keccak256(abi.encode(r, 1)));
            uint256 b = uint256(keccak256(abi.encode(r, 2)));
            uint256 c = uint256(keccak256(abi.encode(r, 3)));
            uint256 pick = r % 38;
            if (pick == 0) {
                handler.propose(a, b, c, r);
            } else if (pick < 3) {
                handler.finalizeEpisode(a);
            } else if (pick < 6) {
                // active phase: short steps (<= 12 h) so most of the walk happens inside the term; end game: far jumps
                if (i < 2800) handler.warp(a % 12 hours);
                else handler.warpFar(a);
            } else if (pick == 6) {
                handler.fund(a);
            } else if (pick < 9) {
                handler.openClaim(a, b);
            } else if (pick < 15) {
                handler.attestPass(a, b, c, r, r % 2 == 0);
            } else if (pick < 18) {
                handler.countPass(a, b);
            } else if (pick < 20) {
                uint256 before = handler.ok("challengePass");
                handler.challengePass(a, b);
                // observers answer two challenges in three at once (CONFIRM / WITHDRAW alternate); the rest expire
                if (handler.ok("challengePass") > before) {
                    if (i % 3 != 0) {
                        handler.resolvePass(a, 1, c, i % 2 == 0);
                    } else {
                        handler.warp(RESOLVE); // nobody answers: anyone ends it after resolveBy
                        handler.finalizePass(a, 1);
                    }
                }
            } else if (pick == 20) {
                handler.resolvePass(a, b, c, r % 2 == 0);
            } else if (pick == 21) {
                handler.finalizePass(a, b);
            } else if (pick < 24) {
                handler.attestCorroboration(a, b, c);
            } else if (pick == 24) {
                uint256 before = handler.ok("corroborationChallenged");
                handler.challengeCorroboration(a, b);
                // responsive observers answer every other challenge at once (the rest are dropped later)
                if (handler.ok("corroborationChallenged") > before && i % 2 == 0) handler.attestCorroboration(a, 1, c);
            } else if (pick < 27) {
                handler.finalizeCorroboration(a, b);
            } else if (pick < 30) {
                handler.release(a, b);
            } else if (pick < 33) {
                handler.relapse(a, b, c);
            } else if (pick == 33) {
                handler.voidPass(a, b);
            } else if (pick == 34) {
                handler.challengeEpisode(a);
            } else if (pick == 35) {
                handler.closeClaim(a, b);
            } else if (pick == 36) {
                handler.sponsorWithdraw(a, b % 2 == 0);
            } else {
                handler.withdraw(a, b);
            }
        }
    }
}

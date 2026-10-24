// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {PoolFixture} from "../utils/PoolFixture.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {CleanPass, KIND_CONFIRM_CLEAN} from "../../src/lib/CleanPass.sol";

/// @notice A party (buyer or seller) contract that is paid by the core or the pool. `mode` 0: accepts ETH; 1: re-enters
///         `withdraw()` on the paying contract (and the pool's `sponsorWithdraw`) and records what came back; 2: rejects ETH.
contract HostileParty {
    MethaneCovenant internal immutable cov;
    IncentivePool internal immutable pool;
    uint8 internal immutable mode;
    address internal paying;
    bytes32 internal covenantId; // the covenant of the pool it re-enters (set once the covenant exists)
    uint256 public received;
    uint256 public calls;
    bytes4 public reentry;
    bytes4 public sponsorReentry;

    constructor(MethaneCovenant cov_, IncentivePool pool_, uint8 mode_) {
        cov = cov_;
        pool = pool_;
        mode = mode_;
    }

    function createCovenant(MethaneCovenant.CovenantParams calldata p) external payable returns (bytes32) {
        return cov.createCovenant{value: msg.value}(p);
    }

    function setCovenant(bytes32 covenantId_) external {
        covenantId = covenantId_;
    }

    function withdrawCore() external {
        paying = address(cov);
        cov.withdraw();
    }

    function withdrawPool() external {
        paying = address(pool);
        pool.withdraw();
    }

    receive() external payable {
        if (mode == 2) revert("no ETH");
        received += msg.value;
        calls += 1;
        if (mode == 1) {
            if (paying == address(cov)) {
                try cov.withdraw() {}
                catch (bytes memory ret) {
                    reentry = bytes4(ret);
                }
            } else {
                try pool.withdraw() {}
                catch (bytes memory ret) {
                    reentry = bytes4(ret);
                }
                try pool.sponsorWithdraw(covenantId) {}
                catch (bytes memory ret) {
                    sponsorReentry = bytes4(ret);
                }
            }
        }
    }
}

/// @notice A sponsor whose `receive()` re-enters the pool's `withdraw()` and `sponsorWithdraw()` while it is being
///         paid, and records what each re-entry returned.
contract ReentrantSponsor {
    IncentivePool internal immutable pool;
    bytes32 internal covenantId;
    uint256 public received;
    uint256 public calls;
    bytes4 public withdrawReentry;
    bytes4 public sponsorWithdrawReentry;

    constructor(IncentivePool pool_) {
        pool = pool_;
    }

    function fund(bytes32 covenantId_, bytes32 policyHash, IncentivePool.PoolParams calldata p) external payable {
        covenantId = covenantId_;
        pool.fund{value: msg.value}(covenantId_, policyHash, IncentivePool.Mode.ONCHAIN, p);
    }

    function sponsorWithdraw() external {
        pool.sponsorWithdraw(covenantId);
    }

    function withdraw() external {
        pool.withdraw();
    }

    receive() external payable {
        received += msg.value;
        calls += 1;
        try pool.withdraw() {}
        catch (bytes memory ret) {
            withdrawReentry = bytes4(ret);
        }
        try pool.sponsorWithdraw(covenantId) {}
        catch (bytes memory ret) {
            sponsorWithdrawReentry = bytes4(ret);
        }
    }
}

/// @notice Hostile receivers on the pool's pull payment (spec-v2 §9.6; §15 A1 audit N3). The core's `withdraw()`
///         and the reverting receiver on the pool (`test_withdraw_revertingReceiver_keepsCredit`) are covered elsewhere;
///         A4 adds the core's buyer and the pool's seller, reentrant and reverting.
contract HostileReceiverTest is PoolFixture {
    function test_pool_reentrantSponsor_paidOnceExactly() public {
        _openOnchain(1 ether);
        ReentrantSponsor rs = new ReentrantSponsor(pool);
        IncentivePool.PoolParams memory p = _validParams();
        rs.fund{value: 3 ether}(covId, POLICY_V2_HASH, p);
        vm.warp(_closingAt());

        rs.sponsorWithdraw();
        assertEq(pool.withdrawable(address(rs)), 3 ether);
        rs.withdraw();

        // paid exactly its credit, once; both re-entries found nothing left (checks-effects-interactions)
        assertEq(rs.received(), 3 ether);
        assertEq(rs.calls(), 1);
        assertEq(rs.withdrawReentry(), IncentivePool.NothingToWithdraw.selector);
        assertEq(rs.sponsorWithdrawReentry(), IncentivePool.NothingToWithdraw.selector);
        assertEq(pool.withdrawable(address(rs)), 0);
        assertEq(pool.contributions(covId, address(rs)), 0);

        // the other sponsor's share is untouched
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);
        assertEq(pool.withdrawable(buyer), 1 ether);
        vm.prank(buyer);
        pool.withdraw();
        assertEq(address(pool).balance, 0);
    }

    // ================================================================== core: hostile buyer (spec-v2 §9.6)

    uint32 internal constant RATE = 2548; // tier 2: rebate TIER2

    /// @dev A covenant whose buyer is `b` (registered as the buyer org's BUYER key), with one CONFIRMED episode: the
    ///      core credits the rebate to `b`.
    function _coreRebateTo(HostileParty b) internal returns (bytes32 cId) {
        _setController(ORG_BUYER, address(this));
        registry.registerKey(ORG_BUYER, KeyRegistry.Role.BUYER, address(b), 1, 0);
        MethaneCovenant.CovenantParams memory p = _params();
        p.buyer = address(b);
        vm.prank(seller);
        cId = cov.createCovenant{value: BOND}(p);
        vm.warp(block.timestamp + 2 days);
        uint256 ep = _proposeOn(cId, SRC_TKM, RATE, uint64(block.timestamp - 1 days));
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(ep);
        assertEq(cov.withdrawable(address(b)), TIER2);
    }

    function test_core_reentrantBuyer_paidOnceExactly() public {
        HostileParty b = new HostileParty(cov, pool, 1);
        _coreRebateTo(b);
        uint256 coreBalance = address(cov).balance;
        b.withdrawCore();
        assertEq(b.received(), TIER2);
        assertEq(b.calls(), 1);
        assertEq(b.reentry(), MethaneCovenant.NothingToWithdraw.selector); // zeroed before the transfer
        assertEq(cov.withdrawable(address(b)), 0);
        assertEq(address(cov).balance, coreBalance - TIER2);
    }

    function test_core_revertingBuyer_keepsCredit() public {
        HostileParty b = new HostileParty(cov, pool, 2);
        _coreRebateTo(b);
        vm.expectRevert(MethaneCovenant.TransferFailed.selector);
        b.withdrawCore();
        assertEq(cov.withdrawable(address(b)), TIER2); // the revert rolled the zeroing back
    }

    // ================================================================== pool: hostile seller

    /// @dev A covenant whose seller is `s`, an ONCHAIN pool on it, a claim, and one clean pass whose challenge the
    ///      observers answer with CONFIRM: the failed challenger's bond is credited to the seller by the pool.
    function _poolCreditTo(HostileParty s) internal returns (bytes32 cId) {
        _setController(ORG_SELLER, address(this));
        registry.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, address(s), 1, 0);
        MethaneCovenant.CovenantParams memory p = _params();
        vm.deal(address(s), 10 ether);
        cId = s.createCovenant{value: BOND}(p);
        s.setCovenant(cId); // its re-entry targets this covenant's open pool
        IncentivePool.PoolParams memory pp = _validParams();
        vm.prank(buyer);
        pool.fund{value: 10 ether}(cId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, pp);
        vm.warp(block.timestamp + 2 days);
        uint64 detected = uint64(block.timestamp - 1 days);
        uint256 ep = _proposeOn(cId, SRC_TKM, RATE, detected);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(ep);
        bytes32 claimId = pool.openClaim(ep);
        uint64 sceneAt = detected + LINK_WINDOW + 1 days;
        vm.warp(uint256(sceneAt) + PUBLICATION_LAG);
        CleanPass memory t = _cp(claimId, SRC_TKM, sceneAt);
        uint256 passId = _attestPass(t);
        uint8 reason = pool.REASON_PLUME_ON_SCENE();
        vm.prank(poolChallenger);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(passId, reason);
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), t, KIND_CONFIRM_CLEAN);
        pool.resolvePass(passId, atts, sigs);
        assertEq(pool.withdrawable(address(s)), POOL_CHALLENGER_BOND);
    }

    function test_pool_revertingSeller_keepsCredit() public {
        HostileParty s = new HostileParty(cov, pool, 2);
        _poolCreditTo(s);
        vm.expectRevert(IncentivePool.TransferFailed.selector);
        s.withdrawPool();
        assertEq(pool.withdrawable(address(s)), POOL_CHALLENGER_BOND);
    }

    function test_pool_reentrantSeller_paidOnceExactly() public {
        HostileParty s = new HostileParty(cov, pool, 1);
        _poolCreditTo(s);
        uint256 poolBalance = address(pool).balance;
        s.withdrawPool();
        assertEq(s.received(), POOL_CHALLENGER_BOND);
        assertEq(s.calls(), 1);
        assertEq(s.reentry(), IncentivePool.NothingToWithdraw.selector);
        assertEq(s.sponsorReentry(), IncentivePool.PoolNotClosed.selector); // its own covenant's pool is still OPEN
        assertEq(address(pool).balance, poolBalance - POOL_CHALLENGER_BOND);
    }
}

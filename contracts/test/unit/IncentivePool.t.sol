// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {PoolFixture} from "../utils/PoolFixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {Attestation, KIND_DETECT, KIND_WITHDRAW} from "../../src/lib/Attestation.sol";
import {
    CleanPass,
    Corroboration,
    KIND_CLEAN,
    KIND_CONFIRM_CLEAN,
    KIND_WITHDRAW_CLEAN,
    KIND_CORROBORATE
} from "../../src/lib/CleanPass.sol";

/// @notice A sponsor contract without `receive()`: funds the pool, later withdraws, and cannot accept ETH.
contract RejectingSponsor {
    IncentivePool internal immutable pool;

    constructor(IncentivePool pool_) {
        pool = pool_;
    }

    function fund(bytes32 covenantId, bytes32 policyHash, IncentivePool.PoolParams calldata p) external payable {
        pool.fund{value: msg.value}(covenantId, policyHash, IncentivePool.Mode.ONCHAIN, p);
    }

    function sponsorWithdraw(bytes32 covenantId) external {
        pool.sponsorWithdraw(covenantId);
    }

    function withdraw() external {
        pool.withdraw();
    }
}

/// @notice Pool lifecycle and claims (spec-v2 §3 "Pool per covenant", "Lifecycle", "Claims"; §9.7 R18, R22): fund, lifecycle,
///         sponsorWithdraw, openClaim, closeClaim, withdraw, receive. One test per custom error touched; events asserted.
contract IncentivePoolTest is PoolFixture {
    uint32 internal constant RATE_T2 = 2548; // tier 2 (core: 1,500 <= rate < 5,000 kg/h)
    uint32 internal constant RATE_T3 = 694; // tier 3
    uint64 internal detected;

    event PoolFunded(
        bytes32 indexed covenantId,
        address indexed sponsor,
        uint256 amount,
        bytes32 policyHash,
        IncentivePool.Mode mode,
        bytes32 paramsHash
    );
    event ClaimOpened(
        bytes32 indexed claimId, uint256 indexed episodeId, bytes32 covenantId, bytes32 sourceId, uint256 cap
    );
    event ClaimClosed(bytes32 indexed claimId);
    event Credited(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);

    function setUp() public override {
        super.setUp();
        detected = uint64(block.timestamp + 5 days);
        vm.warp(block.timestamp + 10 days);
    }

    // ================================================================== fund: opening

    function test_fund_byBuyer_opensPool_storesParams_emits() public {
        IncentivePool.PoolParams memory p = _validParams();
        bytes32 ph = keccak256(abi.encode(p));
        vm.expectEmit(true, true, false, true, address(pool));
        emit PoolFunded(covId, buyer, 1 ether, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, ph);
        vm.prank(buyer);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);

        (IncentivePool.Pool memory pl, IncentivePool.PoolParams memory stored) = pool.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.OPEN));
        assertEq(uint8(pl.mode), uint8(IncentivePool.Mode.ONCHAIN));
        assertEq(pl.policyHash, POLICY_V2_HASH);
        assertEq(pl.paramsHash, ph);
        assertEq(pl.balance, 1 ether);
        assertEq(pl.reserved, 0);
        assertEq(pl.opener, buyer);
        assertEq(keccak256(abi.encode(stored)), ph);
        assertEq(pool.contributions(covId, buyer), 1 ether);
        assertEq(address(pool).balance, 1 ether);
    }

    function test_fund_bySeller_opensPool() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(seller);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(pl.opener, seller);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.OPEN));
    }

    /// R18: a stranger cannot squat the covenant's only pool slot with a policy or mode of its choosing.
    function test_fund_firstByStranger_reverts() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(stranger);
        vm.expectRevert(IncentivePool.PoolNotOpenedByParty.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        // VERDICT_ONLY at zero cost is refused the same way
        vm.prank(stranger);
        vm.expectRevert(IncentivePool.PoolNotOpenedByParty.selector);
        pool.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.NONE));
    }

    function test_fund_unknownCovenant_reverts() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PoolNotOpenedByParty.selector);
        pool.fund{value: 1 ether}(bytes32(uint256(999)), POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
    }

    function test_fund_paramsHash_isAbiEncode() public {
        _openOnchain(1 ether);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(pl.paramsHash, keccak256(abi.encode(_validParams())));
    }

    // ================================================================== fund: top-ups

    function test_fund_topUpByAnyone_tracksContributions() public {
        _openOnchain(1 ether);
        IncentivePool.PoolParams memory p = _validParams();
        bytes32 ph = keccak256(abi.encode(p));
        vm.expectEmit(true, true, false, true, address(pool));
        emit PoolFunded(covId, sponsor, 3 ether, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, ph);
        vm.prank(sponsor);
        pool.fund{value: 3 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        vm.prank(sponsor);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);

        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(pl.balance, 5 ether);
        assertEq(pl.opener, buyer); // a top-up never changes the opener
        assertEq(pool.contributions(covId, buyer), 1 ether);
        assertEq(pool.contributions(covId, sponsor), 4 ether);
        assertEq(address(pool).balance, 5 ether);
    }

    /// R18: later calls must match policyHash, mode and paramsHash.
    function test_fund_laterMismatch_reverts() public {
        _openOnchain(1 ether);
        IncentivePool.PoolParams memory p = _validParams();

        vm.prank(sponsor);
        vm.expectRevert(IncentivePool.PolicyMismatch.selector);
        pool.fund{value: 1 ether}(covId, keccak256("another policy"), IncentivePool.Mode.ONCHAIN, p);

        vm.prank(sponsor);
        vm.expectRevert(IncentivePool.PolicyMismatch.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);

        p.cureTerm += 1;
        vm.prank(sponsor);
        vm.expectRevert(IncentivePool.PolicyMismatch.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);

        // the opener is held to the same rule
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PolicyMismatch.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);

        assertEq(pool.contributions(covId, sponsor), 0);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(pl.balance, 1 ether);
    }

    function test_fund_onchainZeroValue_reverts() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.BadParams.selector);
        pool.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);

        _openOnchain(1 ether);
        vm.prank(sponsor);
        vm.expectRevert(IncentivePool.BadParams.selector);
        pool.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
    }

    function test_fund_verdictOnly_acceptsZero() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.expectEmit(true, true, false, true, address(pool));
        emit PoolFunded(covId, buyer, 0, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, keccak256(abi.encode(p)));
        vm.prank(buyer);
        pool.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.OPEN));
        assertEq(uint8(pl.mode), uint8(IncentivePool.Mode.VERDICT_ONLY));
        assertEq(pl.balance, 0);
    }

    /// VERDICT_ONLY amounts are policy units with no balance (spec-v2 §3 "Settlement modes"): ETH is refused.
    function test_fund_verdictOnly_rejectsValue() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.BadParams.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);
    }

    function test_fund_verdictOnly_topUp_reverts() public {
        _open(IncentivePool.Mode.VERDICT_ONLY, 0);
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        vm.expectRevert(IncentivePool.BadParams.selector);
        pool.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);
    }

    function test_fund_duringClosing_topUpAllowed() public {
        _openOnchain(1 ether);
        uint256 ep = _confirmed(SRC_TKM, RATE_T2, detected);
        pool.openClaim(ep); // keeps the pool from reaching CLOSED
        vm.warp(_closingAt());
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSING));
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(pool.contributions(covId, sponsor), 1 ether);
    }

    function test_fund_afterClosed_reverts() public {
        _openOnchain(1 ether);
        vm.warp(_closingAt()); // no claim open -> CLOSED
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        vm.expectRevert(IncentivePool.PoolClosed.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
    }

    /// spec-v2 §15 (A1 audit N4): a pool cannot be opened once its closing time has passed.
    function test_fund_openAfterClosingAt_reverts() public {
        vm.warp(_closingAt());
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PoolClosed.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.NONE));

        // one second earlier it opens (and is OPEN for exactly that second)
        vm.warp(_closingAt() - 1);
        vm.prank(buyer);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.OPEN));
    }

    // ================================================================== fund: parameter validation (BadParams)

    function _expectBadParams(IncentivePool.PoolParams memory p) internal {
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.BadParams.selector);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
    }

    function test_fund_badParams_zeroPolicyHash() public {
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.BadParams.selector);
        pool.fund{value: 1 ether}(covId, bytes32(0), IncentivePool.Mode.ONCHAIN, p);
    }

    function test_fund_badParams_zeroCleanWindow() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.cleanWindow = 0;
        _expectBadParams(p);
    }

    function test_fund_badParams_resolveBelowClean() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.resolveWindow = p.cleanWindow - 1;
        _expectBadParams(p);
    }

    function test_fund_badParams_holdTooShort() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.holdWindow = uint32(PUBLICATION_LAG + CHALLENGE_WINDOW + CONTEST_WINDOW - 1);
        _expectBadParams(p);
    }

    function test_fund_badParams_holdExactMinimum_ok() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.holdWindow = uint32(PUBLICATION_LAG + CHALLENGE_WINDOW + CONTEST_WINDOW);
        vm.prank(buyer);
        pool.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.OPEN));
    }

    function test_fund_badParams_zeroBonds() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.passBond = 0;
        _expectBadParams(p);
        p = _validParams();
        p.challengerBond = 0;
        _expectBadParams(p);
    }

    function test_fund_badParams_thresholdZero() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[0].afterPasses = 0;
        _expectBadParams(p);
    }

    function test_fund_badParams_thresholdsNotIncreasing() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[1].afterPasses = p.installments[0].afterPasses; // equal is not strictly increasing
        _expectBadParams(p);
        p = _validParams();
        p.installments[2].afterPasses = p.installments[1].afterPasses - 1;
        _expectBadParams(p);
    }

    function test_fund_badParams_zeroShare() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[0].shareBp = 0;
        p.installments[1].shareBp = 6000; // sum still 10000
        _expectBadParams(p);
    }

    function test_fund_badParams_sharesNot10000() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[2].shareBp = 4001;
        _expectBadParams(p);
        p.installments[2].shareBp = 3999;
        _expectBadParams(p);
    }

    function test_fund_badParams_finalNotLast() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[2].isFinal = false;
        _expectBadParams(p);
    }

    function test_fund_badParams_finalEarly() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[1].isFinal = true;
        _expectBadParams(p);
    }

    /// Security review item 5, R21: the final installment must require two instruments on chain (not only by
    /// policy), so a single provider's instrument can never release the largest share.
    function test_fund_badParams_finalWithoutTwoInstruments() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.installments[2].twoInstruments = false;
        _expectBadParams(p);
    }

    function test_fund_badParams_upperBoundAbove10000() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.upperBoundBp[0] = 10001;
        _expectBadParams(p);
    }

    function test_fund_badParams_upperBoundIncreasing() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.upperBoundBp[31] = p.upperBoundBp[30] + 1;
        _expectBadParams(p);
    }

    // ================================================================== lifecycle

    function test_getPool_none_forUnknownCovenant() public view {
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.NONE));
    }

    /// OPEN -> CLOSING at end + publicationLag + cleanWindow -> CLOSED when no claim is OPEN, visible without any tx.
    function test_lifecycle_openClosingClosed_viewTracksTime() public {
        _openOnchain(1 ether);
        uint256 ep = _confirmed(SRC_TKM, RATE_T2, detected);
        bytes32 claimId = pool.openClaim(ep);

        vm.warp(_closingAt() - 1);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.OPEN));
        vm.warp(_closingAt());
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSING));
        pool.closeClaim(claimId);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSED));
    }

    function test_lifecycle_noClaims_closedAtClosingTime() public {
        _openOnchain(1 ether);
        vm.warp(_closingAt());
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSED));
    }

    // ================================================================== sponsorWithdraw

    /// R22: no withdrawal before CLOSED (OPEN, CLOSING with an open claim, or no pool at all).
    function test_sponsorWithdraw_beforeClosed_reverts() public {
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PoolNotClosed.selector);
        pool.sponsorWithdraw(covId);

        _openOnchain(1 ether);
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PoolNotClosed.selector);
        pool.sponsorWithdraw(covId);

        uint256 ep = _confirmed(SRC_TKM, RATE_T2, detected);
        pool.openClaim(ep);
        vm.warp(_closingAt());
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PoolNotClosed.selector);
        pool.sponsorWithdraw(covId);
        assertEq(pool.withdrawable(buyer), 0);
    }

    function test_sponsorWithdraw_proRata_lastTakesRemainder_noDust() public {
        // 1 + 2 wei-odd contributions: shares do not divide evenly
        _openOnchain(1 ether + 1);
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        pool.fund{value: 2 ether + 1}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        vm.prank(sponsor2);
        pool.fund{value: 1}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        uint256 total = 3 ether + 3;

        vm.warp(_closingAt());
        vm.expectEmit(true, false, false, true, address(pool));
        emit Credited(sponsor, 2 ether + 1);
        vm.prank(sponsor);
        pool.sponsorWithdraw(covId);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSED));

        vm.prank(sponsor2);
        pool.sponsorWithdraw(covId);
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);

        assertEq(pool.withdrawable(sponsor), 2 ether + 1);
        assertEq(pool.withdrawable(sponsor2), 1);
        assertEq(pool.withdrawable(buyer), 1 ether + 1);
        assertEq(pool.withdrawable(sponsor) + pool.withdrawable(sponsor2) + pool.withdrawable(buyer), total);
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        assertEq(pl.balance, 0);
        assertEq(pool.contributions(covId, sponsor), 0);
    }

    function test_sponsorWithdraw_twice_reverts() public {
        _openOnchain(1 ether);
        vm.warp(_closingAt());
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.NothingToWithdraw.selector);
        pool.sponsorWithdraw(covId);
        assertEq(pool.withdrawable(buyer), 1 ether);
    }

    function test_sponsorWithdraw_nonSponsor_reverts() public {
        _openOnchain(1 ether);
        vm.warp(_closingAt());
        vm.prank(stranger);
        vm.expectRevert(IncentivePool.NothingToWithdraw.selector);
        pool.sponsorWithdraw(covId);
    }

    // ================================================================== openClaim

    function test_openClaim_onchain_capIsRebate_sinceIsDetectedPlusLink_emits() public {
        _openOnchain(1 ether);
        uint256 ep = _confirmed(SRC_TKM, RATE_T2, detected);
        MethaneCovenant.Episode memory e = cov.getEpisode(ep);
        assertEq(e.rebate, TIER2);

        bytes32 want = _claimIdOf(ep);
        vm.expectEmit(true, true, false, true, address(pool));
        emit ClaimOpened(want, ep, covId, SRC_TKM, TIER2);
        vm.prank(stranger); // anyone
        bytes32 claimId = pool.openClaim(ep);
        assertEq(claimId, want);

        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.episodeId, ep);
        assertEq(c.covenantId, covId);
        assertEq(c.sourceId, SRC_TKM);
        assertEq(c.since, detected + LINK_WINDOW);
        assertEq(c.rateKgH, RATE_T2);
        assertEq(c.tier, 2);
        assertEq(c.cap, TIER2);
        assertEq(c.paid, 0);
        assertEq(c.reserved, 0);
        assertEq(c.counted, 0);
        assertEq(uint8(c.state), uint8(IncentivePool.ClaimState.OPEN));
    }

    function test_openClaim_verdictOnly_capIsUnitTier() public {
        _open(IncentivePool.Mode.VERDICT_ONLY, 0);
        uint256 ep = _confirmed(SRC_DZA, RATE_T3, detected);
        bytes32 claimId = pool.openClaim(ep);
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.tier, 3);
        assertEq(c.cap, 100); // unitTierAmounts[3 - 1], policy units, not the core's ETH rebate
    }

    function test_openClaim_notConfirmed_reverts() public {
        _openOnchain(1 ether);
        // unknown episode
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotConfirmed.selector, 77));
        pool.openClaim(77);

        // PROPOSED
        uint256 ep = _propose(SRC_TKM, _nextEvidence(), RATE_T2, detected);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotConfirmed.selector, ep));
        pool.openClaim(ep);

        // LINKED (a second detection inside the link window of a live episode)
        uint256 linked = _propose(SRC_TKM, _nextEvidence(), RATE_T2, detected + 1 days);
        assertEq(uint8(_state(linked)), uint8(MethaneCovenant.State.LINKED));
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotConfirmed.selector, linked));
        pool.openClaim(linked);

        // EXPIRED (challenged, unresolved)
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(ep, 1);
        vm.warp(block.timestamp + CHALLENGE_WINDOW + CONTEST_WINDOW + 1);
        cov.finalize(ep);
        assertEq(uint8(_state(ep)), uint8(MethaneCovenant.State.EXPIRED));
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotConfirmed.selector, ep));
        pool.openClaim(ep);
    }

    function test_openClaim_noPool_reverts() public {
        uint256 ep = _confirmed(SRC_TKM, RATE_T2, detected);
        vm.expectRevert(IncentivePool.WrongCovenant.selector);
        pool.openClaim(ep);
    }

    function test_openClaim_poolClosing_reverts() public {
        _openOnchain(1 ether);
        uint256 ep1 = _confirmed(SRC_TKM, RATE_T2, detected);
        uint256 ep2 = _confirmed(SRC_DZA, RATE_T2, detected);
        pool.openClaim(ep1);
        vm.warp(_closingAt());
        vm.expectRevert(IncentivePool.PoolClosed.selector);
        pool.openClaim(ep2);
    }

    function test_openClaim_twice_reverts() public {
        _openOnchain(1 ether);
        uint256 ep = _confirmed(SRC_TKM, RATE_T2, detected);
        pool.openClaim(ep);
        vm.expectRevert(IncentivePool.ClaimExists.selector);
        pool.openClaim(ep);
    }

    function test_openClaim_onchainZeroRebate_reverts() public {
        bytes32 cId = _zeroBondCovenant();
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        pool.fund{value: 1 ether}(cId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        vm.warp(block.timestamp + 2 days); // the new covenant starts at its creation
        uint256 ep = _proposeOn(cId, SRC_TKM, RATE_T2, uint64(block.timestamp - 1 days));
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(ep);
        assertEq(cov.getEpisode(ep).rebate, 0); // the core clipped the rebate to the empty bond
        vm.expectRevert(IncentivePool.ZeroCap.selector);
        pool.openClaim(ep);
    }

    function test_openClaim_verdictOnlyZeroTier_reverts() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.unitTierAmounts[2] = 0;
        vm.prank(buyer);
        pool.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);
        uint256 ep = _confirmed(SRC_DZA, RATE_T3, detected);
        vm.expectRevert(IncentivePool.ZeroCap.selector);
        pool.openClaim(ep);
    }

    // ================================================================== closeClaim

    function test_closeClaim_unknown_reverts() public {
        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.closeClaim(keccak256("no such claim"));
    }

    function test_closeClaim_beforeClosing_reverts() public {
        _openOnchain(1 ether);
        bytes32 claimId = pool.openClaim(_confirmed(SRC_TKM, RATE_T2, detected));
        vm.warp(_closingAt() - 1);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.closeClaim(claimId);
        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.OPEN));
    }

    function test_closeClaim_repeat_reverts() public {
        _openOnchain(1 ether);
        bytes32 claimId = pool.openClaim(_confirmed(SRC_TKM, RATE_T2, detected));
        vm.warp(_closingAt());
        pool.closeClaim(claimId);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.closeClaim(claimId);
    }

    function test_closeClaim_lastClaim_closesPool_emits() public {
        _openOnchain(1 ether);
        bytes32 c1 = pool.openClaim(_confirmed(SRC_TKM, RATE_T2, detected));
        bytes32 c2 = pool.openClaim(_confirmed(SRC_DZA, RATE_T2, detected));
        vm.warp(_closingAt());

        vm.expectEmit(true, false, false, true, address(pool));
        emit ClaimClosed(c1);
        vm.prank(stranger); // permissionless
        pool.closeClaim(c1);
        assertEq(uint8(pool.getClaim(c1).state), uint8(IncentivePool.ClaimState.CLOSED));
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSING));

        pool.closeClaim(c2);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSED));
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);
        assertEq(pool.withdrawable(buyer), 1 ether);
    }

    // ================================================================== withdraw / receive

    function test_withdraw_nothing_reverts() public {
        vm.prank(stranger);
        vm.expectRevert(IncentivePool.NothingToWithdraw.selector);
        pool.withdraw();
    }

    function test_withdraw_paysAndZeroes_emits() public {
        _openOnchain(1 ether);
        vm.warp(_closingAt());
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);

        uint256 before = buyer.balance;
        vm.expectEmit(true, false, false, true, address(pool));
        emit Withdrawn(buyer, 1 ether);
        vm.prank(buyer);
        pool.withdraw();
        assertEq(buyer.balance - before, 1 ether);
        assertEq(pool.withdrawable(buyer), 0);
        assertEq(address(pool).balance, 0);
    }

    function test_withdraw_revertingReceiver_keepsCredit() public {
        _openOnchain(1 ether);
        RejectingSponsor rs = new RejectingSponsor(pool);
        IncentivePool.PoolParams memory p = _validParams();
        rs.fund{value: 2 ether}(covId, POLICY_V2_HASH, p);
        vm.warp(_closingAt());
        rs.sponsorWithdraw(covId);
        assertEq(pool.withdrawable(address(rs)), 2 ether);

        vm.expectRevert(IncentivePool.TransferFailed.selector);
        rs.withdraw();
        assertEq(pool.withdrawable(address(rs)), 2 ether); // the revert rolled the zeroing back
        assertEq(address(pool).balance, 3 ether);
    }

    function test_receive_reverts() public {
        vm.prank(stranger);
        (bool ok, bytes memory ret) = address(pool).call{value: 1 ether}("");
        assertFalse(ok);
        assertEq(bytes4(ret), IncentivePool.InvalidState.selector);
        assertEq(address(pool).balance, 0);
    }
}

/// @notice Clean passes (spec-v2 §2 rule 5, §3 "Clean-pass attestation", "Counting", "Challenge of a clean pass", "Source
///         under review"; §9.7 R13, R15, R16, R21; P8, P9; §15 A1-audit N2): attestCleanPass, challengePass,
///         resolvePass, finalizePass, countPass with the observer fee, closeClaim with open passes.
contract IncentivePoolPassTest is PoolFixture {
    uint32 internal constant RATE = 2548; // tier 2
    uint256 internal constant POOL_VALUE = 10 ether;

    uint64 internal detected;
    uint256 internal ep;
    bytes32 internal claimId;
    uint64 internal since;
    uint64 internal sceneAt;

    function setUp() public override {
        super.setUp();
        detected = uint64(block.timestamp + 5 days);
        vm.warp(block.timestamp + 10 days);
        _openOnchain(POOL_VALUE);
        ep = _confirmed(SRC_TKM, RATE, detected);
        claimId = pool.openClaim(ep);
        since = detected + LINK_WINDOW;
        sceneAt = since + 1 days;
        vm.warp(uint256(sceneAt) + PUBLICATION_LAG); // the first moment the scene may be attested
    }

    // ------------------------------------------------------------------ helpers

    function _t() internal returns (CleanPass memory) {
        return _cp(claimId, SRC_TKM, sceneAt);
    }

    function _expectAttestRevert(bytes memory err, CleanPass[] memory atts, bytes[] memory sigs) internal {
        vm.prank(relayer);
        vm.expectRevert(err);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);
    }

    function _expectAttestRevertAB(bytes memory err, CleanPass memory t) internal {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), t, KIND_CLEAN);
        _expectAttestRevert(err, atts, sigs);
    }

    function _balance() internal view returns (uint256) {
        (IncentivePool.Pool memory pl,) = pool.getPool(covId);
        return pl.balance;
    }

    function _challenge(uint256 passId) internal {
        uint8 reason = pool.REASON_PLUME_ON_SCENE(); // read first: an external call would consume the prank
        vm.prank(poolChallenger);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(passId, reason);
    }

    function _resolution(uint256 passId, uint8 kind) internal returns (CleanPass[] memory, bytes[] memory) {
        IncentivePool.Pass memory p = pool.getPass(passId);
        CleanPass memory t = _cp(p.claimId, SRC_TKM, p.sceneAt);
        t.sceneId = p.sceneId;
        t.sceneHash = p.sceneHash;
        t.publishedAt = p.publishedAt;
        t.instrumentId = p.instrumentId;
        t.cloudBp = p.cloudBp;
        return _cpBatch(_pks(obsAPk, obsBPk), t, kind);
    }

    /// @dev A new, non-LINKED core proposal at the claim's source, detected after `since` (a relapse candidate).
    function _relapseProposal() internal returns (uint256 id, bytes32 ev) {
        ev = _nextEvidence();
        id = _propose(SRC_TKM, ev, RATE, since + 10 days);
    }

    function _mutate(CleanPass memory a, uint256 field) internal pure {
        if (field == 0) a.claimId = keccak256("other claim");
        else if (field == 1) a.sourceId = SRC_DZA;
        else if (field == 2) a.sceneId = keccak256("other scene");
        else if (field == 3) a.sceneHash = keccak256("other bundle");
        else if (field == 4) a.sceneAt -= 1;
        else if (field == 5) a.publishedAt -= 1;
        else if (field == 6) a.instrumentId = 2;
        else if (field == 7) a.providerId = "other-provider";
        else if (field == 8) a.cloudBp += 1;
        else if (field == 9) a.notCloudy = false;
        else if (field == 10) a.floorKgH += 1;
        else a.rateFloorKgH += 1;
    }

    // ================================================================== attestCleanPass: happy path

    function test_attestCleanPass_storesPendingPass_emits() public {
        CleanPass memory t = _t();
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassAttested(claimId, 1, t.sceneId, t.sceneHash, t.sceneAt, t.instrumentId);
        uint256 passId = _attestPass(t);
        assertEq(passId, 1);

        IncentivePool.Pass memory p = pool.getPass(passId);
        assertEq(p.claimId, claimId);
        assertEq(p.sceneId, t.sceneId);
        assertEq(p.sceneHash, t.sceneHash);
        assertEq(p.sceneAt, t.sceneAt);
        assertEq(p.publishedAt, t.publishedAt);
        assertEq(p.instrumentId, t.instrumentId);
        assertEq(p.cloudBp, t.cloudBp);
        assertEq(p.submitter, relayer);
        assertEq(p.bond, PASS_BOND);
        assertEq(p.orgs.length, 2);
        assertEq(p.orgs[0], ORG_A);
        assertEq(p.orgs[1], ORG_B);
        assertEq(p.window, block.timestamp + CLEAN_WINDOW);
        assertEq(uint8(p.state), uint8(IncentivePool.PassState.PENDING));
        assertEq(pool.getClaim(claimId).pending, 1);
        // N2: the pass bond is held for the pass, never added to the sponsors' balance
        assertEq(_balance(), POOL_VALUE);
        assertEq(address(pool).balance, POOL_VALUE + PASS_BOND);
    }

    /// P8 boundary: exactly sceneAt + publicationLag is allowed (setUp warps there); one second earlier is not (R15).
    function test_cleanPass_beforePublicationLag_reverts() public {
        CleanPass memory t = _t();
        vm.warp(block.timestamp - 1);
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.BeforePublicationLag.selector), t);
        vm.warp(block.timestamp + 1);
        _attestPass(_t());
    }

    // ================================================================== attestCleanPass: claim, pool, bond, capacity

    function test_attestCleanPass_unknownOrClosedClaim_reverts() public {
        CleanPass memory t = _t();
        t.claimId = keccak256("no such claim");
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), t, KIND_CLEAN);
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.attestCleanPass{value: PASS_BOND}(t.claimId, atts, sigs);

        vm.warp(_closingAt());
        pool.closeClaim(claimId);
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.ClaimNotOpen.selector), _t());
    }

    function test_attestCleanPass_poolClosing_reverts() public {
        vm.warp(_closingAt());
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSING));
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.PoolClosed.selector), _t());
    }

    function test_attestCleanPass_badBond_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.BadBond.selector);
        pool.attestCleanPass{value: PASS_BOND - 1}(claimId, atts, sigs);
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.BadBond.selector);
        pool.attestCleanPass(claimId, atts, sigs);
    }

    function test_attestCleanPass_tooManyPending_reverts() public {
        uint256 max = pool.MAX_PENDING();
        for (uint256 i; i < max; ++i) {
            _attestPass(_t());
        }
        assertEq(pool.getClaim(claimId).pending, max);
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.TooManyPending.selector), _t());
    }

    // ================================================================== attestCleanPass: batch shape and quorum

    function test_attestCleanPass_badBatchShape_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        bytes[] memory oneSig = new bytes[](1);
        oneSig[0] = sigs[0];
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.BadParams.selector), atts, oneSig);

        uint256 n = pool.MAX_ATTESTATIONS() + 1;
        uint256[] memory pks = new uint256[](n);
        for (uint256 i; i < n; ++i) {
            pks[i] = i % 2 == 0 ? obsAPk : obsBPk;
        }
        (atts, sigs) = _cpBatch(pks, _t(), KIND_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.BadParams.selector), atts, sigs);
    }

    function test_attestCleanPass_quorumNotMet_reverts() public {
        CleanPass[] memory none = new CleanPass[](0);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.QuorumNotMet.selector, 0, K), none, new bytes[](0));

        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk), _t(), KIND_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.QuorumNotMet.selector, 0, K), atts, sigs);

        // two keys of one org are one org
        (atts, sigs) = _cpBatch(_pks(obsAPk, obsA2Pk), _t(), KIND_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.QuorumNotMet.selector, 1, K), atts, sigs);
    }

    function test_attestCleanPass_attestationMismatch_reverts() public {
        for (uint256 field; field < 12; ++field) {
            (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
            _mutate(atts[1], field);
            sigs[1] = _signCp(obsBPk, atts[1]); // a valid signature over the disagreeing copy
            _expectAttestRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 1), atts, sigs);
        }
    }

    function test_attestCleanPass_wrongClaimOrSource_reverts() public {
        CleanPass memory t = _t();
        t.sourceId = SRC_DZA; // signed for another source than the claim's
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 0), t);
    }

    function test_attestCleanPass_badKind_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CONFIRM_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.BadKind.selector), atts, sigs);
        (atts, sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_DETECT); // a core kind
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.BadKind.selector), atts, sigs);
    }

    function test_attestCleanPass_expired_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        atts[0].expiry = uint64(block.timestamp - 1);
        sigs[0] = _signCp(obsAPk, atts[0]);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.Expired.selector, atts[0].expiry), atts, sigs);

        (atts, sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        atts[1].expiry = uint64(block.timestamp + 1 days + 1);
        sigs[1] = _signCp(obsBPk, atts[1]);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.Expired.selector, atts[1].expiry), atts, sigs);
    }

    /// R13: a scene field edited after signing (here the cloud value) no longer recovers to a registered observer.
    function test_cleanPass_tamperedCloud_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        atts[0].cloudBp = 0;
        atts[1].cloudBp = 0; // both copies edited the same way: they still agree, the signatures do not
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.InvalidSignature.selector), atts, sigs);
        assertEq(pool.getClaim(claimId).pending, 0);
    }

    /// R21: an observer key inside the seller's (or buyer's) own org cannot sign a clean pass for its covenant.
    function test_cleanPass_sellerOrgSigner_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, sellerObsPk), _t(), KIND_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.SelfAttestation.selector, sellerObs), atts, sigs);
        (atts, sigs) = _cpBatch(_pks(obsAPk, buyerObsPk), _t(), KIND_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.SelfAttestation.selector, buyerObs), atts, sigs);
    }

    function test_attestCleanPass_nonObserverKey_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, otherSellerPk), _t(), KIND_CLEAN);
        _expectAttestRevert(
            abi.encodeWithSelector(IncentivePool.AttestorNotIndependent.selector, otherSeller), atts, sigs
        );
    }

    function test_attestCleanPass_revokedKey_reverts() public {
        vm.prank(obsC);
        registry.revokeKey(obsC);
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsCPk), _t(), KIND_CLEAN);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.RevokedKey.selector, obsC), atts, sigs);
    }

    function test_attestCleanPass_replay_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        vm.prank(relayer);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);
        uint64 usedNonce = atts[0].nonce;

        (atts, sigs) = _cpBatch(_pks(obsAPk, obsBPk), _t(), KIND_CLEAN);
        atts[0].nonce = usedNonce; // obsA reuses a nonce on a different scene
        sigs[0] = _signCp(obsAPk, atts[0]);
        _expectAttestRevert(abi.encodeWithSelector(IncentivePool.Replay.selector, obsA, usedNonce), atts, sigs);
    }

    // ================================================================== attestCleanPass: scene content

    function test_attestCleanPass_cloudy_reverts() public {
        CleanPass memory t = _t();
        t.notCloudy = false;
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.Cloudy.selector), t);
    }

    function test_attestCleanPass_floorAboveRate_reverts() public {
        CleanPass memory t = _t();
        t.rateFloorKgH = RATE + 1;
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.FloorAboveRate.selector), t);
        t = _t();
        t.rateFloorKgH = RATE; // equal to the attested rate counts
        _attestPass(t);
    }

    function test_attestCleanPass_instrumentOutOfRange_reverts() public {
        CleanPass memory t = _t();
        t.instrumentId = 0;
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.BadParams.selector), t);
        t = _t();
        t.instrumentId = 8; // the claim's instrumentsSeen is an 8-bit set of ids 1..7
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.BadParams.selector), t);
        t = _t();
        t.instrumentId = 7;
        _attestPass(t);
    }

    function test_attestCleanPass_sceneOutOfRange_reverts() public {
        bytes memory err = abi.encodeWithSelector(IncentivePool.SceneOutOfRange.selector);
        // a scene inside the core's own link window (sceneAt <= since) never counts
        CleanPass memory t = _cp(claimId, SRC_TKM, since);
        _expectAttestRevertAB(err, t);
        // published before it was acquired
        t = _t();
        t.publishedAt = t.sceneAt - 1;
        _expectAttestRevertAB(err, t);
        // published in the future
        t = _t();
        t.publishedAt = uint64(block.timestamp + 1);
        _expectAttestRevertAB(err, t);
        // published exactly now is fine
        t = _t();
        t.publishedAt = uint64(block.timestamp);
        _attestPass(t);
    }

    function test_attestCleanPass_sceneAtOrAfterEnd_reverts() public {
        uint64 end = cov.getCovenant(covId).end;
        vm.warp(uint256(end) + PUBLICATION_LAG); // the pool is still OPEN (closingAt = end + lag + cleanWindow)
        CleanPass memory t = _cp(claimId, SRC_TKM, end);
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.SceneOutOfRange.selector), t);
        _attestPass(_cp(claimId, SRC_TKM, end - 1));
    }

    function test_attestCleanPass_duplicateScene_reverts() public {
        CleanPass memory t = _t();
        _attestPass(t);
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.DuplicateScene.selector), t);
    }

    // ================================================================== source under review / relapse pending (P9)

    function test_attestCleanPass_sourceUnderReview_reverts() public {
        (uint256 id,) = _relapseProposal();
        bytes memory err = abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id);
        _expectAttestRevertAB(err, _t()); // PROPOSED

        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, 1);
        _expectAttestRevertAB(err, _t()); // CHALLENGED
    }

    function test_attestCleanPass_sourceUnderReview_contested_reverts() public {
        (uint256 id,) = _relapseProposal();
        vm.prank(seller);
        cov.contest(id);
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id), _t());
    }

    function test_attestCleanPass_relapsePending_reverts() public {
        (uint256 id,) = _relapseProposal();
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.CONFIRMED));
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.RelapsePending.selector, id), _t());
    }

    /// A seller who contests a relapse until it EXPIRES gains nothing: EXPIRED still blocks (R19).
    function test_attestCleanPass_expiredRelapseStillPending_reverts() public {
        (uint256 id,) = _relapseProposal();
        vm.prank(seller);
        cov.contest(id);
        vm.warp(block.timestamp + CONTEST_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.EXPIRED));
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.RelapsePending.selector, id), _t());
    }

    function test_attestCleanPass_voidedProposalIgnored() public {
        (uint256 id, bytes32 ev) = _relapseProposal();
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, 1);
        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(obsAPk, obsBPk), SRC_TKM, ev, RATE, since + 10 days, KIND_WITHDRAW);
        cov.submitResolution(id, atts, sigs);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.VOID));
        _attestPass(_t());
    }

    /// Residual R23, pinned so it stays visible: `latestEpisode` holds only the newest non-LINKED proposal. An EXPIRED
    /// relapse (detected after `since`) is overwritten by a later proposal dated before `since` (not LINKED, because an
    /// EXPIRED episode anchors nothing), and the spec's `detectedAt > since` filter then ignores the slot. The sponsor's
    /// watcher must call `relapse` (A3) before that happens; SECURITY.md R23 carries this variant.
    function test_attestCleanPass_relapseHiddenByOlderDatedProposal_residualR23() public {
        (uint256 relapse_,) = _relapseProposal();
        vm.prank(seller);
        cov.contest(relapse_);
        vm.warp(block.timestamp + CONTEST_WINDOW);
        cov.finalize(relapse_); // EXPIRED, detected after since: blocks
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.RelapsePending.selector, relapse_), _t());

        uint256 older = _propose(SRC_TKM, _nextEvidence(), RATE, since - 1 days);
        assertEq(uint8(_state(older)), uint8(MethaneCovenant.State.PROPOSED)); // not LINKED
        assertEq(cov.latestEpisode(covId, SRC_TKM), older);
        _attestPass(_t()); // accepted: the slot no longer shows the relapse

        // closed by relapse (spec-v2 §15 A2 audit (1)): any watcher cancels with the EXPIRED episode's id in one call
        pool.relapse(claimId, relapse_);
        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.CANCELLED));
        _expectAttestRevertAB(abi.encodeWithSelector(IncentivePool.ClaimNotOpen.selector), _t());
    }

    function test_attestCleanPass_otherSourceEpisodeIgnored() public {
        _propose(SRC_DZA, _nextEvidence(), RATE, since + 10 days); // live, but at another source
        _attestPass(_t());
    }

    // ================================================================== challengePass

    function test_challengePass_marksChallenged_emits() public {
        uint256 passId = _attestPass(_t());
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassChallenged(passId, poolChallenger, 3);
        _challenge(passId);
        IncentivePool.Pass memory p = pool.getPass(passId);
        assertEq(uint8(p.state), uint8(IncentivePool.PassState.CHALLENGED));
        assertEq(p.challenger, poolChallenger);
        assertEq(p.challengerBond, POOL_CHALLENGER_BOND);
        assertEq(p.reason, 3);
        assertEq(p.resolveBy, block.timestamp + RESOLVE_WINDOW);
        assertEq(pool.getClaim(claimId).pending, 1); // still open: CHALLENGED counts as pending
        assertEq(_balance(), POOL_VALUE); // N2: the challenger bond is not sponsor money
    }

    function test_challengePass_badBond_reverts() public {
        uint256 passId = _attestPass(_t());
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.BadBond.selector);
        pool.challengePass{value: POOL_CHALLENGER_BOND + 1}(passId, 1);
    }

    function test_challengePass_invalidReason_reverts() public {
        uint256 passId = _attestPass(_t());
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.InvalidReason.selector);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(passId, 0);
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.InvalidReason.selector);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(passId, 7);
    }

    function test_challengePass_afterWindow_reverts() public {
        uint256 passId = _attestPass(_t());
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.WindowClosed.selector);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(passId, 1);
    }

    function test_challengePass_notPending_reverts() public {
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(99, 1);

        uint256 passId = _attestPass(_t());
        _challenge(passId);
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(passId, 1);
    }

    // ================================================================== resolvePass

    /// R16: k CONFIRM_CLEAN -> COUNTED; the failed challenger's bond goes to the covenant seller (§15, 2026-10-16).
    function test_challengePass_confirm_bondToSeller() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_CONFIRM_CLEAN);

        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassCounted(claimId, passId, 1, 9750);
        pool.resolvePass(passId, atts, sigs);

        assertEq(uint8(pool.getPass(passId).state), uint8(IncentivePool.PassState.COUNTED));
        assertEq(pool.withdrawable(seller), POOL_CHALLENGER_BOND);
        assertEq(pool.withdrawable(relayer), PASS_BOND);
        assertEq(pool.withdrawable(poolChallenger), 0);
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.counted, 1);
        assertEq(c.pending, 0);
        IncentivePool.Pass memory p = pool.getPass(passId);
        assertEq(p.bond, 0);
        assertEq(p.challengerBond, 0);
    }

    function test_challengePass_withdraw_voidsPass_bondToChallenger() public {
        CleanPass memory t = _t();
        uint256 passId = _attestPass(t);
        _challenge(passId);
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_WITHDRAW_CLEAN);

        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassVoided(passId, poolChallenger, PASS_BOND);
        pool.resolvePass(passId, atts, sigs);

        assertEq(uint8(pool.getPass(passId).state), uint8(IncentivePool.PassState.VOID));
        assertEq(pool.withdrawable(poolChallenger), PASS_BOND + POOL_CHALLENGER_BOND);
        assertEq(pool.withdrawable(relayer), 0);
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.counted, 0);
        assertEq(c.pending, 0);
        assertEq(_balance(), POOL_VALUE);
        // a VOID pass frees its scene: a corrected attestation of the same scene is accepted
        t.sceneHash = keccak256("corrected bundle");
        _attestPass(t);
    }

    function test_resolvePass_bindsSceneAndClaim() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_WITHDRAW_CLEAN);
        atts[0].sceneId = keccak256("another scene");
        atts[1].sceneId = atts[0].sceneId;
        sigs[0] = _signCp(obsAPk, atts[0]);
        sigs[1] = _signCp(obsBPk, atts[1]);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 0));
        pool.resolvePass(passId, atts, sigs);

        (atts, sigs) = _resolution(passId, KIND_WITHDRAW_CLEAN);
        atts[0].sceneHash = keccak256("another bundle"); // spec-v2 §15 A2 audit (2): sceneHash is bound too
        atts[1].sceneHash = atts[0].sceneHash;
        sigs[0] = _signCp(obsAPk, atts[0]);
        sigs[1] = _signCp(obsBPk, atts[1]);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 0));
        pool.resolvePass(passId, atts, sigs);

        (atts, sigs) = _resolution(passId, KIND_WITHDRAW_CLEAN);
        atts[0].claimId = keccak256("another claim");
        atts[1].claimId = atts[0].claimId;
        sigs[0] = _signCp(obsAPk, atts[0]);
        sigs[1] = _signCp(obsBPk, atts[1]);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 0));
        pool.resolvePass(passId, atts, sigs);
    }

    function test_resolvePass_badKind_reverts() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_CLEAN);
        vm.expectRevert(IncentivePool.BadKind.selector);
        pool.resolvePass(passId, atts, sigs);

        // mixed verdicts in one batch
        (atts, sigs) = _resolution(passId, KIND_CONFIRM_CLEAN);
        atts[1].kind = KIND_WITHDRAW_CLEAN;
        sigs[1] = _signCp(obsBPk, atts[1]);
        vm.expectRevert(IncentivePool.BadKind.selector);
        pool.resolvePass(passId, atts, sigs);
    }

    function test_resolvePass_quorumNotMet_reverts() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_CONFIRM_CLEAN);
        CleanPass[] memory one = new CleanPass[](1);
        bytes[] memory oneSig = new bytes[](1);
        one[0] = atts[0];
        oneSig[0] = sigs[0];
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.QuorumNotMet.selector, 0, K));
        pool.resolvePass(passId, one, oneSig);
    }

    function test_resolvePass_afterResolveBy_reverts() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        vm.warp(block.timestamp + RESOLVE_WINDOW);
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_CONFIRM_CLEAN);
        vm.expectRevert(IncentivePool.WindowClosed.selector);
        pool.resolvePass(passId, atts, sigs);
    }

    function test_resolvePass_notChallenged_reverts() public {
        uint256 passId = _attestPass(_t());
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_CONFIRM_CLEAN);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.resolvePass(passId, atts, sigs);
    }

    /// P9 holds on the resolution path too: a CONFIRM cannot count a pass while a new detection is under review.
    function test_resolvePass_confirmWhileSourceUnderReview_reverts() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        (uint256 id,) = _relapseProposal();
        (CleanPass[] memory atts, bytes[] memory sigs) = _resolution(passId, KIND_CONFIRM_CLEAN);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id));
        pool.resolvePass(passId, atts, sigs);
    }

    // ================================================================== finalizePass

    /// R16 / D11: an unresolved challenge expires; both bonds go back; nobody is slashed; the scene is freed.
    function test_challengePass_unresolved_expires_noSlash() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        vm.warp(block.timestamp + RESOLVE_WINDOW);
        vm.expectEmit(true, false, false, true, address(pool));
        emit IncentivePool.CleanPassExpired(passId);
        vm.prank(stranger); // permissionless
        pool.finalizePass(passId);

        assertEq(uint8(pool.getPass(passId).state), uint8(IncentivePool.PassState.EXPIRED));
        assertEq(pool.withdrawable(relayer), PASS_BOND);
        assertEq(pool.withdrawable(poolChallenger), POOL_CHALLENGER_BOND);
        assertEq(pool.withdrawable(seller), 0);
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.counted, 0);
        assertEq(c.pending, 0);
    }

    function test_finalizePass_beforeResolveBy_reverts() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        vm.warp(block.timestamp + RESOLVE_WINDOW - 1);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.finalizePass(passId);
    }

    function test_finalizePass_notChallenged_reverts() public {
        uint256 passId = _attestPass(_t());
        vm.warp(block.timestamp + RESOLVE_WINDOW);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.finalizePass(passId);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.finalizePass(99);
    }

    // ================================================================== countPass

    function test_countPass_counts_returnsBond_paysObserverFee_emits() public {
        uint256 passId = _attestPass(_t());
        vm.warp(block.timestamp + CLEAN_WINDOW);

        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.ObserverFeePaid(passId, ctrlA, OBSERVER_FEE / 2);
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.ObserverFeePaid(passId, ctrlB, OBSERVER_FEE / 2);
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassCounted(claimId, passId, 1, 9750);
        vm.prank(stranger); // permissionless
        pool.countPass(passId);

        IncentivePool.Pass memory p = pool.getPass(passId);
        assertEq(uint8(p.state), uint8(IncentivePool.PassState.COUNTED));
        assertEq(p.bond, 0);
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.counted, 1);
        assertEq(c.pending, 0);
        assertEq(c.instrumentsSeen, 1 << 1);
        assertEq(pool.withdrawable(relayer), PASS_BOND);
        assertEq(pool.withdrawable(ctrlA), OBSERVER_FEE / 2);
        assertEq(pool.withdrawable(ctrlB), OBSERVER_FEE / 2);
        assertEq(_balance(), POOL_VALUE - OBSERVER_FEE);
    }

    function test_countPass_secondPass_secondInstrument_upperBound() public {
        uint256 p1 = _attestPass(_t());
        CleanPass memory t2 = _cp(claimId, SRC_TKM, sceneAt);
        t2.instrumentId = 2; // "emi"
        uint256 p2 = _attestPass(t2);
        vm.warp(block.timestamp + CLEAN_WINDOW);
        pool.countPass(p1);
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassCounted(claimId, p2, 2, 8418);
        pool.countPass(p2);
        assertEq(pool.getClaim(claimId).instrumentsSeen, (1 << 1) | (1 << 2));
    }

    /// The fee splits equally among the distinct signing orgs; an org without a controller leaves its share in the
    /// pool, and integer-division dust stays there too.
    function test_countPass_feeSplit_orgWithoutController_andDust() public {
        _setController(ORG_C, address(0));
        uint256 passId = _attestPassOn(pool, _t(), _pks(obsAPk, obsCPk, obsDPk));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        pool.countPass(passId);
        uint256 share = OBSERVER_FEE / 3;
        assertEq(pool.withdrawable(ctrlA), share);
        assertEq(pool.withdrawable(admin), share); // ORG_D's controller in the fixture
        assertEq(pool.withdrawable(ctrlC), 0);
        assertEq(_balance(), POOL_VALUE - 2 * share);
    }

    function test_countPass_feeSkippedWhenUnreservedShort() public {
        IncentivePool small = new IncentivePool(cov);
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        small.fund{value: OBSERVER_FEE - 1}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        bytes32 cid = small.openClaim(ep);
        uint256 passId = _attestPassOn(small, _cp(cid, SRC_TKM, sceneAt), _pks(obsAPk, obsBPk));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        small.countPass(passId);
        assertEq(small.getClaim(cid).counted, 1);
        assertEq(small.withdrawable(relayer), PASS_BOND);
        assertEq(small.withdrawable(ctrlA), 0);
        (IncentivePool.Pool memory pl,) = small.getPool(covId);
        assertEq(pl.balance, OBSERVER_FEE - 1);
    }

    function test_countPass_verdictOnly_noFee_bondReturned() public {
        IncentivePool vo = new IncentivePool(cov);
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        vo.fund(covId, POLICY_V2_HASH, IncentivePool.Mode.VERDICT_ONLY, p);
        bytes32 cid = vo.openClaim(ep);
        uint256 passId = _attestPassOn(vo, _cp(cid, SRC_TKM, sceneAt), _pks(obsAPk, obsBPk));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vo.countPass(passId);
        assertEq(vo.getClaim(cid).counted, 1);
        assertEq(vo.withdrawable(relayer), PASS_BOND); // the anti-spam deposit is ETH in both modes
        assertEq(vo.withdrawable(ctrlA), 0);
        assertEq(address(vo).balance, PASS_BOND);
    }

    function test_countPass_beforeWindow_reverts() public {
        uint256 passId = _attestPass(_t());
        vm.warp(block.timestamp + CLEAN_WINDOW - 1);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.countPass(passId);
    }

    function test_countPass_notPending_reverts() public {
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.countPass(99);

        uint256 passId = _attestPass(_t());
        _challenge(passId);
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.countPass(passId); // CHALLENGED

        uint256 p2 = _attestPass(_t());
        vm.warp(block.timestamp + CLEAN_WINDOW);
        pool.countPass(p2);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.countPass(p2); // repeat
    }

    /// P9 on countPass: a live or confirmed later detection at the source blocks counting.
    function test_countPass_sourceUnderReviewOrRelapse_reverts() public {
        uint256 passId = _attestPass(_t());
        (uint256 id,) = _relapseProposal();
        vm.warp(block.timestamp + CLEAN_WINDOW); // the pass's window is over; the core episode is still PROPOSED
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id));
        pool.countPass(passId);
        cov.finalize(id); // CONFIRMED: its deadline (proposal + challengeWindow = 1 day) has just been reached
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.RelapsePending.selector, id));
        pool.countPass(passId);
    }

    // ================================================================== closeClaim with open passes (N2)

    /// A claim with a PENDING pass cannot close, so sponsors cannot leave before the pass settles; once it does,
    /// every party withdraws exactly its due and the pool holds nothing.
    function test_closeClaim_pendingPass_reverts_bondsRecoverable() public {
        uint64 end = cov.getCovenant(covId).end;
        vm.warp(uint256(end) - 1 hours + PUBLICATION_LAG);
        uint256 passId = _attestPass(_cp(claimId, SRC_TKM, end - 1 hours));

        vm.warp(_closingAt());
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.closeClaim(claimId);
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSING));
        vm.prank(buyer);
        vm.expectRevert(IncentivePool.PoolNotClosed.selector);
        pool.sponsorWithdraw(covId);

        pool.countPass(passId); // counting continues in CLOSING
        pool.closeClaim(claimId);
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);

        address[4] memory parties = [buyer, relayer, ctrlA, ctrlB];
        for (uint256 i; i < 4; ++i) {
            vm.prank(parties[i]);
            pool.withdraw();
        }
        assertEq(address(pool).balance, 0);
    }

    function test_closeClaim_challengedPass_reverts_bondsRecoverable() public {
        uint256 passId = _attestPass(_t());
        _challenge(passId);
        vm.warp(_closingAt());
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.closeClaim(claimId);

        pool.finalizePass(passId); // resolveBy passed long ago
        pool.closeClaim(claimId);
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);
        address[3] memory parties = [buyer, relayer, poolChallenger];
        for (uint256 i; i < 3; ++i) {
            vm.prank(parties[i]);
            pool.withdraw();
        }
        assertEq(address(pool).balance, 0);
    }
}

/// @notice Installments and relapse (spec-v2 §3 "Installments", "Source under review / relapse", "Cross-provider corroboration",
///         "Settlement modes"; §9.7 R15, R17, R19, R22, R23, R27, R28; D1; §15 A1/A2 audits): installments, release,
///         relapse, voidPass, MARS corroboration and the final-installment gate, VERDICT_ONLY credit notes.
contract IncentivePoolInstallmentTest is PoolFixture {
    uint32 internal constant RATE = 2548; // tier 2: cap = TIER2 = 2 ether (ONCHAIN), 300 units (VERDICT_ONLY)
    uint256 internal constant POOL_VALUE = 10 ether;
    uint32 internal constant SHORT_CURE = 100 days; // lets the final tranche fit the fixture's one-year covenant
    uint256 internal constant SHARE_0 = 0.6 ether; // 2 ether x 3000 bp
    uint256 internal constant SHARE_2 = 0.8 ether; // 2 ether x 4000 bp
    uint64 internal constant GRACE = PUBLICATION_LAG + CHALLENGE_WINDOW + CONTEST_WINDOW; // relapseGrace, 63 d

    uint64 internal detected;
    uint256 internal ep;
    bytes32 internal claimId;
    uint64 internal since;
    uint64 internal sceneBase;
    uint64 private _sceneMinute;

    function setUp() public override {
        super.setUp();
        detected = uint64(block.timestamp + 5 days);
        vm.warp(block.timestamp + 10 days);
        IncentivePool.PoolParams memory p = _shortCureParams();
        vm.prank(buyer);
        pool.fund{value: POOL_VALUE}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        ep = _confirmed(SRC_TKM, RATE, detected);
        claimId = pool.openClaim(ep);
        since = detected + LINK_WINDOW;
        sceneBase = since + 1 days;
        vm.warp(uint256(sceneBase) + PUBLICATION_LAG + 1 days); // every scene in [sceneBase, sceneBase + 1 d] is attestable
    }

    // ------------------------------------------------------------------ helpers

    function _shortCureParams() internal pure returns (IncentivePool.PoolParams memory p) {
        p = _validParams();
        p.cureTerm = SHORT_CURE;
    }

    function _scene(bytes32 cid, uint8 instrument) internal returns (CleanPass memory t) {
        t = _cp(cid, SRC_TKM, sceneBase + (++_sceneMinute) * 1 minutes);
        t.instrumentId = instrument;
    }

    /// @notice Attests `n` passes (orgs A + B) on `target` for `cid`, waits the clean window, counts them all.
    function _countNOn(IncentivePool target, bytes32 cid, uint256 n, uint8 instrument) internal returns (uint256 last) {
        uint256[] memory ids = new uint256[](n);
        for (uint256 i; i < n; ++i) {
            ids[i] = _attestPassOn(target, _scene(cid, instrument), _pks(obsAPk, obsBPk));
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        for (uint256 i; i < n; ++i) {
            target.countPass(ids[i]);
        }
        return n == 0 ? 0 : ids[n - 1];
    }

    function _countN(uint256 n, uint8 instrument) internal {
        _countNOn(pool, claimId, n, instrument);
    }

    function _inst(uint256 id) internal view returns (IncentivePool.Installment memory) {
        return pool.getInstallment(id);
    }

    function _relapseProposal() internal returns (uint256 id, bytes32 ev) {
        ev = _nextEvidence();
        id = _propose(SRC_TKM, ev, RATE, since + 10 days);
    }

    function _confirmedRelapse() internal returns (uint256 id) {
        (id,) = _relapseProposal();
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
    }

    function _poolBalances(IncentivePool target) internal view returns (uint256 balance, uint256 reserved) {
        (IncentivePool.Pool memory pl,) = target.getPool(covId);
        return (pl.balance, pl.reserved);
    }

    function _openOther(IncentivePool.Mode mode, uint256 value, IncentivePool.PoolParams memory p)
        internal
        returns (IncentivePool target, bytes32 cid)
    {
        target = new IncentivePool(cov);
        vm.prank(buyer);
        target.fund{value: value}(covId, POLICY_V2_HASH, mode, p);
        cid = target.openClaim(ep);
    }

    // ================================================================== scheduling

    function test_installment_scheduledAtThreshold_emits() public {
        _countN(2, 1);
        assertEq(_inst(1).claimId, bytes32(0)); // nothing before the third pass

        uint256 pid = _attestPass(_scene(claimId, 1));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        uint64 due = uint64(block.timestamp) + HOLD_WINDOW;
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.InstallmentScheduled(claimId, 1, 0, SHARE_0, due);
        pool.countPass(pid);

        IncentivePool.Installment memory i0 = _inst(1);
        assertEq(i0.claimId, claimId);
        assertEq(i0.index, 0);
        assertEq(i0.amount, SHARE_0);
        assertEq(i0.claimableAt, due);
        assertEq(uint8(i0.state), uint8(IncentivePool.InstState.PENDING));
        assertEq(pool.getClaim(claimId).reserved, SHARE_0);
        (, uint256 reserved) = _poolBalances(pool);
        assertEq(reserved, SHARE_0);

        _countN(3, 1); // six counted: the second installment
        assertEq(_inst(2).index, 1);
        assertEq(_inst(2).amount, SHARE_0);
        assertEq(pool.getClaim(claimId).reserved, 2 * SHARE_0);
    }

    /// R22: an installment the unreserved balance cannot cover waits UNFUNDED and is scheduled by the next fund.
    function test_installment_waitsWhenPoolShort() public {
        (IncentivePool small, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        vm.recordLogs();
        _countNOn(small, cid, 3, 1);
        IncentivePool.Installment memory i0 = small.getInstallment(1);
        assertEq(uint8(i0.state), uint8(IncentivePool.InstState.UNFUNDED));
        assertEq(i0.amount, SHARE_0);
        assertEq(i0.claimableAt, 0);
        assertEq(small.getClaim(cid).reserved, 0);

        IncentivePool.PoolParams memory p = _validParams();
        vm.expectEmit(true, true, false, true, address(small));
        emit IncentivePool.InstallmentScheduled(cid, 1, 0, SHARE_0, uint64(block.timestamp) + HOLD_WINDOW);
        vm.prank(sponsor);
        small.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(uint8(small.getInstallment(1).state), uint8(IncentivePool.InstState.PENDING));
        (uint256 balance, uint256 reserved) = _poolBalances(small);
        assertEq(reserved, SHARE_0);
        assertLe(reserved, balance); // P1
    }

    function test_installment_unfundedEmitsEvent() public {
        (IncentivePool small, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        _countNOn(small, cid, 2, 1);
        uint256 pid = _attestPassOn(small, _scene(cid, 1), _pks(obsAPk, obsBPk));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.expectEmit(true, true, false, true, address(small));
        emit IncentivePool.InstallmentUnfunded(cid, 1, SHARE_0);
        small.countPass(pid);
    }

    /// FIFO: a top-up schedules queued installments in order and stops at the first it cannot cover.
    function test_installment_unfundedQueue_fifo() public {
        (IncentivePool small, bytes32 cidT) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        uint256 ep2 = _confirmed(SRC_DZA, RATE, detected + 1 days);
        bytes32 cidD = small.openClaim(ep2);
        vm.warp(block.timestamp + 1 days);
        _countNOn(small, cidT, 3, 1); // installment 1 (TKM) UNFUNDED
        // DZA scenes: same template, other source
        uint256[] memory ids = new uint256[](3);
        for (uint256 i; i < 3; ++i) {
            CleanPass memory t = _scene(cidD, 1);
            t.sourceId = SRC_DZA;
            ids[i] = _attestPassOn(small, t, _pks(obsAPk, obsBPk));
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        for (uint256 i; i < 3; ++i) {
            small.countPass(ids[i]);
        }
        assertEq(uint8(small.getInstallment(2).state), uint8(IncentivePool.InstState.UNFUNDED)); // DZA

        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        small.fund{value: 0.2 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p); // covers one, not two
        assertEq(uint8(small.getInstallment(1).state), uint8(IncentivePool.InstState.PENDING));
        assertEq(uint8(small.getInstallment(2).state), uint8(IncentivePool.InstState.UNFUNDED));
        vm.prank(sponsor);
        small.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(uint8(small.getInstallment(2).state), uint8(IncentivePool.InstState.PENDING));
    }

    /// Review focus 4: a top-up during CLOSING schedules a waiting installment; the claim closes after its release.
    function test_installment_unfundedThenFundDuringClosing_schedules() public {
        (IncentivePool small, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        _countNOn(small, cid, 3, 1);
        uint256 closingAt = _closingAt();
        vm.warp(closingAt);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        small.closeClaim(cid); // an UNFUNDED installment keeps the claim open

        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        small.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        small.closeClaim(cid); // now PENDING: still open until released
        vm.warp(block.timestamp + HOLD_WINDOW);
        small.release(1);
        small.closeClaim(cid);
        (IncentivePool.Pool memory pl,) = small.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.CLOSED));
    }

    function test_installment_verdictOnly_scheduledWithoutFunds() public {
        (IncentivePool vo, bytes32 cid) = _openOther(IncentivePool.Mode.VERDICT_ONLY, 0, _validParams());
        _countNOn(vo, cid, 3, 1);
        IncentivePool.Installment memory i0 = vo.getInstallment(1);
        assertEq(uint8(i0.state), uint8(IncentivePool.InstState.PENDING));
        assertEq(i0.amount, 90); // 300 units x 3000 bp
        assertEq(vo.getClaim(cid).reserved, 90);
        (, uint256 reserved) = _poolBalances(vo);
        assertEq(reserved, 0); // units never enter the ETH accounting
    }

    /// Review focus 3: a share that rounds to 0 units is still a tracked installment (it blocks closeClaim until it
    /// is released as a 0-unit credit note).
    function test_installment_verdictOnlyRoundsToZero_stillScheduled() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.unitTierAmounts = [uint128(1), 1, 1];
        (IncentivePool vo, bytes32 cid) = _openOther(IncentivePool.Mode.VERDICT_ONLY, 0, p);
        _countNOn(vo, cid, 3, 1);
        IncentivePool.Installment memory i0 = vo.getInstallment(1);
        assertEq(uint8(i0.state), uint8(IncentivePool.InstState.PENDING));
        assertEq(i0.amount, 0);
        vm.warp(_closingAt());
        vm.expectRevert(IncentivePool.InvalidState.selector);
        vo.closeClaim(cid);
    }

    // ================================================================== final installment: instruments, corroboration, term

    /// R28 / P2: ten counted passes from two instruments do not schedule the final share until a k-of-n MARS
    /// corroboration covers the triggering pass; then it is scheduled for after the cure term plus grace.
    function test_final_requiresCorroboration() public {
        _countN(5, 1);
        _countN(5, 2);
        assertEq(pool.getClaim(claimId).counted, 10);
        assertEq(_inst(3).claimId, bytes32(0)); // no final yet

        uint64 due = since + SHORT_CURE + GRACE; // later than now + holdWindow here
        assertGt(due, block.timestamp + HOLD_WINDOW);
        uint64 to = uint64(block.timestamp);
        _corroborate(pool, _corr(claimId, SRC_TKM, since, to));
        assertEq(_inst(3).claimId, bytes32(0)); // pending: not yet effective

        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.expectEmit(true, false, false, true, address(pool));
        emit IncentivePool.ClaimCorroborated(claimId, keccak256("mars snapshot [SIM test value]"), since, to);
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.InstallmentScheduled(claimId, 3, 2, SHARE_2, due);
        vm.prank(stranger); // permissionless
        pool.finalizeCorroboration(claimId);

        assertEq(pool.getClaim(claimId).corroboratedTo, to);
        assertEq(_inst(3).index, 2);
        assertEq(pool.getClaim(claimId).reserved, 2 * SHARE_0 + SHARE_2); // the whole cap, never more (P1)
    }

    function test_final_requiresTwoInstruments() public {
        _countN(10, 1);
        _corroborateEffective(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        assertEq(_inst(3).claimId, bytes32(0)); // one instrument only
        _countN(1, 2); // eleventh pass, second instrument: the final is scheduled at once (already corroborated)
        assertEq(_inst(3).index, 2);
        assertEq(_inst(3).amount, SHARE_2);
    }

    /// A corroboration that predates the triggering pass does not cover it.
    function test_final_staleCorroboration_doesNotSchedule() public {
        _countN(5, 1);
        _corroborateEffective(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        vm.warp(block.timestamp + 1 days);
        // later scenes, acquired after the corroborated window
        uint64 later = uint64(block.timestamp);
        vm.warp(uint256(later) + 10 minutes + PUBLICATION_LAG);
        uint256[] memory ids = new uint256[](5);
        for (uint256 i; i < 5; ++i) {
            CleanPass memory t = _cp(claimId, SRC_TKM, later + uint64(i + 1) * 1 minutes);
            t.instrumentId = 2;
            ids[i] = _attestPass(t);
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        for (uint256 i; i < 5; ++i) {
            pool.countPass(ids[i]);
        }
        assertEq(_inst(3).claimId, bytes32(0));
        _corroborateEffective(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        assertEq(_inst(3).index, 2);
    }

    /// R27: a final tranche whose cure term plus grace outlives `end + publicationLag` is never scheduled.
    function test_finalInstallment_outOfTerm_notScheduled() public {
        (IncentivePool longCure, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 10 ether, _validParams());
        _countNOn(longCure, cid, 5, 1);
        _countNOn(longCure, cid, 4, 2);
        uint256 pid = _attestPassOn(longCure, _scene(cid, 2), _pks(obsAPk, obsBPk));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.expectEmit(true, false, false, true, address(longCure));
        emit IncentivePool.FinalOutOfTerm(cid);
        longCure.countPass(pid);
        _corroborateEffective(longCure, _corr(cid, SRC_TKM, since, uint64(block.timestamp)));
        assertEq(longCure.getInstallment(3).claimId, bytes32(0));
        assertEq(longCure.getClaim(cid).reserved, 2 * SHARE_0); // capped at the non-final shares
    }

    // ================================================================== release

    function test_release_beforeClaimable_reverts() public {
        _countN(3, 1);
        vm.warp(_inst(1).claimableAt - 1);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.release(1);
    }

    /// R23: after the hold window, with no later detection at the source, the share goes to the seller (pull).
    function test_release_afterHold_beforeRelapse() public {
        _countN(3, 1);
        (uint256 balanceBefore,) = _poolBalances(pool);
        vm.warp(_inst(1).claimableAt);
        vm.expectEmit(true, false, false, true, address(pool));
        emit IncentivePool.InstallmentReleased(1, SHARE_0);
        vm.prank(stranger); // permissionless
        pool.release(1);

        assertEq(uint8(_inst(1).state), uint8(IncentivePool.InstState.RELEASED));
        assertEq(pool.withdrawable(seller), SHARE_0);
        IncentivePool.Claim memory c = pool.getClaim(claimId);
        assertEq(c.paid, SHARE_0);
        assertEq(c.reserved, 0);
        (uint256 balance, uint256 reserved) = _poolBalances(pool);
        assertEq(balance, balanceBefore - SHARE_0);
        assertEq(reserved, 0);
    }

    function test_release_relapsePending_reverts() public {
        _countN(3, 1);
        uint256 id = _confirmedRelapse();
        vm.warp(_inst(1).claimableAt);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.RelapsePending.selector, id));
        pool.release(1);
    }

    /// R19: a live detection at the source freezes release.
    function test_release_blockedWhileEpisodeLive() public {
        _countN(3, 1);
        vm.warp(_inst(1).claimableAt);
        (uint256 id,) = _relapseProposal();
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id));
        pool.release(1);
    }

    /// spec-v2 §15 C3 audit (1), §8.9 g: the check order is state -> later detection -> claimableAt. A PENDING
    /// installment under a live relapse reverts `SourceUnderReview` even before its window opens (the freeze is the
    /// visible reason, not the clock); a confirmed one reverts `RelapsePending`.
    function test_release_liveRelapseBeforeClaimable_reportsFreezeFirst() public {
        _countN(3, 1);
        (uint256 id,) = _relapseProposal();
        assertLt(block.timestamp, _inst(1).claimableAt); // the window is still closed
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id));
        pool.release(1);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(id);
        assertLt(block.timestamp, _inst(1).claimableAt);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.RelapsePending.selector, id));
        pool.release(1);
    }

    function test_release_verdictOnly_emitsCreditNoteDue_noFunds() public {
        (IncentivePool vo, bytes32 cid) = _openOther(IncentivePool.Mode.VERDICT_ONLY, 0, _validParams());
        _countNOn(vo, cid, 3, 1);
        vm.warp(vo.getInstallment(1).claimableAt);
        bytes32 ref = keccak256(abi.encode(block.chainid, address(vo), uint256(1)));
        vm.expectEmit(true, true, false, true, address(vo));
        emit IncentivePool.CreditNoteDue(cid, 1, 90, ref);
        vo.release(1);
        assertEq(vo.getInstallment(1).ref, ref);
        assertEq(vo.getClaim(cid).paid, 90);
        assertEq(vo.withdrawable(seller), 0); // a verdict, not a payment
    }

    function test_release_notPending_reverts() public {
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.release(1); // unknown
        _countN(3, 1);
        vm.warp(_inst(1).claimableAt);
        pool.release(1);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.release(1); // repeat

        (IncentivePool small, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        _countNOn(small, cid, 3, 1);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        small.release(1); // UNFUNDED
    }

    // ================================================================== relapse

    function test_relapse_cancelsClaim_voidsInstallments_releasesReserve() public {
        _countN(6, 1);
        uint256 id = _confirmedRelapse();
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.ClaimCancelled(claimId, id, 2 * SHARE_0);
        vm.prank(stranger); // any watcher
        pool.relapse(claimId, id);

        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.CANCELLED));
        assertEq(uint8(_inst(1).state), uint8(IncentivePool.InstState.VOID));
        assertEq(uint8(_inst(2).state), uint8(IncentivePool.InstState.VOID));
        assertEq(pool.getClaim(claimId).reserved, 0);
        (, uint256 reserved) = _poolBalances(pool);
        assertEq(reserved, 0);
    }

    /// D1: a released installment stays released after a relapse; only what is still pending is voided.
    function test_relapse_releasedInstallmentStays() public {
        _countN(3, 1);
        vm.warp(_inst(1).claimableAt);
        pool.release(1);
        _countN(3, 1); // installment 2 pending
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);
        assertEq(uint8(_inst(1).state), uint8(IncentivePool.InstState.RELEASED));
        assertEq(uint8(_inst(2).state), uint8(IncentivePool.InstState.VOID));
        assertEq(pool.getClaim(claimId).paid, SHARE_0);
        assertEq(pool.withdrawable(seller), SHARE_0);
    }

    function test_relapse_voidsUnfundedInstallment() public {
        (IncentivePool small, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        _countNOn(small, cid, 3, 1);
        uint256 id = _confirmedRelapse();
        small.relapse(cid, id);
        assertEq(uint8(small.getInstallment(1).state), uint8(IncentivePool.InstState.VOID));
        // a later top-up skips the voided queue entry
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        small.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(uint8(small.getInstallment(1).state), uint8(IncentivePool.InstState.VOID));
        (, uint256 reserved) = _poolBalances(small);
        assertEq(reserved, 0);
    }

    /// LINKED detections sit inside the episode's own window (detectedAt <= since): never a relapse.
    function test_relapse_linkedInsideWindow_reverts() public {
        uint256 linked = _propose(SRC_TKM, _nextEvidence(), RATE, detected + 1 days);
        assertEq(uint8(_state(linked)), uint8(MethaneCovenant.State.LINKED));
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotARelapse.selector, linked));
        pool.relapse(claimId, linked);
    }

    /// R15 (`test_relapse_linkedEpisodeCancels` in spec-v2 §9.7, renamed for what it shows): a later detection the core
    /// records as a new, non-LINKED episode whose `parentEpisode` is the claim's episode cancels the claim.
    function test_relapse_laterEpisodeWithParentPointer_cancels() public {
        uint256 id = _confirmedRelapse();
        assertEq(cov.getEpisode(id).parentEpisode, ep);
        pool.relapse(claimId, id);
        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.CANCELLED));
    }

    /// R19: a seller who contests a relapse to EXPIRED gains nothing.
    function test_relapse_expiredContestedEpisodeCancels() public {
        (uint256 id,) = _relapseProposal();
        vm.prank(seller);
        cov.contest(id);
        vm.warp(block.timestamp + CONTEST_WINDOW);
        cov.finalize(id);
        assertEq(uint8(_state(id)), uint8(MethaneCovenant.State.EXPIRED));
        pool.relapse(claimId, id);
        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.CANCELLED));
    }

    function test_relapse_voidEpisode_reverts() public {
        (uint256 id, bytes32 ev) = _relapseProposal();
        vm.prank(challenger);
        cov.challenge{value: CHALLENGER_BOND}(id, 1);
        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(obsAPk, obsBPk), SRC_TKM, ev, RATE, since + 10 days, KIND_WITHDRAW);
        cov.submitResolution(id, atts, sigs);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotARelapse.selector, id));
        pool.relapse(claimId, id);
    }

    function test_relapse_otherSourceOrUnknown_reverts() public {
        uint256 other = _propose(SRC_DZA, _nextEvidence(), RATE, since + 10 days);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(other);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotARelapse.selector, other));
        pool.relapse(claimId, other);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotARelapse.selector, 999));
        pool.relapse(claimId, 999);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotARelapse.selector, ep));
        pool.relapse(claimId, ep); // the claim's own episode
    }

    function test_relapse_detectedBeforeSince_reverts() public {
        uint256 id = _propose(SRC_TKM, _nextEvidence(), RATE, since - 1 days); // LINKED to the claim's episode
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.NotARelapse.selector, id));
        pool.relapse(claimId, id);
    }

    function test_relapse_liveEpisode_reverts() public {
        (uint256 id,) = _relapseProposal();
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id));
        pool.relapse(claimId, id);
    }

    function test_relapse_notOpenClaim_reverts() public {
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);
        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.relapse(claimId, id); // repeat
        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.relapse(keccak256("no such claim"), id);
    }

    function test_relapse_newClaimOnRelapseEpisode() public {
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);
        bytes32 next = pool.openClaim(id);
        IncentivePool.Claim memory c = pool.getClaim(next);
        assertEq(c.episodeId, id);
        assertEq(c.since, since + 10 days + LINK_WINDOW);
    }

    /// R17: cancelling costs the same with 0 or 32 pending passes (passes are voided lazily, one call each).
    function test_relapse_gasIndependentOfPendingPasses() public {
        uint256 snap = vm.snapshotState();
        uint256 id = _confirmedRelapse();
        uint256 g0 = gasleft();
        pool.relapse(claimId, id);
        uint256 gasNone = g0 - gasleft();
        vm.revertToState(snap);

        uint256 max = pool.MAX_PENDING();
        for (uint256 i; i < max; ++i) {
            _attestPass(_scene(claimId, 1)); // before the relapse exists: P9 would refuse them afterwards
        }
        assertEq(pool.getClaim(claimId).pending, max);
        id = _confirmedRelapse();
        g0 = gasleft();
        pool.relapse(claimId, id);
        uint256 gasFull = g0 - gasleft();

        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.CANCELLED));
        assertApproxEqRel(gasFull, gasNone, 0.05e18); // within 5 %
        assertLt(gasFull, 150_000);
    }

    // ================================================================== voidPass and the cancelled claim

    function test_voidPass_onCancelledClaim_returnsBonds() public {
        uint256 p1 = _attestPass(_scene(claimId, 1));
        uint256 p2 = _attestPass(_scene(claimId, 1));
        uint8 reason = pool.REASON_PLUME_ON_SCENE();
        vm.prank(poolChallenger);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(p2, reason);
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);

        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CleanPassVoided(p1, relayer, PASS_BOND);
        vm.prank(stranger);
        pool.voidPass(p1);
        pool.voidPass(p2);
        assertEq(uint8(pool.getPass(p1).state), uint8(IncentivePool.PassState.VOID));
        assertEq(uint8(pool.getPass(p2).state), uint8(IncentivePool.PassState.VOID));
        assertEq(pool.withdrawable(relayer), 2 * PASS_BOND);
        assertEq(pool.withdrawable(poolChallenger), POOL_CHALLENGER_BOND);
        assertEq(pool.getClaim(claimId).pending, 0);
    }

    function test_voidPass_openClaimOrEndedPass_reverts() public {
        uint256 p1 = _attestPass(_scene(claimId, 1));
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.voidPass(p1); // claim OPEN
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.voidPass(99);
        vm.warp(block.timestamp + CLEAN_WINDOW);
        pool.countPass(p1);
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.voidPass(p1); // COUNTED
    }

    /// §15 A2 audit (3): the shared count path refuses a cancelled claim (countPass and a CONFIRM resolvePass).
    function test_countPass_cancelledClaim_reverts() public {
        uint256 p1 = _attestPass(_scene(claimId, 1));
        uint256 p2 = _attestPass(_scene(claimId, 1));
        uint8 reason = pool.REASON_PLUME_ON_SCENE();
        vm.prank(poolChallenger);
        pool.challengePass{value: POOL_CHALLENGER_BOND}(p2, reason);
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);

        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.countPass(p1);

        IncentivePool.Pass memory p = pool.getPass(p2);
        CleanPass memory t = _cp(claimId, SRC_TKM, p.sceneAt);
        t.sceneId = p.sceneId;
        t.sceneHash = p.sceneHash;
        t.publishedAt = p.publishedAt;
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), t, KIND_CONFIRM_CLEAN);
        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.resolvePass(p2, atts, sigs);
    }

    /// A cancelled claim no longer holds the pool open: CLOSED at closing time even with passes still to void.
    function test_relapse_poolClosesWithPassesToVoid() public {
        uint256 p1 = _attestPass(_scene(claimId, 1));
        uint256 id = _confirmedRelapse();
        pool.relapse(claimId, id);
        vm.warp(_closingAt());
        assertEq(uint8(_poolState()), uint8(IncentivePool.PoolState.CLOSED));
        vm.prank(buyer);
        pool.sponsorWithdraw(covId);
        pool.voidPass(p1);
        vm.prank(buyer);
        pool.withdraw();
        vm.prank(relayer);
        pool.withdraw();
        assertEq(address(pool).balance, 0);
    }

    // ================================================================== attestCorroboration

    /// §15 A3 audit (3): a corroboration is pending for `cleanWindow`, then anyone makes it effective.
    function test_corroboration_pendingThenEffective_emits() public {
        _countN(3, 1);
        uint64 to = uint64(block.timestamp);
        vm.expectEmit(true, false, false, true, address(pool));
        emit IncentivePool.CorroborationPending(
            claimId, keccak256("mars snapshot [SIM test value]"), uint64(block.timestamp) + CLEAN_WINDOW
        );
        vm.prank(stranger);
        _corroborate(pool, _corr(claimId, SRC_TKM, since, to));
        assertEq(pool.getClaim(claimId).corroboratedTo, 0);

        vm.warp(block.timestamp + CLEAN_WINDOW - 1);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.finalizeCorroboration(claimId);

        vm.warp(block.timestamp + 1);
        vm.expectEmit(true, false, false, true, address(pool));
        emit IncentivePool.ClaimCorroborated(claimId, keccak256("mars snapshot [SIM test value]"), since, to);
        vm.prank(stranger);
        pool.finalizeCorroboration(claimId);
        assertEq(pool.getClaim(claimId).corroboratedTo, to);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.finalizeCorroboration(claimId); // nothing pending any more
    }

    /// R28: observers cannot corroborate a window in which their own snapshot counts a MARS plume.
    function test_corroboration_marsPlume_reverts() public {
        Corroboration memory t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp));
        t.marsPlumesInWindow = 1;
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.MarsPlumeInWindow.selector);
        pool.attestCorroboration(claimId, atts, sigs);
    }

    function test_corroboration_window_reverts() public {
        _countN(3, 1);
        uint64 latest = pool.getPass(3).sceneAt;
        Corroboration memory t = _corr(claimId, SRC_TKM, since + 1, uint64(block.timestamp)); // starts after since
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.SceneOutOfRange.selector);
        pool.attestCorroboration(claimId, atts, sigs);

        t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp + 1)); // ends in the future
        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.SceneOutOfRange.selector);
        pool.attestCorroboration(claimId, atts, sigs);

        t = _corr(claimId, SRC_TKM, since, latest - 1); // does not reach the latest counted pass
        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.NotCorroborated.selector);
        pool.attestCorroboration(claimId, atts, sigs);

        _corroborate(pool, _corr(claimId, SRC_TKM, since, latest)); // exactly the latest counted scene is enough
    }

    function test_corroboration_notExtending_reverts() public {
        uint64 to = uint64(block.timestamp);
        _corroborateEffective(pool, _corr(claimId, SRC_TKM, since, to));
        Corroboration memory t = _corr(claimId, SRC_TKM, since, to);
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.attestCorroboration(claimId, atts, sigs);
    }

    function test_corroboration_kindOrProvider_reverts() public {
        Corroboration memory t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp));
        t.kind = KIND_CLEAN;
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.BadKind.selector);
        pool.attestCorroboration(claimId, atts, sigs);

        t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp));
        t.providerId = PROVIDER_CM; // a Carbon Mapper negative is not a second provider
        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.BadKind.selector);
        pool.attestCorroboration(claimId, atts, sigs);
    }

    function test_corroboration_attestationMismatch_reverts() public {
        for (uint256 field; field < 6; ++field) {
            (Corroboration[] memory atts, bytes[] memory sigs) =
                _corrBatchFor(pool, _pks(obsAPk, obsBPk), _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
            if (field == 0) atts[1].claimId = keccak256("other");
            else if (field == 1) atts[1].sourceId = SRC_DZA;
            else if (field == 2) atts[1].marsSnapshotHash = keccak256("other snapshot");
            else if (field == 3) atts[1].windowFrom -= 1;
            else if (field == 4) atts[1].windowTo -= 1;
            else atts[1].providerId = "unep-mars-2";
            sigs[1] = _signCorrOn(obsBPk, atts[1], address(pool));
            vm.expectRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 1));
            pool.attestCorroboration(claimId, atts, sigs);
        }
    }

    function test_corroboration_signerRules_reverts() public {
        Corroboration memory t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp));
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, sellerObsPk), t);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SelfAttestation.selector, sellerObs));
        pool.attestCorroboration(claimId, atts, sigs);

        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsA2Pk), t);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.QuorumNotMet.selector, 1, K));
        pool.attestCorroboration(claimId, atts, sigs);

        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        atts[0].marsSnapshotHash = keccak256("edited after signing");
        atts[1].marsSnapshotHash = atts[0].marsSnapshotHash;
        vm.expectRevert(IncentivePool.InvalidSignature.selector);
        pool.attestCorroboration(claimId, atts, sigs);

        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        atts[0].expiry = uint64(block.timestamp - 1);
        sigs[0] = _signCorrOn(obsAPk, atts[0], address(pool));
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.Expired.selector, atts[0].expiry));
        pool.attestCorroboration(claimId, atts, sigs);
    }

    function test_corroboration_claimNotOpen_reverts() public {
        Corroboration memory t = _corr(keccak256("no such claim"), SRC_TKM, since, uint64(block.timestamp));
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.ClaimNotOpen.selector);
        pool.attestCorroboration(t.claimId, atts, sigs);
    }

    // ================================================================== closeClaim with installments

    function test_closeClaim_pendingInstallment_reverts() public {
        _countN(3, 1);
        vm.warp(_closingAt());
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.closeClaim(claimId);
        pool.release(1); // claimableAt (count + 64 d) is long past
        pool.closeClaim(claimId);
    }

    // ================================================================== A3b: corroboration challenge (§15 A3 audit (3))

    function _challengeCorr(bytes32 cid, IncentivePool target) internal {
        uint8 reason = target.REASON_MARS_PLUME_NEARBY(); // read first: an external call would consume the prank
        vm.prank(poolChallenger);
        target.challengeCorroboration{value: POOL_CHALLENGER_BOND}(cid, reason);
    }

    function test_corroboration_secondWhilePending_reverts() public {
        uint64 to = uint64(block.timestamp);
        _corroborate(pool, _corr(claimId, SRC_TKM, since, to));
        vm.warp(block.timestamp + 1 hours);
        Corroboration memory t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp));
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.attestCorroboration(claimId, atts, sigs); // one pending at a time
    }

    /// §15 A3 audit (3) item 6: P9 also guards corroboration.
    function test_corroboration_sourceUnderReview_reverts() public {
        (uint256 id,) = _relapseProposal();
        Corroboration memory t = _corr(claimId, SRC_TKM, since, uint64(block.timestamp));
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.SourceUnderReview.selector, id));
        pool.attestCorroboration(claimId, atts, sigs);
    }

    function test_finalizeCorroboration_nothingPending_reverts() public {
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.finalizeCorroboration(claimId);
    }

    /// A challenge holds the pending corroboration: it does not become effective at the end of the clean window.
    function test_challengeCorroboration_suspends() public {
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.CorroborationChallenged(claimId, poolChallenger, uint64(block.timestamp) + RESOLVE_WINDOW);
        _challengeCorr(claimId, pool);
        assertEq(address(pool).balance, POOL_VALUE + POOL_CHALLENGER_BOND);
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.finalizeCorroboration(claimId); // challenged: waits for an answer until resolveWindow ends
        assertEq(pool.getClaim(claimId).corroboratedTo, 0);
        (uint256 balance,) = _poolBalances(pool);
        assertEq(balance, POOL_VALUE); // the challenger's bond is not sponsor money
    }

    function test_challengeCorroboration_invalidReason_reverts() public {
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        for (uint8 r; r < 8; ++r) {
            if (r == 5) continue;
            vm.prank(poolChallenger);
            vm.expectRevert(IncentivePool.InvalidReason.selector);
            pool.challengeCorroboration{value: POOL_CHALLENGER_BOND}(claimId, r);
        }
    }

    function test_challengeCorroboration_badBond_reverts() public {
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.BadBond.selector);
        pool.challengeCorroboration{value: POOL_CHALLENGER_BOND - 1}(claimId, 5);
    }

    function test_challengeCorroboration_afterWindow_reverts() public {
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.WindowClosed.selector);
        pool.challengeCorroboration{value: POOL_CHALLENGER_BOND}(claimId, 5);
    }

    function test_challengeCorroboration_nothingPending_reverts() public {
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.challengeCorroboration{value: POOL_CHALLENGER_BOND}(claimId, 5);

        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        _challengeCorr(claimId, pool);
        vm.prank(poolChallenger);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.challengeCorroboration{value: POOL_CHALLENGER_BOND}(claimId, 5); // already challenged
    }

    /// A fresh k-org attestation of the same snapshot and window answers the challenge: effective at once, the
    /// failed challenger's bond goes to the seller, and a due final installment is scheduled.
    function test_challengeCorroboration_reattested_effective_bondToSeller() public {
        _countN(5, 1);
        _countN(5, 2);
        uint64 to = uint64(block.timestamp);
        Corroboration memory t = _corr(claimId, SRC_TKM, since, to);
        _corroborate(pool, t);
        _challengeCorr(claimId, pool);
        vm.warp(block.timestamp + 2 days);

        vm.expectEmit(true, false, false, true, address(pool));
        emit IncentivePool.ClaimCorroborated(claimId, t.marsSnapshotHash, since, to);
        vm.expectEmit(true, true, false, true, address(pool));
        emit IncentivePool.InstallmentScheduled(claimId, 3, 2, SHARE_2, since + SHORT_CURE + GRACE);
        _corroborate(pool, _corr(claimId, SRC_TKM, since, to));

        assertEq(pool.getClaim(claimId).corroboratedTo, to);
        assertEq(pool.withdrawable(seller), POOL_CHALLENGER_BOND);
        assertEq(pool.withdrawable(poolChallenger), 0);
        vm.expectRevert(IncentivePool.InvalidState.selector);
        pool.finalizeCorroboration(claimId); // resolved: nothing pending
    }

    function test_challengeCorroboration_reattestDifferentSnapshotOrWindow_reverts() public {
        uint64 to = uint64(block.timestamp);
        _corroborate(pool, _corr(claimId, SRC_TKM, since, to));
        _challengeCorr(claimId, pool);

        Corroboration memory t = _corr(claimId, SRC_TKM, since, to);
        t.marsSnapshotHash = keccak256("another snapshot");
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 0));
        pool.attestCorroboration(claimId, atts, sigs);

        t = _corr(claimId, SRC_TKM, since, to - 1);
        (atts, sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(abi.encodeWithSelector(IncentivePool.AttestationMismatch.selector, 0));
        pool.attestCorroboration(claimId, atts, sigs);
    }

    function test_challengeCorroboration_reattestAfterResolveBy_reverts() public {
        uint64 to = uint64(block.timestamp);
        _corroborate(pool, _corr(claimId, SRC_TKM, since, to));
        _challengeCorr(claimId, pool);
        vm.warp(block.timestamp + RESOLVE_WINDOW);
        Corroboration memory t = _corr(claimId, SRC_TKM, since, to);
        (Corroboration[] memory atts, bytes[] memory sigs) = _corrBatchFor(pool, _pks(obsAPk, obsBPk), t);
        vm.expectRevert(IncentivePool.WindowClosed.selector);
        pool.attestCorroboration(claimId, atts, sigs);
    }

    /// D11: an unanswered challenge drops the pending corroboration and refunds the challenger; nobody is slashed; a
    /// new corroboration may be attested afterwards.
    function test_challengeCorroboration_unanswered_dropped_refunded() public {
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        _challengeCorr(claimId, pool);
        vm.warp(block.timestamp + RESOLVE_WINDOW - 1);
        vm.expectRevert(IncentivePool.WindowOpen.selector);
        pool.finalizeCorroboration(claimId);

        vm.warp(block.timestamp + 1);
        vm.prank(stranger);
        pool.finalizeCorroboration(claimId);
        assertEq(pool.getClaim(claimId).corroboratedTo, 0);
        assertEq(pool.withdrawable(poolChallenger), POOL_CHALLENGER_BOND);
        assertEq(pool.withdrawable(seller), 0);

        _corroborateEffective(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        assertEq(pool.getClaim(claimId).corroboratedTo, block.timestamp - CLEAN_WINDOW);
    }

    // ================================================================== A3b audit N1: no final share on a claim that is not OPEN

    /// A relapse cancels the claim while a corroboration is PENDING; finalizing that corroboration afterwards must not
    /// schedule the final share on the cancelled claim (`afterCorroboration` checks the claim state).
    function test_finalizeCorroboration_afterRelapse_schedulesNothing() public {
        _countN(5, 1);
        _countN(5, 2); // the final's count and two-instrument rules are met; only the corroboration is missing
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        uint256 id = _confirmedRelapse(); // also lets the clean window pass
        pool.relapse(claimId, id);
        (, uint256 reservedBefore) = _poolBalances(pool);

        pool.finalizeCorroboration(claimId);
        assertEq(_inst(3).claimId, bytes32(0)); // no final installment
        (, uint256 reservedAfter) = _poolBalances(pool);
        assertEq(reservedAfter, reservedBefore);
        assertEq(pool.getClaim(claimId).reserved, 0);
    }

    /// A claim closed at the end of the pool's life, with a corroboration still PENDING: finalizing it later schedules
    /// nothing and reserves nothing.
    function test_finalizeCorroboration_afterClose_schedulesNothing() public {
        _countN(5, 1);
        _countN(5, 2);
        uint256 closingAt = _closingAt();
        vm.warp(closingAt - 1 hours);
        pool.release(1);
        pool.release(2); // the two non-final shares are paid, so the claim can close
        _corroborate(pool, _corr(claimId, SRC_TKM, since, uint64(block.timestamp)));
        vm.warp(closingAt);
        pool.closeClaim(claimId);
        (, uint256 reservedBefore) = _poolBalances(pool);

        vm.warp(block.timestamp + CLEAN_WINDOW);
        pool.finalizeCorroboration(claimId);
        assertEq(_inst(3).claimId, bytes32(0));
        (, uint256 reservedAfter) = _poolBalances(pool);
        assertEq(reservedAfter, reservedBefore);
        assertEq(uint8(pool.getClaim(claimId).state), uint8(IncentivePool.ClaimState.CLOSED));
    }

    // ================================================================== A3 audit notes

    /// The final share's `now + holdWindow` branch (a short cure term), released end to end: the seller is paid the
    /// whole cap and nothing stays reserved.
    function test_final_holdBranch_releasedEndToEnd() public {
        IncentivePool.PoolParams memory p = _validParams();
        p.cureTerm = 1 days; // since + cure + grace < now + holdWindow at scheduling time
        (IncentivePool quick, bytes32 cid) = _openOther(IncentivePool.Mode.ONCHAIN, 10 ether, p);
        _countNOn(quick, cid, 5, 1);
        _countNOn(quick, cid, 5, 2);
        _corroborate(quick, _corr(cid, SRC_TKM, since, uint64(block.timestamp)));
        vm.warp(block.timestamp + CLEAN_WINDOW);
        uint64 due = uint64(block.timestamp) + HOLD_WINDOW;
        assertGt(due, since + 1 days + GRACE);
        vm.expectEmit(true, true, false, true, address(quick));
        emit IncentivePool.InstallmentScheduled(cid, 3, 2, SHARE_2, due);
        quick.finalizeCorroboration(cid);

        vm.warp(due);
        quick.release(1);
        quick.release(2);
        vm.expectEmit(true, false, false, true, address(quick));
        emit IncentivePool.InstallmentReleased(3, SHARE_2);
        quick.release(3);
        IncentivePool.Claim memory c = quick.getClaim(cid);
        assertEq(c.paid, c.cap); // the whole rebate, never more
        assertEq(c.reserved, 0);
        assertEq(quick.withdrawable(seller), c.cap);
        (, uint256 reserved) = _poolBalances(quick);
        assertEq(reserved, 0);
    }

    /// FIFO across claims: a new, smaller installment does not skip an older UNFUNDED one, even when it alone would fit.
    function test_installment_doesNotSkipQueue() public {
        (IncentivePool small, bytes32 cidT) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        uint256 ep3 = _confirmed(SRC_DZA, 694, detected + 1 days); // tier 3: cap 1 ether, first share 0.3 ether
        bytes32 cidD = small.openClaim(ep3);
        vm.warp(block.timestamp + 1 days);
        _countNOn(small, cidT, 3, 1); // installment 1 (0.6 ether) UNFUNDED
        uint256[] memory ids = new uint256[](3);
        for (uint256 i; i < 3; ++i) {
            CleanPass memory t = _scene(cidD, 1);
            t.sourceId = SRC_DZA;
            t.rateFloorKgH = 100;
            ids[i] = _attestPassOn(small, t, _pks(obsAPk, obsBPk));
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        small.countPass(ids[0]);
        small.countPass(ids[1]);
        (uint256 balance, uint256 reserved) = _poolBalances(small);
        assertGe(balance - reserved, 0.3 ether + OBSERVER_FEE); // the new 0.3 ether share would fit on its own
        small.countPass(ids[2]);
        assertEq(uint8(small.getInstallment(2).state), uint8(IncentivePool.InstState.UNFUNDED)); // but waits its turn

        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(sponsor);
        small.fund{value: 1 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        assertEq(uint8(small.getInstallment(1).state), uint8(IncentivePool.InstState.PENDING));
        assertEq(uint8(small.getInstallment(2).state), uint8(IncentivePool.InstState.PENDING));
        assertLt(small.getInstallment(1).claimableAt, small.getInstallment(2).claimableAt + 1); // same top-up, in order
    }

    /// A queue whose head was voided by a relapse does not hold back a new installment that fits.
    function test_installment_voidedQueueHeadDoesNotBlock() public {
        (IncentivePool small, bytes32 cidT) = _openOther(IncentivePool.Mode.ONCHAIN, 0.5 ether, _validParams());
        uint256 ep3 = _confirmed(SRC_DZA, 694, detected + 1 days);
        bytes32 cidD = small.openClaim(ep3);
        vm.warp(block.timestamp + 1 days);
        _countNOn(small, cidT, 3, 1); // installment 1 UNFUNDED
        uint256 id = _confirmedRelapse();
        small.relapse(cidT, id); // voids installment 1 in the queue
        uint256[] memory ids = new uint256[](3);
        for (uint256 i; i < 3; ++i) {
            CleanPass memory t = _scene(cidD, 1);
            t.sourceId = SRC_DZA;
            ids[i] = _attestPassOn(small, t, _pks(obsAPk, obsBPk));
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        for (uint256 i; i < 3; ++i) {
            small.countPass(ids[i]);
        }
        assertEq(uint8(small.getInstallment(2).state), uint8(IncentivePool.InstState.PENDING));
    }
}

/// @notice The frozen surface of spec-v2 §3.1 (+ §15 additions) pinned: every function, event and error selector equals
///         the one computed from its canonical signature, and every struct still builds field by field in frozen
///         order and type. A changed signature or a moved field fails here (or fails to compile).
contract IncentivePoolSurfaceTest is Test {
    function _sel(string memory sig) internal pure returns (bytes4) {
        return bytes4(keccak256(bytes(sig)));
    }

    function test_surface_functions() public view {
        assertEq(
            IncentivePool.fund.selector,
            _sel(
                "fund(bytes32,bytes32,uint8,(uint32,uint32,uint32,uint32,uint32,uint96,uint96,uint96,uint128[3],(uint8,uint16,bool,bool)[3],uint16[32]))"
            )
        );
        assertEq(IncentivePool.sponsorWithdraw.selector, _sel("sponsorWithdraw(bytes32)"));
        assertEq(IncentivePool.closeClaim.selector, _sel("closeClaim(bytes32)"));
        assertEq(IncentivePool.openClaim.selector, _sel("openClaim(uint256)"));
        assertEq(
            IncentivePool.attestCleanPass.selector,
            _sel(
                "attestCleanPass(bytes32,(bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint8,bytes32,uint16,bool,uint32,uint32,uint8,uint64,uint64)[],bytes[])"
            )
        );
        assertEq(
            IncentivePool.attestCorroboration.selector,
            _sel(
                "attestCorroboration(bytes32,(bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint16,uint8,uint64,uint64)[],bytes[])"
            )
        );
        assertEq(IncentivePool.challengeCorroboration.selector, _sel("challengeCorroboration(bytes32,uint8)"));
        assertEq(IncentivePool.challengePass.selector, _sel("challengePass(uint256,uint8)"));
        assertEq(
            IncentivePool.resolvePass.selector,
            _sel(
                "resolvePass(uint256,(bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint8,bytes32,uint16,bool,uint32,uint32,uint8,uint64,uint64)[],bytes[])"
            )
        );
        assertEq(IncentivePool.countPass.selector, _sel("countPass(uint256)"));
        assertEq(IncentivePool.finalizePass.selector, _sel("finalizePass(uint256)"));
        assertEq(IncentivePool.voidPass.selector, _sel("voidPass(uint256)"));
        assertEq(IncentivePool.release.selector, _sel("release(uint256)"));
        assertEq(IncentivePool.relapse.selector, _sel("relapse(bytes32,uint256)"));
        assertEq(IncentivePool.withdraw.selector, _sel("withdraw()"));
        assertEq(IncentivePool.getPool.selector, _sel("getPool(bytes32)"));
        assertEq(IncentivePool.getClaim.selector, _sel("getClaim(bytes32)"));
        assertEq(IncentivePool.getPass.selector, _sel("getPass(uint256)"));
        assertEq(IncentivePool.getInstallment.selector, _sel("getInstallment(uint256)"));
        assertEq(
            IncentivePool.cleanPassDigest.selector,
            _sel(
                "cleanPassDigest((bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint8,bytes32,uint16,bool,uint32,uint32,uint8,uint64,uint64))"
            )
        );
        assertEq(
            IncentivePool.corroborationDigest.selector,
            _sel("corroborationDigest((bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint16,uint8,uint64,uint64))")
        );
        assertEq(IncentivePool.eip712Domain.selector, _sel("eip712Domain()"));
        // §15 ABI additions: A1 audit (contributions), A3 audit (finalizeCorroboration)
        assertEq(IncentivePool.finalizeCorroboration.selector, _sel("finalizeCorroboration(bytes32)"));
        assertEq(IncentivePool(payable(address(0))).contributions.selector, _sel("contributions(bytes32,address)"));
        assertEq(IncentivePool(payable(address(0))).withdrawable.selector, _sel("withdrawable(address)"));
    }

    function test_surface_events() public pure {
        assertEq(
            IncentivePool.PoolFunded.selector, keccak256("PoolFunded(bytes32,address,uint256,bytes32,uint8,bytes32)")
        );
        assertEq(IncentivePool.ClaimOpened.selector, keccak256("ClaimOpened(bytes32,uint256,bytes32,bytes32,uint256)"));
        assertEq(
            IncentivePool.CleanPassAttested.selector,
            keccak256("CleanPassAttested(bytes32,uint256,bytes32,bytes32,uint64,uint8)")
        );
        assertEq(IncentivePool.CleanPassChallenged.selector, keccak256("CleanPassChallenged(uint256,address,uint8)"));
        assertEq(IncentivePool.CleanPassCounted.selector, keccak256("CleanPassCounted(bytes32,uint256,uint32,uint16)"));
        assertEq(IncentivePool.CleanPassVoided.selector, keccak256("CleanPassVoided(uint256,address,uint256)"));
        assertEq(IncentivePool.CleanPassExpired.selector, keccak256("CleanPassExpired(uint256)"));
        assertEq(IncentivePool.ObserverFeePaid.selector, keccak256("ObserverFeePaid(uint256,address,uint256)"));
        assertEq(
            IncentivePool.InstallmentScheduled.selector,
            keccak256("InstallmentScheduled(bytes32,uint256,uint8,uint256,uint64)")
        );
        assertEq(IncentivePool.InstallmentUnfunded.selector, keccak256("InstallmentUnfunded(bytes32,uint256,uint256)"));
        assertEq(IncentivePool.InstallmentReleased.selector, keccak256("InstallmentReleased(uint256,uint256)"));
        assertEq(IncentivePool.CreditNoteDue.selector, keccak256("CreditNoteDue(bytes32,uint256,uint256,bytes32)"));
        assertEq(IncentivePool.CreditNoteSuperseded.selector, keccak256("CreditNoteSuperseded(bytes32,bytes32)"));
        assertEq(IncentivePool.ClaimCancelled.selector, keccak256("ClaimCancelled(bytes32,uint256,uint256)"));
        assertEq(IncentivePool.ClaimClosed.selector, keccak256("ClaimClosed(bytes32)"));
        assertEq(
            IncentivePool.ClaimCorroborated.selector, keccak256("ClaimCorroborated(bytes32,bytes32,uint64,uint64)")
        );
        assertEq(IncentivePool.FinalOutOfTerm.selector, keccak256("FinalOutOfTerm(bytes32)"));
        assertEq(IncentivePool.Credited.selector, keccak256("Credited(address,uint256)"));
        assertEq(IncentivePool.Withdrawn.selector, keccak256("Withdrawn(address,uint256)"));
        // §15 A3b additions
        assertEq(IncentivePool.CorroborationPending.selector, keccak256("CorroborationPending(bytes32,bytes32,uint64)"));
        assertEq(
            IncentivePool.CorroborationChallenged.selector, keccak256("CorroborationChallenged(bytes32,address,uint64)")
        );
    }

    function test_surface_errors() public pure {
        assertEq(IncentivePool.PoolNotOpenedByParty.selector, _sel("PoolNotOpenedByParty()"));
        assertEq(IncentivePool.PolicyMismatch.selector, _sel("PolicyMismatch()"));
        assertEq(IncentivePool.BadParams.selector, _sel("BadParams()"));
        assertEq(IncentivePool.PoolClosed.selector, _sel("PoolClosed()"));
        assertEq(IncentivePool.PoolNotClosed.selector, _sel("PoolNotClosed()"));
        assertEq(IncentivePool.NotConfirmed.selector, _sel("NotConfirmed(uint256)"));
        assertEq(IncentivePool.WrongCovenant.selector, _sel("WrongCovenant()"));
        assertEq(IncentivePool.ZeroCap.selector, _sel("ZeroCap()"));
        assertEq(IncentivePool.ClaimExists.selector, _sel("ClaimExists()"));
        assertEq(IncentivePool.ClaimNotOpen.selector, _sel("ClaimNotOpen()"));
        assertEq(IncentivePool.QuorumNotMet.selector, _sel("QuorumNotMet(uint256,uint256)"));
        assertEq(IncentivePool.AttestorNotIndependent.selector, _sel("AttestorNotIndependent(address)"));
        assertEq(IncentivePool.SelfAttestation.selector, _sel("SelfAttestation(address)"));
        assertEq(IncentivePool.RevokedKey.selector, _sel("RevokedKey(address)"));
        assertEq(IncentivePool.InvalidSignature.selector, _sel("InvalidSignature()"));
        assertEq(IncentivePool.AttestationMismatch.selector, _sel("AttestationMismatch(uint256)"));
        assertEq(IncentivePool.Replay.selector, _sel("Replay(address,uint64)"));
        assertEq(IncentivePool.Expired.selector, _sel("Expired(uint64)"));
        assertEq(IncentivePool.BadKind.selector, _sel("BadKind()"));
        assertEq(IncentivePool.Cloudy.selector, _sel("Cloudy()"));
        assertEq(IncentivePool.FloorAboveRate.selector, _sel("FloorAboveRate()"));
        assertEq(IncentivePool.BeforePublicationLag.selector, _sel("BeforePublicationLag()"));
        assertEq(IncentivePool.SceneOutOfRange.selector, _sel("SceneOutOfRange()"));
        assertEq(IncentivePool.DuplicateScene.selector, _sel("DuplicateScene()"));
        assertEq(IncentivePool.TooManyPending.selector, _sel("TooManyPending()"));
        assertEq(IncentivePool.SourceUnderReview.selector, _sel("SourceUnderReview(uint256)"));
        assertEq(IncentivePool.RelapsePending.selector, _sel("RelapsePending(uint256)"));
        assertEq(IncentivePool.FinalOutOfTermError.selector, _sel("FinalOutOfTermError()"));
        assertEq(IncentivePool.NotCorroborated.selector, _sel("NotCorroborated()"));
        assertEq(IncentivePool.MarsPlumeInWindow.selector, _sel("MarsPlumeInWindow()"));
        assertEq(IncentivePool.WindowOpen.selector, _sel("WindowOpen()"));
        assertEq(IncentivePool.WindowClosed.selector, _sel("WindowClosed()"));
        assertEq(IncentivePool.InvalidState.selector, _sel("InvalidState()"));
        assertEq(IncentivePool.BadBond.selector, _sel("BadBond()"));
        assertEq(IncentivePool.InvalidReason.selector, _sel("InvalidReason()"));
        assertEq(IncentivePool.NotARelapse.selector, _sel("NotARelapse(uint256)"));
        assertEq(IncentivePool.NothingToWithdraw.selector, _sel("NothingToWithdraw()"));
        assertEq(IncentivePool.TransferFailed.selector, _sel("TransferFailed()"));
    }

    /// Positional construction with typed values: reordering or narrowing a field breaks compilation or this test.
    function test_surface_structs() public pure {
        IncentivePool.Pool memory pl = IncentivePool.Pool(
            IncentivePool.PoolState.OPEN,
            IncentivePool.Mode.VERDICT_ONLY,
            bytes32(uint256(1)),
            bytes32(uint256(2)),
            uint256(3),
            uint256(4),
            address(5)
        );
        assertEq(abi.encode(pl).length, 7 * 32);
        IncentivePool.Claim memory c = IncentivePool.Claim(
            uint256(1),
            bytes32(0),
            bytes32(0),
            uint64(2),
            uint32(3),
            uint8(4),
            uint256(5),
            uint256(6),
            uint256(7),
            uint32(8),
            uint8(9),
            uint8(10),
            uint64(11),
            IncentivePool.ClaimState.CLOSED
        );
        assertEq(abi.encode(c).length, 14 * 32);
        IncentivePool.Installment memory i = IncentivePool.Installment(
            bytes32(0), uint8(1), uint256(2), uint64(3), IncentivePool.InstState.VOID, bytes32(uint256(4))
        );
        assertEq(abi.encode(i).length, 6 * 32);
        IncentivePool.InstallmentRule memory r = IncentivePool.InstallmentRule(uint8(3), uint16(3000), false, true);
        assertEq(abi.encode(r).length, 4 * 32);
        bytes32[] memory orgs = new bytes32[](0);
        IncentivePool.Pass memory p = IncentivePool.Pass(
            bytes32(0),
            bytes32(0),
            bytes32(0),
            uint64(1),
            uint64(2),
            uint8(3),
            uint16(4),
            address(5),
            uint96(6),
            orgs,
            address(7),
            uint96(8),
            uint8(9),
            uint64(10),
            uint64(11),
            IncentivePool.PassState.EXPIRED
        );
        assertEq(p.resolveBy, 11);
        assertEq(uint8(type(IncentivePool.PassState).max), 5);
        assertEq(uint8(type(IncentivePool.InstState).max), 4);
        assertEq(uint8(type(IncentivePool.ClaimState).max), 3);
        assertEq(uint8(type(IncentivePool.PoolState).max), 3);
        assertEq(uint8(type(IncentivePool.Mode).max), 1);
    }
}

/// @notice Signature domains (spec-v2 §3 construction, P7; §9.7 R14): a core `Attestation` signature is rejected by the pool and a
///         pool `CleanPass` signature by the core, in both directions and across chains. The type hashes and the
///         verifying contracts differ, so each digest recovers a stranger's address (fail closed: `InvalidSignature`).
contract IncentivePoolReplayTest is PoolFixture {
    uint32 internal constant RATE = 2548;
    bytes32 internal claimId;
    uint64 internal since;
    uint64 internal sceneAt;
    uint64 internal detected;

    function setUp() public override {
        super.setUp();
        detected = uint64(block.timestamp + 5 days);
        vm.warp(block.timestamp + 10 days);
        _openOnchain(10 ether);
        uint256 ep = _confirmed(SRC_TKM, RATE, detected);
        claimId = pool.openClaim(ep);
        since = detected + LINK_WINDOW;
        sceneAt = since + 1 days;
        vm.warp(uint256(sceneAt) + PUBLICATION_LAG);
    }

    function _coreAttFrom(CleanPass memory t) internal view returns (Attestation memory a) {
        a.covenantId = covId;
        a.sourceId = t.sourceId;
        a.evidenceHash = t.sceneHash;
        a.emissionKgH = RATE;
        a.uncertaintyKgH = RATE / 10;
        a.detectedAt = t.sceneAt;
        a.instrumentId = t.instrumentId;
        a.providerId = t.providerId;
        a.kind = KIND_DETECT;
        a.nonce = t.nonce;
        a.expiry = t.expiry;
    }

    /// R14: signatures over the core's `Attestation` digest (core domain) do not verify as a pool clean pass.
    function test_pool_rejectsCoreAttestation() public {
        (CleanPass[] memory atts, bytes[] memory sigs) =
            _cpBatch(_pks(obsAPk, obsBPk), _cp(claimId, SRC_TKM, sceneAt), KIND_CLEAN);
        for (uint256 i; i < 2; ++i) {
            sigs[i] = _sign(i == 0 ? obsAPk : obsBPk, _coreAttFrom(atts[i])); // signed as a core detection
        }
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.InvalidSignature.selector);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);

        // the clean pass itself signed under the core's domain (wrong verifying contract)
        (atts, sigs) = _cpBatch(_pks(obsAPk, obsBPk), _cp(claimId, SRC_TKM, sceneAt), KIND_CLEAN);
        for (uint256 i; i < 2; ++i) {
            sigs[i] = _signCpOn(i == 0 ? obsAPk : obsBPk, atts[i], block.chainid, address(cov));
        }
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.InvalidSignature.selector);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);

        // positive control: the same pass signed under the pool's own domain is accepted
        for (uint256 i; i < 2; ++i) {
            sigs[i] = _signCp(i == 0 ? obsAPk : obsBPk, atts[i]);
        }
        vm.prank(relayer);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);
    }

    /// R14: signatures over a pool `CleanPass` digest do not verify as a core detection, nor does a core attestation
    ///      signed under the pool's domain.
    function test_core_rejectsCleanPass() public {
        CleanPass memory t = _cp(claimId, SRC_TKM, sceneAt);
        Attestation[] memory atts = new Attestation[](2);
        bytes[] memory sigs = new bytes[](2);
        for (uint256 i; i < 2; ++i) {
            t.nonce = nextNonce++;
            atts[i] = _coreAttFrom(t);
            sigs[i] = _signCp(i == 0 ? obsAPk : obsBPk, t); // a clean-pass signature
        }
        bytes32 ev = t.sceneHash;
        bytes32[] memory proof = _proof(sources, SRC_TKM);
        vm.prank(proposer);
        vm.expectRevert(MethaneCovenant.InvalidSignature.selector);
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, ev, atts, sigs);

        for (uint256 i; i < 2; ++i) {
            sigs[i] = _signOn(i == 0 ? obsAPk : obsBPk, atts[i], block.chainid, address(pool)); // pool's domain
        }
        vm.prank(proposer);
        vm.expectRevert(MethaneCovenant.InvalidSignature.selector);
        cov.proposeEpisode{value: PROPOSER_BOND}(covId, SRC_TKM, proof, ev, atts, sigs);
        assertEq(cov.episodeCount(), 1); // only the setUp episode exists
    }

    /// R14: the same clean pass signed for another chain id is rejected; and after a fork (chain id change) the pool's
    ///      domain separator follows the new id, so signatures from the old chain stop verifying.
    function test_cleanPass_crossChain_reverts() public {
        (CleanPass[] memory atts, bytes[] memory sigs) =
            _cpBatch(_pks(obsAPk, obsBPk), _cp(claimId, SRC_TKM, sceneAt), KIND_CLEAN);
        for (uint256 i; i < 2; ++i) {
            sigs[i] = _signCpOn(i == 0 ? obsAPk : obsBPk, atts[i], 84_532, address(pool)); // Base Sepolia id
        }
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.InvalidSignature.selector);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);

        (atts, sigs) = _cpBatch(_pks(obsAPk, obsBPk), _cp(claimId, SRC_TKM, sceneAt), KIND_CLEAN); // this chain
        bytes32 before = pool.DOMAIN_SEPARATOR();
        vm.chainId(11_155_111);
        assertTrue(pool.DOMAIN_SEPARATOR() != before);
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.InvalidSignature.selector);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);
    }

    /// P7 as a property: whatever the fields, a clean pass signed under any domain other than (this chain, this pool)
    ///      never verifies.
    function testFuzz_cleanPass_foreignDomain_neverVerifies(uint256 chainId, address verifying, uint32 cloudBp) public {
        vm.assume(chainId != block.chainid || verifying != address(pool));
        chainId = bound(chainId, 1, type(uint64).max);
        CleanPass memory t = _cp(claimId, SRC_TKM, sceneAt);
        t.cloudBp = uint16(bound(cloudBp, 0, 10_000));
        (CleanPass[] memory atts, bytes[] memory sigs) = _cpBatch(_pks(obsAPk, obsBPk), t, KIND_CLEAN);
        for (uint256 i; i < 2; ++i) {
            sigs[i] = _signCpOn(i == 0 ? obsAPk : obsBPk, atts[i], chainId, verifying);
        }
        vm.prank(relayer);
        vm.expectRevert(IncentivePool.InvalidSignature.selector);
        pool.attestCleanPass{value: PASS_BOND}(claimId, atts, sigs);
    }
}

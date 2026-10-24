// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {PoolFixture} from "../utils/PoolFixture.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {SuretyBond} from "../../src/SuretyBond.sol";
import {CleanPass} from "../../src/lib/CleanPass.sol";

/// @notice A funder contract that rejects ETH (its withdrawal must revert and keep the money in the bond).
contract RejectingFunder {
    function withdrawFrom(SuretyBond b) external {
        b.withdraw();
    }
}

/// @notice Surety bond (spec-v2 §3 "SuretyBond"; §9.7 R25; review L2): a surety posts the seller's bond; the operator (the
///         real seller) may only create covenants, contest and record mitigation claims through it; settlement refunds
///         and pool refunds are assigned to the surety on chain and can only reach the funder.
contract SuretyBondTest is PoolFixture {
    address internal operator = makeAddr("operator [SIM seller]");
    address internal funder = makeAddr("funder [SIM surety]");
    SuretyBond internal bond;

    uint32 internal constant RATE = 2548; // tier 2

    event CovenantCreatedThroughBond(bytes32 indexed covenantId, address indexed operator, uint256 bond);
    event Pulled(address indexed caller, uint256 fromCovenant, uint256 fromPool);
    event FunderWithdrawn(address indexed funder, uint256 amount);
    event SelfRevoked(address indexed funder);

    function setUp() public override {
        super.setUp();
        bond = new SuretyBond(cov, pool, operator, funder);
        // the seller org's controller registers the bond's address as SELLER; the funder needs no registry role
        address sellerOrgController = makeAddr("sellerOrgController");
        _setController(ORG_SELLER, sellerOrgController);
        vm.prank(sellerOrgController);
        registry.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, address(bond), 1, 0);
        vm.deal(operator, 10 ether);
        vm.deal(funder, 100 ether);
    }

    function _create(uint256 value) internal returns (bytes32 cId) {
        MethaneCovenant.CovenantParams memory p = _params(); // computed first: sha256 would eat the prank
        vm.prank(operator);
        cId = bond.createCovenant{value: value}(p);
    }

    // ================================================================== construction

    function test_suretyBond_constructor_wiring() public view {
        assertEq(address(bond.covenant()), address(cov));
        assertEq(address(bond.pool()), address(pool));
        assertEq(bond.operator(), operator);
        assertEq(bond.funder(), funder);
    }

    /// @dev External, so `vm.expectRevert` applies to this call (a bare `new` right after it would match the test's
    ///      own revert and silently skip the rest of the test).
    function deployBond(MethaneCovenant c, IncentivePool p, address o, address f) external returns (SuretyBond) {
        return new SuretyBond(c, p, o, f);
    }

    function test_suretyBond_constructor_badArgs_revert() public {
        vm.expectRevert(SuretyBond.ZeroAddress.selector);
        this.deployBond(MethaneCovenant(address(0)), pool, operator, funder);
        vm.expectRevert(SuretyBond.ZeroAddress.selector);
        this.deployBond(cov, pool, address(0), funder);
        vm.expectRevert(SuretyBond.ZeroAddress.selector);
        this.deployBond(cov, pool, operator, address(0));
        // a pool over another covenant contract would pull nothing that belongs to this bond
        MethaneCovenant other = new MethaneCovenant(registry, PROPOSER_BOND, CHALLENGER_BOND);
        vm.expectRevert(SuretyBond.PoolMismatch.selector);
        this.deployBond(other, pool, operator, funder);
        // no pool is allowed (core-only surety)
        SuretyBond coreOnly = this.deployBond(cov, IncentivePool(payable(address(0))), operator, funder);
        assertEq(address(coreOnly.pool()), address(0));
    }

    // ================================================================== operator functions

    function test_suretyBond_operatorCreatesCovenant_sellerIsBond_emits() public {
        MethaneCovenant.CovenantParams memory p = _params();
        vm.expectEmit(false, true, false, true, address(bond));
        emit CovenantCreatedThroughBond(bytes32(0), operator, 1 ether);
        vm.prank(operator);
        bytes32 cId = bond.createCovenant{value: 1 ether}(p);
        MethaneCovenant.Covenant memory c = cov.getCovenant(cId);
        assertEq(c.seller, address(bond)); // refunds are assigned to the surety on chain
        assertEq(c.buyer, buyer);
        assertEq(c.funded, 1 ether);
        assertEq(address(bond).balance, 0); // forwarded, not kept
    }

    function test_suretyBond_onlyOperator_reverts() public {
        MethaneCovenant.CovenantParams memory p = _params();
        address[2] memory others = [funder, stranger];
        for (uint256 i; i < 2; ++i) {
            vm.startPrank(others[i]);
            vm.expectRevert(SuretyBond.NotOperator.selector);
            bond.createCovenant(p);
            vm.expectRevert(SuretyBond.NotOperator.selector);
            bond.contest(1);
            vm.expectRevert(SuretyBond.NotOperator.selector);
            bond.recordMitigationClaim(covId, SRC_TKM, keccak256("claim"));
            vm.stopPrank();
        }
    }

    function test_suretyBond_operatorContestsAndRecordsMitigation() public {
        bytes32 cId = _create(0);
        vm.prank(funder);
        cov.fund{value: 5 ether}(cId); // the surety posts the bond directly to the core
        vm.warp(block.timestamp + 2 days);
        uint256 ep = _proposeOn(cId, SRC_TKM, RATE, uint64(block.timestamp - 1 days));
        vm.prank(operator);
        bond.contest(ep);
        assertEq(uint8(cov.getEpisode(ep).state), uint8(MethaneCovenant.State.CONTESTED));
        vm.prank(operator);
        bond.recordMitigationClaim(cId, SRC_TKM, keccak256("valve replaced [SIM]")); // inert in the core
    }

    // ================================================================== money: pull and withdraw

    /// R25: settlement refunds reach the funder only; the operator has no path to them.
    function test_suretyBond_pull_afterSettle() public {
        bytes32 cId = _create(0);
        vm.prank(funder);
        cov.fund{value: 5 ether}(cId);
        MethaneCovenant.Covenant memory c = cov.getCovenant(cId);
        vm.warp(uint256(c.end) + c.challengeWindow);
        cov.settle(cId); // the remaining bond is credited to the seller: the bond contract
        assertEq(cov.withdrawable(address(bond)), 5 ether);

        vm.expectEmit(true, false, false, true, address(bond));
        emit Pulled(stranger, 5 ether, 0);
        vm.prank(stranger); // permissionless
        bond.pull();
        assertEq(address(bond).balance, 5 ether);
        assertEq(cov.withdrawable(address(bond)), 0);

        uint256 before = funder.balance;
        vm.expectEmit(true, false, false, true, address(bond));
        emit FunderWithdrawn(funder, 5 ether);
        vm.prank(funder);
        bond.withdraw();
        assertEq(funder.balance - before, 5 ether);
        assertEq(address(bond).balance, 0);
    }

    /// R25: whatever the operator calls, no ETH leaves the bond towards it.
    function test_suretyBond_operatorCannotMoveFunds() public {
        bytes32 cId = _create(0);
        vm.prank(funder);
        cov.fund{value: 5 ether}(cId);
        MethaneCovenant.Covenant memory c = cov.getCovenant(cId);
        vm.warp(uint256(c.end) + c.challengeWindow);
        cov.settle(cId);

        // A5 audit N3: the operator's own three functions leave the bond's balance and credits untouched
        MethaneCovenant.CovenantParams memory p = _params();
        uint256 creditBefore = cov.withdrawable(address(bond));
        uint256 poolCreditBefore = pool.withdrawable(address(bond));
        uint256 balanceBefore = address(bond).balance;
        vm.prank(operator);
        bytes32 cId2 = bond.createCovenant{value: 0}(p);
        vm.warp(block.timestamp + 1 days);
        uint256 ep2 = _proposeOn(cId2, SRC_TKM, RATE, uint64(block.timestamp - 1 hours));
        vm.startPrank(operator);
        bond.contest(ep2);
        bond.recordMitigationClaim(cId2, SRC_TKM, keccak256("mitigation [SIM]"));
        vm.stopPrank();
        assertEq(cov.withdrawable(address(bond)), creditBefore);
        assertEq(pool.withdrawable(address(bond)), poolCreditBefore);
        assertEq(address(bond).balance, balanceBefore);

        uint256 operatorBefore = operator.balance;
        vm.startPrank(operator);
        bond.pull(); // allowed, but it only brings the money home
        vm.expectRevert(SuretyBond.NotFunder.selector);
        bond.withdraw();
        vm.expectRevert(SuretyBond.NotFunder.selector);
        bond.revokeSelf();
        vm.stopPrank();
        assertEq(operator.balance, operatorBefore);
        assertEq(address(bond).balance, 5 ether);

        // the core pays the seller only through the seller's own address: the operator cannot withdraw for the bond
        vm.prank(operator);
        vm.expectRevert(MethaneCovenant.NothingToWithdraw.selector);
        cov.withdraw();
    }

    /// A pool installment released to the covenant's seller (the bond) is pulled home as well.
    function test_suretyBond_pull_poolRefund() public {
        bytes32 cId = _create(0);
        vm.prank(funder);
        cov.fund{value: 5 ether}(cId);
        IncentivePool.PoolParams memory p = _validParams();
        vm.prank(buyer);
        pool.fund{value: 10 ether}(cId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, p);
        vm.warp(block.timestamp + 2 days);
        uint64 detected = uint64(block.timestamp - 1 days);
        uint256 ep = _proposeOn(cId, SRC_TKM, RATE, detected);
        vm.warp(block.timestamp + CHALLENGE_WINDOW);
        cov.finalize(ep);
        bytes32 claimId = pool.openClaim(ep);
        uint64 sceneAt = detected + LINK_WINDOW + 1 days;
        vm.warp(uint256(sceneAt) + PUBLICATION_LAG + 1 hours);
        uint256[] memory ids = new uint256[](3);
        for (uint256 i; i < 3; ++i) {
            CleanPass memory t = _cp(claimId, SRC_TKM, sceneAt + uint64(i) * 1 minutes);
            ids[i] = _attestPass(t);
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        for (uint256 i; i < 3; ++i) {
            pool.countPass(ids[i]);
        }
        vm.warp(pool.getInstallment(1).claimableAt);
        pool.release(1);
        uint256 refund = pool.getInstallment(1).amount;
        assertEq(pool.withdrawable(address(bond)), refund);

        vm.expectEmit(true, false, false, true, address(bond));
        emit Pulled(address(this), 0, refund);
        bond.pull();
        vm.prank(funder);
        bond.withdraw();
        assertEq(address(bond).balance, 0);
    }

    function test_suretyBond_pull_nothing_reverts() public {
        vm.expectRevert(SuretyBond.NothingToWithdraw.selector);
        bond.pull();
    }

    function test_suretyBond_withdraw_nothing_reverts() public {
        vm.prank(funder);
        vm.expectRevert(SuretyBond.NothingToWithdraw.selector);
        bond.withdraw();
    }

    /// Plain ETH is accepted and does nothing else (the core's and the pool's `withdraw` must never revert on it);
    /// it belongs to the funder like everything else the bond holds.
    function test_suretyBond_receive_acceptsAndBelongsToFunder() public {
        vm.prank(stranger);
        (bool ok,) = address(bond).call{value: 1 ether}("");
        assertTrue(ok);
        uint256 before = funder.balance;
        vm.prank(funder);
        bond.withdraw();
        assertEq(funder.balance - before, 1 ether);
    }

    function test_suretyBond_withdraw_revertingFunder_keepsBalance() public {
        RejectingFunder rf = new RejectingFunder();
        SuretyBond b = new SuretyBond(cov, pool, operator, address(rf));
        vm.deal(address(b), 1 ether);
        vm.expectRevert(SuretyBond.TransferFailed.selector);
        rf.withdrawFrom(b);
        assertEq(address(b).balance, 1 ether);
    }

    // ================================================================== revokeSelf

    /// A5 audit N3: after `revokeSelf` the existing covenant still settles and the refund still reaches the funder.
    function test_suretyBond_settleAndPull_afterRevokeSelf() public {
        bytes32 cId = _create(0);
        vm.prank(funder);
        cov.fund{value: 5 ether}(cId);
        vm.prank(funder);
        bond.revokeSelf();
        MethaneCovenant.Covenant memory c = cov.getCovenant(cId);
        vm.warp(uint256(c.end) + c.challengeWindow);
        cov.settle(cId);
        bond.pull();
        uint256 before = funder.balance;
        vm.prank(funder);
        bond.withdraw();
        assertEq(funder.balance - before, 5 ether);
    }

    /// The funder withdraws its backing for new covenants: the bond's SELLER key is closed in the registry. Existing
    /// covenants keep running (the core gates seller functions on the address, not on key validity).
    function test_suretyBond_revokeSelf_onlyFunder() public {
        bytes32 cId = _create(0);
        vm.prank(stranger);
        vm.expectRevert(SuretyBond.NotFunder.selector);
        bond.revokeSelf();

        vm.expectEmit(true, false, false, true, address(bond));
        emit SelfRevoked(funder);
        vm.prank(funder);
        bond.revokeSelf();
        assertFalse(registry.isValidAt(address(bond), KeyRegistry.Role.SELLER, block.timestamp));

        MethaneCovenant.CovenantParams memory p = _params();
        vm.prank(operator);
        vm.expectRevert(MethaneCovenant.NotAuthorized.selector); // no new covenants under the surety
        bond.createCovenant(p);

        vm.prank(funder);
        cov.fund{value: 1 ether}(cId);
        vm.warp(block.timestamp + 2 days);
        uint256 ep = _proposeOn(cId, SRC_TKM, RATE, uint64(block.timestamp - 1 days));
        vm.prank(operator);
        bond.contest(ep); // the existing covenant still works
    }
}

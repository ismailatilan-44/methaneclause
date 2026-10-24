// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {PoolFixture} from "../utils/PoolFixture.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {CleanPass} from "../../src/lib/CleanPass.sol";

/// @notice POLICY_V2_MULTIYEAR (pipeline/policy.py; vector pipeline/tests/vectors/policy_v2_multiyear.json): the same
///         clause over a three-year Term. The fixture's windows are the policy's (checked against the vector), the
///         covenant's term is the vector's, and the boundary day is pipeline/final_reach.py's answer: an episode on
///         that day reaches the final installment and the seller is refunded the whole rebate; one day later the final
///         is out of term (`FinalOutOfTerm`, SECURITY R27) and a refund stops at the non-final shares.
contract MultiYearTermTest is PoolFixture {
    uint32 internal constant RATE = 2548;
    string internal vec;
    uint64 internal term;
    uint64 internal lastDay;
    uint64 private _sceneMinute;

    function setUp() public override {
        super.setUp();
        vec = vm.readFile(string.concat(vm.projectRoot(), "/../pipeline/tests/vectors/policy_v2_multiyear.json"));
        assertEq(_u(".linkWindow"), LINK_WINDOW, "fixture link window != policy");
        assertEq(_u(".challengeWindow"), CHALLENGE_WINDOW, "fixture challenge window != policy");
        assertEq(_u(".contestWindow"), CONTEST_WINDOW, "fixture contest window != policy");
        assertEq(_u(".publicationLag"), PUBLICATION_LAG, "fixture publication lag != policy");
        assertEq(_u(".cleanWindow"), CLEAN_WINDOW, "fixture clean window != policy");
        assertEq(_u(".holdWindow"), HOLD_WINDOW, "fixture hold window != policy");
        assertEq(_u(".cureTerm"), CURE_TERM, "fixture cure term != policy");
        term = uint64(_u(".term"));
        lastDay = uint64(_u(".finalReach.lastDetectionDay"));
        // the fixture helpers (_propose, _att) act on `covId`: point it at a covenant with the policy's term
        MethaneCovenant.CovenantParams memory p = _params();
        p.term = term;
        vm.prank(seller);
        covId = cov.createCovenant{value: BOND}(p);
    }

    function _u(string memory path) internal view returns (uint256) {
        return vm.parseJsonUint(vec, path);
    }

    /// @notice Pool funded under the policy's windows, episode CONFIRMED `day` days after the covenant's start, claim
    ///         opened, clock past the publication lag of the first scenes.
    function _claimOn(uint64 day) internal returns (IncentivePool target, bytes32 cid, uint64 since) {
        target = new IncentivePool(cov);
        vm.prank(buyer);
        target.fund{value: 10 ether}(covId, POLICY_V2_HASH, IncentivePool.Mode.ONCHAIN, _validParams());
        uint64 detected = cov.getCovenant(covId).start + day * 1 days;
        vm.warp(detected + 1 hours);
        cid = target.openClaim(_confirmed(SRC_TKM, RATE, detected));
        since = detected + LINK_WINDOW;
        vm.warp(uint256(since) + 1 days + PUBLICATION_LAG + 1 days);
    }

    function _attest(IncentivePool target, bytes32 cid, uint64 since, uint8 instrument) internal returns (uint256) {
        CleanPass memory t = _cp(cid, SRC_TKM, since + 1 days + (++_sceneMinute) * 1 minutes);
        t.instrumentId = instrument;
        return _attestPassOn(target, t, _pks(obsAPk, obsBPk));
    }

    function _count(IncentivePool target, bytes32 cid, uint64 since, uint256 n, uint8 instrument) internal {
        uint256[] memory ids = new uint256[](n);
        for (uint256 i; i < n; ++i) {
            ids[i] = _attest(target, cid, since, instrument);
        }
        vm.warp(block.timestamp + CLEAN_WINDOW);
        for (uint256 i; i < n; ++i) {
            target.countPass(ids[i]);
        }
    }

    function test_vector_boundaryIsInsideTheTerm() public view {
        assertGe(lastDay, 365, "every episode of the first contract year reaches the final installment");
        assertLt(lastDay * 1 days, term);
    }

    /// The whole schedule on the boundary day: three installments released, paid == cap, nothing reserved.
    function test_finalReleased_onTheBoundaryDay() public {
        (IncentivePool target, bytes32 cid, uint64 since) = _claimOn(lastDay);
        _count(target, cid, since, 5, 1);
        _count(target, cid, since, 5, 2); // two instruments
        _corroborateEffective(target, _corr(cid, SRC_TKM, since, uint64(block.timestamp)));
        IncentivePool.Installment memory fin = target.getInstallment(3);
        assertEq(fin.claimId, cid, "final installment scheduled");
        assertLe(fin.claimableAt, cov.getCovenant(covId).end + PUBLICATION_LAG, "claimable inside the term");

        vm.warp(fin.claimableAt);
        target.release(1);
        target.release(2);
        target.release(3);
        IncentivePool.Claim memory c = target.getClaim(cid);
        assertEq(c.paid, c.cap); // the whole rebate, never more
        assertEq(c.reserved, 0);
        assertEq(target.withdrawable(seller), c.cap);
    }

    /// One day later the final is out of term: the tenth pass emits FinalOutOfTerm and the reserve stops at 60 %.
    function test_finalOutOfTerm_oneDayLater() public {
        (IncentivePool target, bytes32 cid, uint64 since) = _claimOn(lastDay + 1);
        _count(target, cid, since, 5, 1);
        _count(target, cid, since, 4, 2);
        uint256 tenth = _attest(target, cid, since, 2);
        vm.warp(block.timestamp + CLEAN_WINDOW);
        vm.expectEmit(true, false, false, true, address(target));
        emit IncentivePool.FinalOutOfTerm(cid);
        target.countPass(tenth);
        _corroborateEffective(target, _corr(cid, SRC_TKM, since, uint64(block.timestamp)));
        assertEq(target.getInstallment(3).claimId, bytes32(0));
        IncentivePool.Claim memory c = target.getClaim(cid);
        assertEq(c.reserved, c.cap * 6000 / 10_000, "capped at the non-final shares");
    }
}

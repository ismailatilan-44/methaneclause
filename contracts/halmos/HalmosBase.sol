// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {KeyRegistry} from "../src/KeyRegistry.sol";
import {MethaneCovenant} from "../src/MethaneCovenant.sol";
import {IncentivePool} from "../src/IncentivePool.sol";
import {IncentivePoolHarness} from "./IncentivePoolHarness.sol";

/// @notice The pool parameters of the Halmos runs (the unit fixture's values: spec-v2 §4 shape, test amounts [SIM]).
abstract contract HalmosParams {
    function _upperBounds() internal pure returns (uint16[32] memory u) {
        u = [
            uint16(9750),
            8418,
            7075,
            6023,
            5218,
            4592,
            4096,
            3694,
            3362,
            3084,
            2849,
            2646,
            2470,
            2316,
            2180,
            2059,
            1950,
            1853,
            1764,
            1684,
            1610,
            1543,
            1481,
            1424,
            1371,
            1322,
            1277,
            1234,
            1194,
            1157,
            1121,
            1088
        ];
    }

    /// @dev spec-v2 §4 shape (the fixture's values): 30 / 30 / 40 % after 3 / 6 / 10 passes, final needs two
    ///      instruments; windows in seconds.
    function _params() internal pure returns (IncentivePool.PoolParams memory p) {
        p.publicationLag = 60 days;
        p.cleanWindow = 1 days;
        p.resolveWindow = 7 days;
        p.holdWindow = 64 days;
        p.cureTerm = 540 days;
        p.passBond = 0.001 ether;
        p.challengerBond = 0.002 ether;
        p.observerFeeWei = 0.0001 ether;
        p.unitTierAmounts = [uint128(500), 300, 100];
        p.installments[0] = IncentivePool.InstallmentRule(3, 3000, false, false);
        p.installments[1] = IncentivePool.InstallmentRule(6, 3000, false, false);
        p.installments[2] = IncentivePool.InstallmentRule(10, 4000, true, true);
        p.upperBoundBp = _upperBounds();
    }
}

/// @notice Shared Halmos setup: the real KeyRegistry and the unchanged core MethaneCovenant with one covenant, and the
///         pool harness over it. Concrete addresses only (Halmos models `vm.addr` symbolically) and no SHA-256 in the
///         setup. All amounts are test values [SIM].
abstract contract HalmosBase is Test, HalmosParams {
    address internal constant SELLER = address(0x5E11E7);
    address internal constant BUYER = address(0xB0FE7);
    address internal constant SPONSOR = address(0x5B0750);
    address internal constant RELAYER = address(0x7E1A7);
    address internal constant CTRL_A = address(0xC7A1);
    address internal constant CTRL_B = address(0xC7B1);
    bytes32 internal constant ORG_SELLER = "ORG_SELLER_SIM";
    bytes32 internal constant ORG_BUYER = "ORG_BUYER_SIM";
    bytes32 internal constant ORG_A = "OBS_A";
    bytes32 internal constant ORG_B = "OBS_B";
    bytes32 internal constant POLICY = keccak256("policy-v2 [SIM halmos value]");
    uint256 internal constant T0 = 1_780_000_000;
    uint64 internal constant TERM = 3 * 365 days; // long enough for the 540-day cure term to fit (R27)
    uint64 internal constant CHALLENGE_WINDOW = 1 days;
    uint64 internal constant CONTEST_WINDOW = 2 days;
    uint64 internal constant LINK_WINDOW = 30 days;

    KeyRegistry internal registry;
    MethaneCovenant internal cov;
    IncentivePoolHarness internal pool;
    bytes32 internal cid;

    function setUp() public virtual {
        vm.warp(T0);
        registry = new KeyRegistry(address(this));
        registry.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, SELLER, 1, 0);
        registry.registerKey(ORG_BUYER, KeyRegistry.Role.BUYER, BUYER, 1, 0);
        registry.setOrgController(ORG_A, CTRL_A);
        registry.setOrgController(ORG_B, CTRL_B);
        cov = new MethaneCovenant(registry, 0.0001 ether, 0.0002 ether);
        MethaneCovenant.CovenantParams memory cp;
        cp.buyer = BUYER;
        cp.sourceSetRoot = keccak256("source set [SIM]");
        cp.policyHash = keccak256("policy-v1 [SIM]");
        cp.term = TERM;
        cp.k = 2;
        cp.challengeWindow = CHALLENGE_WINDOW;
        cp.contestWindow = CONTEST_WINDOW;
        cp.linkWindow = LINK_WINDOW;
        cp.minRateKgH = 100;
        cp.tierAmounts = [uint128(3 ether), 2 ether, 1 ether];
        vm.deal(SELLER, 10 ether);
        vm.prank(SELLER);
        cid = cov.createCovenant{value: 5 ether}(cp);
        pool = new IncentivePoolHarness(cov);
    }
}

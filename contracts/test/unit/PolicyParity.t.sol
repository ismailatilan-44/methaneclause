// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";

/// @notice The pipeline's POLICY_V2 vector (pipeline/tests/vectors/policy_v2.json, written by pipeline/policy.py) rebuilt
///         field by field into the pool's frozen PoolParams layout: the on-chain hash a deployment emits in
///         `PoolFunded.paramsHash` equals POLICY_V2.onchainParamsHash (spec-v2 §3, §4; §15 B1 review (9)).
contract PolicyParityTest is Test {
    string internal vec;

    function setUp() public {
        vec = vm.readFile(string.concat(vm.projectRoot(), "/../pipeline/tests/vectors/policy_v2.json"));
    }

    function _u(string memory path) internal view returns (uint256) {
        return vm.parseJsonUint(vec, path);
    }

    function _field(uint256 i, string memory name) internal view returns (string memory path) {
        path = string.concat(".fields[", vm.toString(i), "]");
        assertEq(vm.parseJsonString(vec, string.concat(path, ".name")), name, "field order differs from PoolParams");
        path = string.concat(path, ".value");
    }

    function _params() internal view returns (IncentivePool.PoolParams memory p) {
        p.publicationLag = uint32(_u(_field(0, "publicationLag")));
        p.cleanWindow = uint32(_u(_field(1, "cleanWindow")));
        p.resolveWindow = uint32(_u(_field(2, "resolveWindow")));
        p.holdWindow = uint32(_u(_field(3, "holdWindow")));
        p.cureTerm = uint32(_u(_field(4, "cureTerm")));
        p.passBond = uint96(_u(_field(5, "passBond")));
        p.challengerBond = uint96(_u(_field(6, "challengerBond")));
        p.observerFeeWei = uint96(_u(_field(7, "observerFeeWei")));
        string memory tiers = _field(8, "unitTierAmounts");
        for (uint256 i; i < 3; ++i) {
            p.unitTierAmounts[i] = uint128(_u(string.concat(tiers, "[", vm.toString(i), "]")));
        }
        string memory inst = _field(9, "installments");
        for (uint256 i; i < 3; ++i) {
            string memory r = string.concat(inst, "[", vm.toString(i), "]");
            p.installments[i] = IncentivePool.InstallmentRule({
                afterPasses: uint8(_u(string.concat(r, ".afterPasses"))),
                shareBp: uint16(_u(string.concat(r, ".shareBp"))),
                twoInstruments: vm.parseJsonBool(vec, string.concat(r, ".twoInstruments")),
                isFinal: vm.parseJsonBool(vec, string.concat(r, ".isFinal"))
            });
        }
        string memory ub = _field(10, "upperBoundBp");
        for (uint256 i; i < 32; ++i) {
            p.upperBoundBp[i] = uint16(_u(string.concat(ub, "[", vm.toString(i), "]")));
        }
    }

    function test_policyV2_paramsHash_matchesPipeline() public view {
        IncentivePool.PoolParams memory p = _params();
        assertEq(keccak256(abi.encode(p)), vm.parseJsonBytes32(vec, ".onchainParamsHash"));
        assertEq(abi.encode(p), vm.parseJsonBytes(vec, ".abiEncoded"));
    }

    /// The pipeline's values must also pass the pool's own `fund` validation shape (no silent BadParams on deploy).
    function test_policyV2_params_shapeIsValid() public view {
        IncentivePool.PoolParams memory p = _params();
        uint256 shares;
        for (uint256 i; i < 3; ++i) {
            shares += p.installments[i].shareBp;
            assertEq(p.installments[i].isFinal, i == 2);
            if (i > 0) assertGt(p.installments[i].afterPasses, p.installments[i - 1].afterPasses);
        }
        assertEq(shares, 10_000);
        assertTrue(p.installments[2].twoInstruments); // R21: required on chain since the security review
        assertGe(p.resolveWindow, p.cleanWindow);
        assertGt(p.passBond, 0);
        assertGt(p.challengerBond, 0);
        for (uint256 i = 1; i < 32; ++i) {
            assertLe(p.upperBoundBp[i], p.upperBoundBp[i - 1]);
        }
    }
}

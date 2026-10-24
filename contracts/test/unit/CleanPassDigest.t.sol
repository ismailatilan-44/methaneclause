// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Fixture} from "../utils/Fixture.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {AttestationLib} from "../../src/lib/Attestation.sol";
import {CleanPass, Corroboration, CleanPassLib} from "../../src/lib/CleanPass.sol";

/// @notice Step 0 (spec-v2 §3.1): Solidity and Python agree on the pool's EIP-712 digests byte for byte (vector written
///         by pipeline/observer/attest.py, checked here and in pipeline/tests/test_cleanpass_digest.py); the pool trusts
///         exactly the covenant's registry (R20).
contract CleanPassDigestTest is Fixture {
    string internal vec;

    function setUp() public override {
        super.setUp();
        vec = vm.readFile(string.concat(vm.projectRoot(), "/../pipeline/tests/vectors/cleanpass_v1.json"));
    }

    function _cleanPass() internal view returns (CleanPass memory a) {
        a.claimId = vm.parseJsonBytes32(vec, ".cleanPass.claimId");
        a.sourceId = vm.parseJsonBytes32(vec, ".cleanPass.sourceId");
        a.sceneId = vm.parseJsonBytes32(vec, ".cleanPass.sceneId");
        a.sceneHash = vm.parseJsonBytes32(vec, ".cleanPass.sceneHash");
        a.sceneAt = uint64(vm.parseJsonUint(vec, ".cleanPass.sceneAt"));
        a.publishedAt = uint64(vm.parseJsonUint(vec, ".cleanPass.publishedAt"));
        a.instrumentId = uint8(vm.parseJsonUint(vec, ".cleanPass.instrumentId"));
        a.providerId = vm.parseJsonBytes32(vec, ".cleanPass.providerId");
        a.cloudBp = uint16(vm.parseJsonUint(vec, ".cleanPass.cloudBp"));
        a.notCloudy = vm.parseJsonBool(vec, ".cleanPass.notCloudy");
        a.floorKgH = uint32(vm.parseJsonUint(vec, ".cleanPass.floorKgH"));
        a.rateFloorKgH = uint32(vm.parseJsonUint(vec, ".cleanPass.rateFloorKgH"));
        a.kind = uint8(vm.parseJsonUint(vec, ".cleanPass.kind"));
        a.nonce = uint64(vm.parseJsonUint(vec, ".cleanPass.nonce"));
        a.expiry = uint64(vm.parseJsonUint(vec, ".cleanPass.expiry"));
    }

    function _corroboration() internal view returns (Corroboration memory c) {
        c.claimId = vm.parseJsonBytes32(vec, ".corroboration.claimId");
        c.sourceId = vm.parseJsonBytes32(vec, ".corroboration.sourceId");
        c.providerId = vm.parseJsonBytes32(vec, ".corroboration.providerId");
        c.marsSnapshotHash = vm.parseJsonBytes32(vec, ".corroboration.marsSnapshotHash");
        c.windowFrom = uint64(vm.parseJsonUint(vec, ".corroboration.windowFrom"));
        c.windowTo = uint64(vm.parseJsonUint(vec, ".corroboration.windowTo"));
        c.marsPlumesInWindow = uint16(vm.parseJsonUint(vec, ".corroboration.marsPlumesInWindow"));
        c.kind = uint8(vm.parseJsonUint(vec, ".corroboration.kind"));
        c.nonce = uint64(vm.parseJsonUint(vec, ".corroboration.nonce"));
        c.expiry = uint64(vm.parseJsonUint(vec, ".corroboration.expiry"));
    }

    function _domain() internal view returns (bytes32) {
        return AttestationLib.domainSeparator(
            vm.parseJsonUint(vec, ".chainId"), vm.parseJsonAddress(vec, ".verifyingContract")
        );
    }

    function test_cleanPassDigest_matchesPython() public view {
        bytes32 d = AttestationLib.toTypedDataHash(_domain(), CleanPassLib.hash(_cleanPass()));
        assertEq(d, vm.parseJsonBytes32(vec, ".cleanPassDigest"));
    }

    function test_corroborationDigest_matchesPython() public view {
        bytes32 d = AttestationLib.toTypedDataHash(_domain(), CleanPassLib.hash(_corroboration()));
        assertEq(d, vm.parseJsonBytes32(vec, ".corroborationDigest"));
    }

    function test_constructor_registryIsCovenants() public {
        IncentivePool pool = new IncentivePool(cov);
        assertEq(address(pool.registry()), address(cov.registry()));
        assertEq(address(pool.covenant()), address(cov));
    }

    function test_poolDigest_isLibDigestUnderPoolDomain() public {
        IncentivePool pool = new IncentivePool(cov);
        CleanPass memory a = _cleanPass();
        bytes32 want = AttestationLib.toTypedDataHash(
            AttestationLib.domainSeparator(block.chainid, address(pool)), CleanPassLib.hash(a)
        );
        assertEq(pool.cleanPassDigest(a), want);
        assertTrue(pool.cleanPassDigest(a) != cov.DOMAIN_SEPARATOR()); // pool and core domains differ
    }
}

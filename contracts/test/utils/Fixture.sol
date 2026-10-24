// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {Attestation, AttestationLib, KIND_DETECT} from "../../src/lib/Attestation.sol";
import {SigLib} from "../../src/lib/SigLib.sol";

/// @notice Shared world for unit tests: one registry (admin renounced), one covenant over 4 sources, observer orgs
///         A (two keys), B, C, D, plus an observer key inside the seller org and one inside the buyer org.
abstract contract Fixture is Test {
    uint96 internal constant PROPOSER_BOND = 0.0001 ether;
    uint96 internal constant CHALLENGER_BOND = 0.0002 ether;
    uint256 internal constant BOND = 5 ether;
    uint128 internal constant TIER1 = 3 ether;
    uint128 internal constant TIER2 = 2 ether;
    uint128 internal constant TIER3 = 1 ether;
    uint64 internal constant TERM = 365 days;
    uint64 internal constant CHALLENGE_WINDOW = 1 days;
    uint64 internal constant CONTEST_WINDOW = 2 days;
    uint64 internal constant LINK_WINDOW = 30 days;
    uint32 internal constant MIN_RATE = 100;
    uint8 internal constant K = 2;
    uint256 internal constant T0 = 1_780_000_000; // 2026-05

    bytes32 internal constant ORG_SELLER = "ORG_SELLER_SIM";
    bytes32 internal constant ORG_BUYER = "ORG_BUYER_SIM";
    bytes32 internal constant ORG_A = "OBS_A";
    bytes32 internal constant ORG_B = "OBS_B";
    bytes32 internal constant ORG_C = "OBS_C";
    bytes32 internal constant ORG_D = "OBS_D";
    bytes32 internal constant ORG_OTHER_SELLER = "ORG_OTHER_SELLER";

    bytes32 internal constant SRC_TKM = keccak256("TKM_S_276");
    bytes32 internal constant SRC_DZA = keccak256("DZA_S_012");
    bytes32 internal constant SRC_USA = keccak256("USA_S_077");
    bytes32 internal constant SRC_LBY = keccak256("LBY_S_119");
    bytes32 internal constant SRC_UNCOVERED = keccak256("NOT_IN_SET");
    bytes32 internal constant PROVIDER_CM = "carbonmapper";

    KeyRegistry internal registry;
    MethaneCovenant internal cov;
    bytes32 internal covId;

    address internal admin = makeAddr("admin");
    address internal seller;
    uint256 internal sellerPk;
    address internal buyer;
    uint256 internal buyerPk;
    address internal obsA;
    uint256 internal obsAPk;
    address internal obsA2;
    uint256 internal obsA2Pk;
    address internal obsB;
    uint256 internal obsBPk;
    address internal obsC;
    uint256 internal obsCPk;
    address internal obsD;
    uint256 internal obsDPk;
    address internal sellerObs;
    uint256 internal sellerObsPk;
    address internal buyerObs;
    uint256 internal buyerObsPk;
    address internal otherSeller;
    uint256 internal otherSellerPk;
    address internal proposer = makeAddr("proposer");
    address internal challenger = makeAddr("challenger");

    bytes32[] internal sources;
    uint64 internal nextNonce = 1;

    function setUp() public virtual {
        vm.warp(T0);
        (seller, sellerPk) = makeAddrAndKey("seller");
        (buyer, buyerPk) = makeAddrAndKey("buyer");
        (obsA, obsAPk) = makeAddrAndKey("obsA");
        (obsA2, obsA2Pk) = makeAddrAndKey("obsA2");
        (obsB, obsBPk) = makeAddrAndKey("obsB");
        (obsC, obsCPk) = makeAddrAndKey("obsC");
        (obsD, obsDPk) = makeAddrAndKey("obsD");
        (sellerObs, sellerObsPk) = makeAddrAndKey("sellerObs");
        (buyerObs, buyerObsPk) = makeAddrAndKey("buyerObs");
        (otherSeller, otherSellerPk) = makeAddrAndKey("otherSeller");

        vm.startPrank(admin);
        registry = new KeyRegistry(admin);
        registry.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, seller, 1, 0);
        registry.registerKey(ORG_SELLER, KeyRegistry.Role.OBSERVER, sellerObs, 1, 0);
        registry.registerKey(ORG_BUYER, KeyRegistry.Role.BUYER, buyer, 1, 0);
        registry.registerKey(ORG_BUYER, KeyRegistry.Role.OBSERVER, buyerObs, 1, 0);
        registry.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, obsA, 1, 0);
        registry.registerKey(ORG_A, KeyRegistry.Role.OBSERVER, obsA2, 2, keccak256("mldsa44-pk-A2"));
        registry.registerKey(ORG_B, KeyRegistry.Role.OBSERVER, obsB, 1, 0);
        registry.registerKey(ORG_C, KeyRegistry.Role.OBSERVER, obsC, 1, 0);
        registry.registerKey(ORG_D, KeyRegistry.Role.OBSERVER, obsD, 1, 0);
        registry.registerKey(ORG_OTHER_SELLER, KeyRegistry.Role.SELLER, otherSeller, 1, 0);
        registry.setOrgController(ORG_D, admin);
        registry.renounceAdmin();
        vm.stopPrank();

        cov = new MethaneCovenant(registry, PROPOSER_BOND, CHALLENGER_BOND);

        sources.push(SRC_TKM);
        sources.push(SRC_DZA);
        sources.push(SRC_USA);
        sources.push(SRC_LBY);

        vm.deal(seller, 100 ether);
        vm.deal(proposer, 10 ether);
        vm.deal(challenger, 10 ether);
        MethaneCovenant.CovenantParams memory p = _params(); // computed first: sha256 precompile would eat the prank
        vm.prank(seller);
        covId = cov.createCovenant{value: BOND}(p);
    }

    // ------------------------------------------------------------------ builders

    function _params() internal view returns (MethaneCovenant.CovenantParams memory p) {
        p.buyer = buyer;
        p.sourceSetRoot = _root(sources);
        p.policyHash = sha256("policy-v1");
        p.term = TERM;
        p.k = K;
        p.challengeWindow = CHALLENGE_WINDOW;
        p.contestWindow = CONTEST_WINDOW;
        p.linkWindow = LINK_WINDOW;
        p.minRateKgH = MIN_RATE;
        p.tierAmounts = [TIER1, TIER2, TIER3];
    }

    function _att(bytes32 sourceId, bytes32 evidence, uint32 kgh, uint64 detectedAt, uint8 kind)
        internal
        returns (Attestation memory a)
    {
        a.covenantId = covId;
        a.sourceId = sourceId;
        a.evidenceHash = evidence;
        a.modelHash = bytes32(0);
        a.emissionKgH = kgh;
        a.uncertaintyKgH = kgh / 10;
        a.detectedAt = detectedAt;
        a.instrumentId = 1;
        a.providerId = PROVIDER_CM;
        a.kind = kind;
        a.nonce = nextNonce++;
        a.expiry = uint64(block.timestamp + 1 hours);
    }

    function _sign(uint256 pk, Attestation memory a) internal view returns (bytes memory) {
        return _signOn(pk, a, block.chainid, address(cov));
    }

    function _signOn(uint256 pk, Attestation memory a, uint256 chainId, address verifying)
        internal
        pure
        returns (bytes memory)
    {
        bytes32 digest = AttestationLib.toTypedDataHash(
            AttestationLib.domainSeparator(chainId, verifying), AttestationLib.hashMemory(a)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    /// @notice Builds one attestation per key (fresh nonce each) over the same detection, and signs it.
    function _batch(uint256[] memory pks, bytes32 sourceId, bytes32 evidence, uint32 kgh, uint64 detectedAt, uint8 kind)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs)
    {
        atts = new Attestation[](pks.length);
        sigs = new bytes[](pks.length);
        for (uint256 i; i < pks.length; ++i) {
            atts[i] = _att(sourceId, evidence, kgh, detectedAt, kind);
            sigs[i] = _sign(pks[i], atts[i]);
        }
    }

    function _pks(uint256 a, uint256 b) internal pure returns (uint256[] memory p) {
        p = new uint256[](2);
        p[0] = a;
        p[1] = b;
    }

    function _pks(uint256 a, uint256 b, uint256 c) internal pure returns (uint256[] memory p) {
        p = new uint256[](3);
        p[0] = a;
        p[1] = b;
        p[2] = c;
    }

    /// @notice Propose a 2-of-3 detection (orgs A, B) as `proposer`.
    function _propose(bytes32 sourceId, bytes32 evidence, uint32 kgh, uint64 detectedAt) internal returns (uint256) {
        (Attestation[] memory atts, bytes[] memory sigs) =
            _batch(_pks(obsAPk, obsBPk), sourceId, evidence, kgh, detectedAt, KIND_DETECT);
        vm.prank(proposer);
        return
            cov.proposeEpisode{value: PROPOSER_BOND}(covId, sourceId, _proof(sources, sourceId), evidence, atts, sigs);
    }

    // ------------------------------------------------------------------ Merkle (sorted pairs, odd node promoted)

    function _root(bytes32[] memory srcs) internal pure returns (bytes32) {
        bytes32[] memory level = _leaves(srcs);
        while (level.length > 1) {
            level = _up(level);
        }
        return level[0];
    }

    function _proof(bytes32[] memory srcs, bytes32 sourceId) internal pure returns (bytes32[] memory proof) {
        bytes32[] memory level = _leaves(srcs);
        uint256 idx = type(uint256).max;
        for (uint256 i; i < srcs.length; ++i) {
            if (srcs[i] == sourceId) idx = i;
        }
        if (idx == type(uint256).max) return new bytes32[](0);
        bytes32[] memory tmp = new bytes32[](32);
        uint256 n;
        while (level.length > 1) {
            uint256 sib = idx ^ 1;
            if (sib < level.length) tmp[n++] = level[sib];
            level = _up(level);
            idx /= 2;
        }
        proof = new bytes32[](n);
        for (uint256 i; i < n; ++i) {
            proof[i] = tmp[i];
        }
    }

    function _leaves(bytes32[] memory srcs) private pure returns (bytes32[] memory l) {
        l = new bytes32[](srcs.length);
        for (uint256 i; i < srcs.length; ++i) {
            l[i] = keccak256(bytes.concat(keccak256(abi.encode(srcs[i]))));
        }
    }

    function _up(bytes32[] memory level) private pure returns (bytes32[] memory next) {
        next = new bytes32[]((level.length + 1) / 2);
        for (uint256 i; i < level.length; i += 2) {
            if (i + 1 == level.length) {
                next[i / 2] = level[i];
            } else {
                (bytes32 a, bytes32 b) = level[i] < level[i + 1] ? (level[i], level[i + 1]) : (level[i + 1], level[i]);
                next[i / 2] = keccak256(abi.encode(a, b));
            }
        }
    }

    function _state(uint256 episodeId) internal view returns (MethaneCovenant.State) {
        return cov.getEpisode(episodeId).state;
    }

    // silence unused import warning for SigLib in derived tests
    function _leaf(bytes32 s) internal pure returns (bytes32) {
        return SigLib.sourceLeaf(s);
    }
}

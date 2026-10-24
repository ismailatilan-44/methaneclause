// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {KeyRegistry} from "../src/KeyRegistry.sol";
import {MethaneCovenant} from "../src/MethaneCovenant.sol";
import {Attestation, KIND_DETECT, KIND_CONFIRM} from "../src/lib/Attestation.sol";

/// @notice The actions Halmos may call on the unchanged core in any order with any arguments. Signatures are free
///         65-byte inputs: Halmos models `ecrecover` as an uninterpreted function, so a signature may recover to any
///         address, including every registered observer key. That over-approximates who can sign (a forged quorum is
///         allowed), so I4 holding here holds for real signatures too; what it does not cover is the signature check
///         itself (unit tests and the stateful fuzz do).
contract CoreActions is Test {
    MethaneCovenant internal immutable cov;
    bytes32 internal immutable cid;
    bytes32 internal constant SRC = keccak256("TKM_S_276");
    address internal constant SELLER = address(0x5E11E7);
    address internal constant BUYER = address(0xB0FE7);
    address internal constant PROPOSER = address(0x9409);
    address internal constant CHALLENGER = address(0xC4A11);
    uint64 internal nonce = 1;
    uint256 internal evidenceNonce;

    constructor(MethaneCovenant cov_, bytes32 cid_) {
        cov = cov_;
        cid = cid_;
    }

    function _att(bytes32 evidence, uint32 kgh, uint64 detectedAt, uint8 kind) internal returns (Attestation memory a) {
        a.covenantId = cid;
        a.sourceId = SRC;
        a.evidenceHash = evidence;
        a.emissionKgH = kgh;
        a.detectedAt = detectedAt;
        a.instrumentId = 1;
        a.providerId = "carbonmapper";
        a.kind = kind;
        a.nonce = nonce++;
        a.expiry = uint64(block.timestamp + 1 hours);
    }

    function _pair(bytes32 evidence, uint32 kgh, uint64 detectedAt, uint8 kind, bytes calldata sa, bytes calldata sb)
        internal
        returns (Attestation[] memory atts, bytes[] memory sigs)
    {
        atts = new Attestation[](2);
        sigs = new bytes[](2);
        atts[0] = _att(evidence, kgh, detectedAt, kind);
        atts[1] = _att(evidence, kgh, detectedAt, kind);
        sigs[0] = sa;
        sigs[1] = sb;
    }

    function propose(uint32 kgh, uint32 ago, bytes calldata sa, bytes calldata sb) external {
        bytes32 evidence = keccak256(abi.encode("evidence [SIM]", ++evidenceNonce));
        (Attestation[] memory atts, bytes[] memory sigs) =
            _pair(evidence, kgh, uint64(block.timestamp - ago), KIND_DETECT, sa, sb);
        uint256 bond = cov.proposerBond();
        vm.deal(PROPOSER, bond);
        vm.prank(PROPOSER);
        cov.proposeEpisode{value: bond}(cid, SRC, new bytes32[](0), evidence, atts, sigs);
    }

    function challenge(uint8 episodeId, uint8 reason) external {
        uint256 bond = cov.challengerBond();
        vm.deal(CHALLENGER, bond);
        vm.prank(CHALLENGER);
        cov.challenge{value: bond}(episodeId, reason);
    }

    function contest(uint8 episodeId) external {
        vm.prank(SELLER);
        cov.contest(episodeId);
    }

    function resolve(uint8 episodeId, uint8 kind, bytes calldata sa, bytes calldata sb) external {
        MethaneCovenant.Episode memory e = cov.getEpisode(episodeId);
        (Attestation[] memory atts, bytes[] memory sigs) =
            _pair(e.evidenceHash, e.emissionKgH, e.detectedAt, kind, sa, sb);
        cov.submitResolution(episodeId, atts, sigs);
    }

    function finalize(uint8 episodeId) external {
        cov.finalize(episodeId);
    }

    function fund(uint96 value) external {
        vm.deal(BUYER, value);
        vm.prank(BUYER);
        cov.fund{value: value}(cid);
    }

    function settle() external {
        cov.settle(cid);
    }

    function withdraw(uint8 who) external {
        address account = who == 0 ? SELLER : who == 1 ? BUYER : who == 2 ? PROPOSER : CHALLENGER;
        vm.prank(account);
        cov.withdraw();
    }

    function warp(uint32 dt) external {
        vm.warp(block.timestamp + dt);
    }
}

/// @notice Shared state of the core runs: the real KeyRegistry (seller, buyer, three observer orgs; admin renounced)
///         and the unchanged core with one covenant (5 ETH bond [SIM], k = 2, one-leaf source set), ten days in.
abstract contract CoreHalmosSetup is Test {
    address internal constant SELLER = address(0x5E11E7);
    address internal constant BUYER = address(0xB0FE7);
    address internal constant PROPOSER = address(0x9409);
    address internal constant CHALLENGER = address(0xC4A11);
    KeyRegistry internal registry;
    MethaneCovenant internal cov;
    bytes32 internal cid;
    CoreActions internal actions;

    function setUp() public {
        vm.warp(1_780_000_000);
        registry = new KeyRegistry(address(this));
        registry.registerKey("ORG_SELLER_SIM", KeyRegistry.Role.SELLER, SELLER, 1, 0);
        registry.registerKey("ORG_BUYER_SIM", KeyRegistry.Role.BUYER, BUYER, 1, 0);
        registry.registerKey("OBS_A", KeyRegistry.Role.OBSERVER, address(0xA1), 1, 0);
        registry.registerKey("OBS_B", KeyRegistry.Role.OBSERVER, address(0xB1), 1, 0);
        registry.registerKey("OBS_C", KeyRegistry.Role.OBSERVER, address(0xC1), 1, 0);
        registry.renounceAdmin();
        cov = new MethaneCovenant(registry, 0.0001 ether, 0.0002 ether);
        MethaneCovenant.CovenantParams memory cp;
        cp.buyer = BUYER;
        cp.sourceSetRoot = keccak256(bytes.concat(keccak256(abi.encode(keccak256("TKM_S_276"))))); // one-leaf set
        cp.policyHash = keccak256("policy-v1 [SIM]");
        cp.term = 365 days;
        cp.k = 2;
        cp.challengeWindow = 1 days;
        cp.contestWindow = 2 days;
        cp.linkWindow = 30 days;
        cp.minRateKgH = 100;
        cp.tierAmounts = [uint128(3 ether), 2 ether, 1 ether];
        vm.deal(SELLER, 5 ether);
        vm.prank(SELLER);
        cid = cov.createCovenant{value: 5 ether}(cp);
        vm.warp(block.timestamp + 10 days); // detections must fall after the covenant's start
        actions = new CoreActions(cov, cid);
        targetContract(address(actions));
    }

    /// I4: per covenant funded == paid + held + remaining + refunded; the core's ETH equals what it owes.
    function _i4() internal view {
        MethaneCovenant.Covenant memory c = cov.getCovenant(cid);
        assert(c.funded == c.paid + c.held + c.remaining + c.refunded);
        uint256 bonds;
        uint256 n = cov.episodeCount();
        for (uint256 id = 1; id <= n; ++id) {
            MethaneCovenant.Episode memory e = cov.getEpisode(id);
            bonds += e.proposerBond + e.challengerBond;
        }
        uint256 owed = cov.withdrawable(SELLER) + cov.withdrawable(BUYER) + cov.withdrawable(PROPOSER)
            + cov.withdrawable(CHALLENGER);
        assert(address(cov).balance == c.remaining + c.held + bonds + owed);
    }
}

/// @notice Halmos, bounded: I4 after every sequence of CoreActions calls up to `--invariant-depth`, with reachability
///         canaries in the same run.
contract CoreI4Halmos is CoreHalmosSetup {
    function invariant_I4_bondConservation() public view {
        _i4();
    }

    // ------------------------------------------------------------------ reachability canaries (expected to FAIL)

    function invariant_canary_noEpisode() public view {
        assert(cov.episodeCount() == 0);
    }

    function invariant_canary_nothingHeld() public view {
        assert(cov.getCovenant(cid).held == 0);
    }
}

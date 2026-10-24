// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {KeyRegistryV2} from "../../src/KeyRegistryV2.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {Attestation, AttestationLib, KIND_DETECT} from "../../src/lib/Attestation.sol";
import {CleanPass, CleanPassLib, KIND_CLEAN} from "../../src/lib/CleanPass.sol";

/// @notice Shared genesis: five observer orgs (members) A-E, two party orgs (seller, buyer); k floor 2; admission delay
///         600 s (the Anvil value; production 7 days).
abstract contract V2Base is Test {
    uint64 internal constant DELAY = 600;
    uint64 internal constant TTL = 1 hours; // proposalTtl on Anvil (production 7 days)
    uint256 internal constant K = 2;
    uint256 internal constant T0 = 1_780_000_000;

    bytes32 internal constant ORG_SELLER = "ORG_SELLER_SIM";
    bytes32 internal constant ORG_BUYER = "ORG_BUYER_SIM";
    bytes32 internal constant ORG_NEW = "OBS_NEW_SATELLITE";

    KeyRegistryV2 internal reg;
    bytes32[] internal members;
    address[] internal ctrls;
    address internal sellerCtrl = makeAddr("sellerCtrl");
    address internal buyerCtrl = makeAddr("buyerCtrl");
    address internal newCtrl = makeAddr("newCtrl");
    address internal outsider = makeAddr("outsider");

    function setUp() public virtual {
        vm.warp(T0);
        string[5] memory names = ["OBS_A", "OBS_B", "OBS_C", "OBS_D", "OBS_E"];
        for (uint256 i; i < 5; ++i) {
            members.push(bytes32(bytes(names[i])));
            ctrls.push(makeAddr(string.concat("ctrl_", names[i])));
        }
        bytes32[] memory parties = new bytes32[](2);
        parties[0] = ORG_SELLER;
        parties[1] = ORG_BUYER;
        address[] memory partyCtrls = new address[](2);
        partyCtrls[0] = sellerCtrl;
        partyCtrls[1] = buyerCtrl;
        reg = new KeyRegistryV2(members, ctrls, parties, partyCtrls, K, DELAY, TTL);
    }

    /// @dev Members 0..n-1 vote to admit ORG_NEW (the first one proposes).
    function _admit(uint256 n) internal {
        vm.prank(ctrls[0]);
        reg.proposeOrg(ORG_NEW, newCtrl);
        for (uint256 i = 1; i < n; ++i) {
            vm.prank(ctrls[i]);
            reg.voteOrg(ORG_NEW);
        }
    }
}

/// @notice Observer admission (spec-v2 §9.4; review M5; §9.7 R24): KeyRegistryV2, Anvil only. A new observer organisation joins by
///         a majority of the existing observer organisations, with a k floor and an admission delay; a symmetric vote
///         removes one and closes its keys. Only member orgs hold OBSERVER keys. The view interface is the v1
///         registry's, so the unchanged core and the pool run on it.
contract KeyRegistryV2Test is V2Base {
    event OrgAdmitted(
        bytes32 indexed orgId, address indexed controller, uint64 activeFrom, uint256 votes, uint256 members
    );
    event OrgRemoved(bytes32 indexed orgId, uint256 votes, uint256 members);

    // ================================================================== genesis

    function test_v2_genesis() public view {
        assertEq(reg.memberCount(), 5);
        assertEq(reg.minVotes(), K);
        assertEq(reg.admissionDelay(), DELAY);
        assertEq(reg.proposalTtl(), TTL);
        assertEq(reg.orgController(members[0]), ctrls[0]);
        assertEq(uint8(reg.orgKind(members[0])), uint8(KeyRegistryV2.OrgKind.MEMBER));
        assertEq(uint8(reg.orgKind(ORG_SELLER)), uint8(KeyRegistryV2.OrgKind.PARTY));
        assertEq(reg.threshold(), 3); // max(2, 5/2 + 1)
    }

    /// @dev External, so `vm.expectRevert` applies to this call (see SuretyBond.t.sol `deployBond`).
    function deployV2(bytes32[] memory m, address[] memory mc, bytes32[] memory pa, address[] memory pc, uint256 k)
        external
        returns (KeyRegistryV2)
    {
        return new KeyRegistryV2(m, mc, pa, pc, k, DELAY, TTL);
    }

    function test_v2_constructor_badArgs_revert() public {
        bytes32[] memory one = new bytes32[](1);
        one[0] = "OBS_A";
        address[] memory oneCtrl = new address[](1);
        oneCtrl[0] = ctrls[0];
        bytes32[] memory none = new bytes32[](0);
        address[] memory noneCtrl = new address[](0);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // fewer members than the k floor
        this.deployV2(one, oneCtrl, none, noneCtrl, K);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // k = 0
        this.deployV2(members, ctrls, none, noneCtrl, 0);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // length mismatch
        this.deployV2(members, oneCtrl, none, noneCtrl, K);
        bytes32[] memory dup = new bytes32[](2);
        dup[0] = "OBS_A";
        dup[1] = "OBS_A";
        address[] memory twoCtrl = new address[](2);
        twoCtrl[0] = ctrls[0];
        twoCtrl[1] = ctrls[1];
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // duplicate org
        this.deployV2(dup, twoCtrl, none, noneCtrl, K);
        twoCtrl[1] = ctrls[0];
        dup[1] = "OBS_B";
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // one controller for two orgs
        this.deployV2(dup, twoCtrl, none, noneCtrl, K);
        assertEq(address(this.deployV2(members, ctrls, none, noneCtrl, K)).code.length > 0, true);
    }

    // ================================================================== admission

    /// R24: with 5 members the threshold is 3 (majority), above the k floor of 2.
    function test_v2_admission_needsMajority() public {
        _admit(2);
        assertEq(uint8(reg.orgKind(ORG_NEW)), uint8(KeyRegistryV2.OrgKind.NONE)); // 2 of 5 is not enough
        (uint256 counted, uint256 needed) = reg.admissionVotes(ORG_NEW);
        assertEq(counted, 2);
        assertEq(needed, 3);

        vm.expectEmit(true, true, false, true, address(reg));
        emit OrgAdmitted(ORG_NEW, newCtrl, uint64(block.timestamp) + DELAY, 3, 5);
        vm.prank(ctrls[2]);
        reg.voteOrg(ORG_NEW);
        assertEq(uint8(reg.orgKind(ORG_NEW)), uint8(KeyRegistryV2.OrgKind.MEMBER));
        assertEq(reg.memberCount(), 6);
        assertEq(reg.orgController(ORG_NEW), newCtrl);
        assertEq(reg.threshold(), 4); // max(2, 6/2 + 1)
    }

    /// The k floor binds when the membership is small: 2 members, k = 2 -> both must vote.
    function test_v2_admission_kFloor() public {
        bytes32[] memory two = new bytes32[](2);
        two[0] = members[0];
        two[1] = members[1];
        address[] memory twoCtrl = new address[](2);
        twoCtrl[0] = ctrls[0];
        twoCtrl[1] = ctrls[1];
        KeyRegistryV2 small = new KeyRegistryV2(two, twoCtrl, new bytes32[](0), new address[](0), 2, DELAY, TTL);
        assertEq(small.threshold(), 2); // max(2, 2/2 + 1) = 2
        vm.prank(ctrls[0]);
        small.proposeOrg(ORG_NEW, newCtrl);
        assertEq(uint8(small.orgKind(ORG_NEW)), uint8(KeyRegistryV2.OrgKind.NONE));
        vm.prank(ctrls[1]);
        small.voteOrg(ORG_NEW);
        assertEq(uint8(small.orgKind(ORG_NEW)), uint8(KeyRegistryV2.OrgKind.MEMBER));
    }

    /// New keys are valid only from admittedAt + admissionDelay; the new org votes only from then on.
    function test_v2_admission_delay() public {
        _admit(3);
        uint64 activeFrom = uint64(block.timestamp) + DELAY;
        assertEq(reg.activeFrom(ORG_NEW), activeFrom);
        address k1 = makeAddr("newObserverKey");
        vm.prank(newCtrl);
        reg.registerKey(ORG_NEW, KeyRegistry.Role.OBSERVER, k1, 1, 0);
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp));
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, activeFrom - 1));
        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, activeFrom));
        (bytes32 org,, bool valid) = reg.keyStatus(k1, block.timestamp);
        assertEq(org, ORG_NEW);
        assertFalse(valid);

        bytes32 next = "OBS_NEXT";
        vm.prank(newCtrl);
        vm.expectRevert(KeyRegistryV2.NotVoter.selector); // not active yet
        reg.proposeOrg(next, makeAddr("nextCtrl"));
        vm.warp(activeFrom);
        vm.prank(newCtrl);
        reg.proposeOrg(next, makeAddr("nextCtrl"));
    }

    function test_v2_admission_nonMember_reverts() public {
        address[3] memory others = [outsider, sellerCtrl, newCtrl]; // a party controller is not a voter either
        for (uint256 i; i < 3; ++i) {
            vm.prank(others[i]);
            vm.expectRevert(KeyRegistryV2.NotVoter.selector);
            reg.proposeOrg(ORG_NEW, newCtrl);
        }
        vm.prank(ctrls[0]);
        reg.proposeOrg(ORG_NEW, newCtrl);
        vm.prank(outsider);
        vm.expectRevert(KeyRegistryV2.NotVoter.selector);
        reg.voteOrg(ORG_NEW);
    }

    function test_v2_admission_doubleVoteAndDuplicates_revert() public {
        vm.prank(ctrls[0]);
        reg.proposeOrg(ORG_NEW, newCtrl);
        vm.prank(ctrls[0]);
        vm.expectRevert(KeyRegistryV2.AlreadyVoted.selector);
        reg.voteOrg(ORG_NEW);
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // the controller is reserved by the live proposal
        reg.proposeOrg(ORG_NEW, newCtrl);
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.ProposalExists.selector); // and the org has a live proposal
        reg.proposeOrg(ORG_NEW, makeAddr("anotherCtrl"));
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.NoProposal.selector);
        reg.voteOrg("OBS_NOBODY_PROPOSED");
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // an existing org cannot be admitted again
        reg.proposeOrg(members[2], newCtrl);
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // a controller already controlling an org
        reg.proposeOrg("OBS_OTHER", ctrls[3]);
    }

    /// spec-v2 §15 A5 audit (2): votes are judged when cast. An org with a key revocation inside the last admission
    /// delay cannot vote (a compromised org cannot help admit a Sybil); once the quarantine has passed it can.
    function test_v2_recentRevocation_voteRejected() public {
        address k = makeAddr("keyC");
        vm.startPrank(ctrls[2]);
        reg.registerKey(members[2], KeyRegistry.Role.OBSERVER, k, 1, 0);
        reg.revokeKey(k);
        vm.stopPrank();

        vm.prank(ctrls[2]);
        vm.expectRevert(KeyRegistryV2.VoterQuarantined.selector);
        reg.proposeOrg(ORG_NEW, newCtrl);
        _admit(2); // A proposes, B votes
        vm.prank(ctrls[2]);
        vm.expectRevert(KeyRegistryV2.VoterQuarantined.selector);
        reg.voteOrg(ORG_NEW);
        (uint256 counted,) = reg.admissionVotes(ORG_NEW);
        assertEq(counted, 2);

        vm.warp(block.timestamp + DELAY);
        vm.prank(ctrls[2]);
        reg.voteOrg(ORG_NEW); // quarantine over: C's vote is accepted and counts
        assertEq(uint8(reg.orgKind(ORG_NEW)), uint8(KeyRegistryV2.OrgKind.MEMBER));
        (uint32 votes,,) = reg.admission(ORG_NEW);
        assertEq(votes, 3);
    }

    /// A vote once cast keeps counting: a revocation after the vote does not withdraw it (judged when cast).
    function test_v2_voteCastBeforeRevocation_stillCounts() public {
        _admit(2);
        address k = makeAddr("keyB");
        vm.startPrank(ctrls[1]);
        reg.registerKey(members[1], KeyRegistry.Role.OBSERVER, k, 1, 0);
        reg.revokeKey(k);
        vm.stopPrank();
        vm.prank(ctrls[2]);
        reg.voteOrg(ORG_NEW);
        assertEq(uint8(reg.orgKind(ORG_NEW)), uint8(KeyRegistryV2.OrgKind.MEMBER));
    }

    // ================================================================== proposals: TTL and controller reservation (A5 audit N1)

    /// A proposal expires after `proposalTtl`: late votes revert, and the org can be proposed again (fresh votes).
    function test_v2_proposal_expires_reProposable() public {
        _admit(2);
        vm.warp(block.timestamp + TTL);
        vm.prank(ctrls[2]);
        vm.expectRevert(KeyRegistryV2.ProposalExpired.selector);
        reg.voteOrg(ORG_NEW);

        address otherCtrl = makeAddr("otherCtrl");
        vm.prank(ctrls[3]);
        reg.proposeOrg(ORG_NEW, otherCtrl); // re-proposed, possibly with another controller
        (uint256 counted,) = reg.admissionVotes(ORG_NEW);
        assertEq(counted, 1); // the old votes do not carry over
        vm.prank(ctrls[0]);
        reg.voteOrg(ORG_NEW);
        vm.prank(ctrls[1]);
        reg.voteOrg(ORG_NEW);
        assertEq(reg.orgController(ORG_NEW), otherCtrl);
    }

    function test_v2_liveProposal_notReProposable() public {
        _admit(1);
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.ProposalExists.selector);
        reg.proposeOrg(ORG_NEW, makeAddr("otherCtrl"));
    }

    /// A controller address is reserved while proposed, so two proposals can never race for it (the earlier stuck
    /// case: one admitted, the other unable to complete or be re-proposed). The reservation ends with the TTL.
    function test_v2_controllerReservedWhileProposed() public {
        _admit(1);
        bytes32 other = "OBS_OTHER";
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.proposeOrg(other, newCtrl);

        vm.warp(block.timestamp + TTL); // ORG_NEW's proposal expired: its reservation too
        vm.prank(ctrls[1]);
        reg.proposeOrg(other, newCtrl);
        vm.prank(ctrls[2]);
        reg.voteOrg(other);
        vm.prank(ctrls[3]);
        reg.voteOrg(other);
        assertEq(reg.orgController(other), newCtrl);
        vm.prank(ctrls[0]); // ORG_NEW cannot take the same controller any more
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.proposeOrg(ORG_NEW, newCtrl);
    }

    /// A4 audit N3: clearing an expired proposal releases only its own reservation. org1(X) expires; org2 takes X;
    /// org1 is re-proposed with Y: X must stay reserved for org2, so org3(X) still reverts.
    function test_v2_reProposal_keepsOtherOrgsReservation() public {
        bytes32 org1 = "OBS_ONE";
        bytes32 org2 = "OBS_TWO";
        bytes32 org3 = "OBS_THREE";
        address x = makeAddr("controllerX");
        vm.prank(ctrls[0]);
        reg.proposeOrg(org1, x);
        vm.warp(block.timestamp + TTL); // org1's proposal expires
        vm.prank(ctrls[1]);
        reg.proposeOrg(org2, x); // X now reserved for org2
        vm.prank(ctrls[2]);
        reg.proposeOrg(org1, makeAddr("controllerY")); // re-proposing org1 clears org1's old record only
        assertEq(reg.reservedFor(x), org2);
        vm.prank(ctrls[3]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.proposeOrg(org3, x);
    }

    /// Security review item 5: two removals proposed while n = 3 (k = 2) cannot both execute: the k floor is
    /// re-checked when a removal executes, not only when it is proposed.
    function test_v2_concurrentRemovals_cannotDropBelowFloor() public {
        bytes32[] memory three = new bytes32[](3);
        address[] memory threeCtrl = new address[](3);
        for (uint256 i; i < 3; ++i) {
            three[i] = members[i];
            threeCtrl[i] = ctrls[i];
        }
        KeyRegistryV2 small = new KeyRegistryV2(three, threeCtrl, new bytes32[](0), new address[](0), 2, DELAY, TTL);
        assertEq(small.threshold(), 2);
        vm.prank(ctrls[1]);
        small.proposeRemoval(members[0]); // B: remove A
        vm.prank(ctrls[2]);
        small.proposeRemoval(members[1]); // C: remove B
        vm.prank(ctrls[0]);
        small.voteRemoval(members[1]); // A + C: B removed, n = 2
        assertEq(small.memberCount(), 2);

        vm.prank(ctrls[2]); // C's vote would give A's removal 2 votes (B's was cast while B was a member)
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        small.voteRemoval(members[0]);
        assertEq(small.memberCount(), 2);
        assertEq(uint8(small.orgKind(members[0])), uint8(KeyRegistryV2.OrgKind.MEMBER));
    }

    function test_v2_removalProposal_expires() public {
        vm.prank(ctrls[0]);
        reg.proposeRemoval(members[4]);
        vm.warp(block.timestamp + TTL);
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.ProposalExpired.selector);
        reg.voteRemoval(members[4]);
        vm.prank(ctrls[1]);
        reg.proposeRemoval(members[4]); // re-proposable after expiry
    }

    // ================================================================== removal

    /// R24: a symmetric majority removes an org and closes all its keys at once.
    function test_v2_removal_closesKeys() public {
        address k1 = makeAddr("keyE1");
        address k2 = makeAddr("keyE2");
        vm.startPrank(ctrls[4]);
        reg.registerKey(members[4], KeyRegistry.Role.OBSERVER, k1, 1, 0);
        reg.registerKey(members[4], KeyRegistry.Role.OBSERVER, k2, 2, keccak256("mldsa"));
        vm.stopPrank();
        vm.warp(block.timestamp + 1 days);

        vm.prank(ctrls[0]);
        reg.proposeRemoval(members[4]);
        vm.prank(ctrls[1]);
        reg.voteRemoval(members[4]);
        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp)); // 2 of 5: not yet

        vm.expectEmit(true, false, false, true, address(reg));
        emit OrgRemoved(members[4], 3, 5);
        vm.prank(ctrls[2]);
        reg.voteRemoval(members[4]);

        assertEq(uint8(reg.orgKind(members[4])), uint8(KeyRegistryV2.OrgKind.REMOVED));
        assertEq(reg.memberCount(), 4);
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp));
        assertFalse(reg.isValidAt(k2, KeyRegistry.Role.OBSERVER, block.timestamp));
        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp - 1)); // history kept
        assertEq(reg.orgController(members[4]), address(0));
        vm.prank(ctrls[4]);
        vm.expectRevert(KeyRegistryV2.NotAuthorized.selector); // the former controller manages nothing
        reg.registerKey(members[4], KeyRegistry.Role.OBSERVER, makeAddr("keyE3"), 1, 0);
        vm.prank(ctrls[4]);
        vm.expectRevert(KeyRegistryV2.NotVoter.selector); // nor votes
        reg.proposeOrg(ORG_NEW, newCtrl);
    }

    function test_v2_removal_rules_revert() public {
        vm.prank(ctrls[0]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // an org cannot propose its own removal
        reg.proposeRemoval(members[0]);
        vm.prank(ctrls[0]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // only members can be removed
        reg.proposeRemoval(ORG_SELLER);
        vm.prank(outsider);
        vm.expectRevert(KeyRegistryV2.NotVoter.selector);
        reg.proposeRemoval(members[1]);
        vm.prank(ctrls[0]);
        reg.proposeRemoval(members[1]);
        vm.prank(ctrls[1]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector); // the target does not vote on its own removal
        reg.voteRemoval(members[1]);
    }

    /// Removal never leaves fewer members than the k floor (the registry could no longer admit anyone).
    function test_v2_removal_keepsKFloor() public {
        bytes32[] memory two = new bytes32[](2);
        two[0] = members[0];
        two[1] = members[1];
        address[] memory twoCtrl = new address[](2);
        twoCtrl[0] = ctrls[0];
        twoCtrl[1] = ctrls[1];
        KeyRegistryV2 small = new KeyRegistryV2(two, twoCtrl, new bytes32[](0), new address[](0), 2, DELAY, TTL);
        vm.prank(ctrls[0]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        small.proposeRemoval(members[1]);
    }

    // ================================================================== keys: roles, rotation, revocation (v1 parity)

    function test_v2_roles_byOrgKind() public {
        vm.prank(sellerCtrl); // a party org cannot mint observer keys (no back door around admission)
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(ORG_SELLER, KeyRegistry.Role.OBSERVER, makeAddr("sneaky"), 1, 0);
        vm.prank(ctrls[0]); // and a member org cannot mint party keys
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(members[0], KeyRegistry.Role.SELLER, makeAddr("sneaky2"), 1, 0);
        vm.prank(sellerCtrl);
        reg.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, makeAddr("seller"), 1, 0);
        vm.prank(ctrls[0]); // a controller manages only its own org
        vm.expectRevert(KeyRegistryV2.NotAuthorized.selector);
        reg.registerKey(members[1], KeyRegistry.Role.OBSERVER, makeAddr("k"), 1, 0);
    }

    function test_v2_rotate_keepsHistory() public {
        address k1 = makeAddr("k1");
        address k2 = makeAddr("k2");
        vm.prank(ctrls[0]);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, k1, 1, 0);
        uint256 t1 = block.timestamp;
        vm.warp(t1 + 100);
        vm.prank(ctrls[0]);
        reg.rotateKey(k1, k2, 2, keccak256("mldsa-pk"));
        assertTrue(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, t1 + 99));
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, t1 + 100));
        assertTrue(reg.isValidAt(k2, KeyRegistry.Role.OBSERVER, t1 + 100));
        KeyRegistry.Key memory old = reg.getKey(k1);
        assertEq(old.replacedBy, k2);
        assertEq(old.validUntil, t1 + 100);
        assertEq(reg.getKey(k2).pqPubKeyHash, keccak256("mldsa-pk"));
        assertEq(reg.orgKeys(members[0]).length, 2);
        assertEq(reg.orgOf(k2), members[0]);
        vm.prank(ctrls[0]);
        vm.expectRevert(abi.encodeWithSelector(KeyRegistryV2.KeyNotActive.selector, k1));
        reg.rotateKey(k1, makeAddr("k3"), 1, 0);
        vm.prank(ctrls[0]);
        vm.expectRevert(abi.encodeWithSelector(KeyRegistryV2.KeyExists.selector, k1));
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, k1, 1, 0);
        vm.prank(ctrls[0]); // a rotation is not a revocation: the org's votes keep counting
        reg.proposeOrg(ORG_NEW, newCtrl);
        (uint256 counted,) = reg.admissionVotes(ORG_NEW);
        assertEq(counted, 1);
    }

    function test_v2_revoke_bySelfOrController() public {
        address k1 = makeAddr("k1");
        address k2 = makeAddr("k2");
        vm.startPrank(ctrls[0]);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, k1, 1, 0);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, k2, 1, 0);
        vm.stopPrank();
        vm.warp(block.timestamp + 1 days);
        vm.prank(k1);
        reg.revokeKey(k1);
        assertFalse(reg.isValidAt(k1, KeyRegistry.Role.OBSERVER, block.timestamp));
        assertEq(reg.lastRevocation(members[0]), block.timestamp);
        vm.prank(k1);
        vm.expectRevert(KeyRegistryV2.NotAuthorized.selector);
        reg.revokeKey(k2);
        vm.prank(ctrls[0]);
        reg.revokeKey(k2);
        vm.prank(ctrls[0]); // v1 parity: a closed key cannot be revoked again
        vm.expectRevert(abi.encodeWithSelector(KeyRegistryV2.KeyNotActive.selector, k2));
        reg.revokeKey(k2);
        vm.prank(ctrls[0]);
        vm.expectRevert(abi.encodeWithSelector(KeyRegistryV2.UnknownKey.selector, outsider));
        reg.revokeKey(outsider);

        // v1 parity: a key not yet valid (a new org inside its admission delay) is not active either
        vm.warp(block.timestamp + DELAY); // org A's own quarantine (it just revoked keys) ends first
        _admit(3);
        address kn = makeAddr("newKey");
        vm.prank(newCtrl);
        reg.registerKey(ORG_NEW, KeyRegistry.Role.OBSERVER, kn, 1, 0);
        vm.prank(newCtrl);
        vm.expectRevert(abi.encodeWithSelector(KeyRegistryV2.KeyNotActive.selector, kn));
        reg.revokeKey(kn);
    }

    function test_v2_invalidKeyParams_revert() public {
        vm.startPrank(ctrls[0]);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(members[0], KeyRegistry.Role.NONE, makeAddr("k"), 1, 0);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, makeAddr("k"), 2, 0); // hybrid needs a PQ hash
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, makeAddr("k"), 1, keccak256("x"));
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, makeAddr("k"), 3, 0);
        vm.expectRevert(KeyRegistryV2.InvalidParams.selector);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, address(0), 1, 0);
        vm.stopPrank();
    }

    function test_v2_holdsNoFunds_noAdmin() public {
        vm.deal(address(this), 1 ether);
        (bool ok,) = address(reg).call{value: 1 ether}("");
        assertFalse(ok);
        (ok,) = address(reg).call(abi.encodeWithSignature("admin()"));
        assertFalse(ok); // no admin role exists
    }

    // ================================================================== the unchanged core and the pool run on V2

    function test_v2_coreAndPoolRunOnV2() public {
        address seller = makeAddr("seller");
        address buyer = makeAddr("buyer");
        vm.prank(sellerCtrl);
        reg.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, seller, 1, 0);
        vm.prank(buyerCtrl);
        reg.registerKey(ORG_BUYER, KeyRegistry.Role.BUYER, buyer, 1, 0);
        // a newly admitted satellite operator and one genesis member sign a detection
        _admit(3);
        (address obsNew, uint256 obsNewPk) = makeAddrAndKey("obsNew");
        (address obsA, uint256 obsAPk) = makeAddrAndKey("obsA");
        vm.prank(newCtrl);
        reg.registerKey(ORG_NEW, KeyRegistry.Role.OBSERVER, obsNew, 1, 0);
        vm.prank(ctrls[0]);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, obsA, 1, 0);

        MethaneCovenant cov = new MethaneCovenant(KeyRegistry(address(reg)), 0.0001 ether, 0.0002 ether);
        IncentivePool pool = new IncentivePool(cov);
        assertEq(address(pool.registry()), address(reg));

        bytes32 src = keccak256("TKM_S_276");
        MethaneCovenant.CovenantParams memory p;
        p.buyer = buyer;
        p.sourceSetRoot = keccak256(bytes.concat(keccak256(abi.encode(src)))); // one-leaf set
        p.policyHash = keccak256("policy [SIM]");
        p.term = 365 days;
        p.k = 2;
        p.challengeWindow = 1 days;
        p.contestWindow = 2 days;
        p.linkWindow = 30 days;
        p.minRateKgH = 100;
        p.tierAmounts = [uint128(3 ether), 2 ether, 1 ether];
        vm.deal(seller, 10 ether);
        vm.prank(seller);
        bytes32 cId = cov.createCovenant{value: 5 ether}(p);

        vm.warp(block.timestamp + 1 days);
        bytes32 ev = keccak256("bundle [SIM]");
        Attestation[] memory atts = new Attestation[](2);
        bytes[] memory sigs = new bytes[](2);
        uint256[2] memory pks = [obsNewPk, obsAPk];
        for (uint256 i; i < 2; ++i) {
            atts[i] = Attestation({
                covenantId: cId,
                sourceId: src,
                evidenceHash: ev,
                modelHash: bytes32(0),
                emissionKgH: 2548,
                uncertaintyKgH: 254,
                detectedAt: uint64(block.timestamp - 1 hours),
                instrumentId: 1,
                providerId: "carbonmapper",
                kind: KIND_DETECT,
                nonce: uint64(i + 1),
                expiry: uint64(block.timestamp + 1 hours)
            });
            bytes32 digest = AttestationLib.toTypedDataHash(cov.DOMAIN_SEPARATOR(), AttestationLib.hashMemory(atts[i]));
            (uint8 v, bytes32 r, bytes32 s) = vm.sign(pks[i], digest);
            sigs[i] = abi.encodePacked(r, s, v);
        }
        address proposer = makeAddr("proposer");
        vm.deal(proposer, 1 ether);
        vm.prank(proposer);
        uint256 ep = cov.proposeEpisode{value: 0.0001 ether}(cId, src, new bytes32[](0), ev, atts, sigs);
        assertEq(uint8(cov.getEpisode(ep).state), uint8(MethaneCovenant.State.PROPOSED));
        assertEq(cov.getEpisode(ep).quorumOrgs, 2);
    }
}

/// @notice A5 audit N3: the pool reads V2 live (`keyStatus` when a clean pass is attested, `orgController` when its
///         observer fee is paid). A clean pass signed by a newly admitted org and by org E; E is then voted out before
///         the pass is counted: E's fee share is forfeited to the pool, the new org's controller is paid.
contract KeyRegistryV2PoolTest is V2Base {
    MethaneCovenant internal cov;
    IncentivePool internal pool;
    bytes32 internal cId;
    bytes32 internal src = keccak256("TKM_S_276");
    address internal buyer = makeAddr("buyer");
    uint256 internal obsNewPk;
    uint256 internal obsEPk;
    uint256 internal obsAPk;
    uint64 internal detectedAt;

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
        for (uint256 i; i < 32; ++i) {
            p.upperBoundBp[i] = uint16(9750 - i * 200);
        }
    }

    function _setupKeys() internal {
        address seller = makeAddr("seller");
        vm.prank(sellerCtrl);
        reg.registerKey(ORG_SELLER, KeyRegistry.Role.SELLER, seller, 1, 0);
        vm.prank(buyerCtrl);
        reg.registerKey(ORG_BUYER, KeyRegistry.Role.BUYER, buyer, 1, 0);
        _admit(3);
        address obsNew;
        address obsE;
        address obsA;
        (obsNew, obsNewPk) = makeAddrAndKey("obsNew");
        (obsE, obsEPk) = makeAddrAndKey("obsE");
        (obsA, obsAPk) = makeAddrAndKey("obsA");
        vm.prank(newCtrl);
        reg.registerKey(ORG_NEW, KeyRegistry.Role.OBSERVER, obsNew, 1, 0);
        vm.prank(ctrls[4]);
        reg.registerKey(members[4], KeyRegistry.Role.OBSERVER, obsE, 1, 0);
        vm.prank(ctrls[0]);
        reg.registerKey(members[0], KeyRegistry.Role.OBSERVER, obsA, 1, 0);

        cov = new MethaneCovenant(KeyRegistry(address(reg)), 0.0001 ether, 0.0002 ether);
        pool = new IncentivePool(cov);
        MethaneCovenant.CovenantParams memory cp;
        cp.buyer = buyer;
        cp.sourceSetRoot = keccak256(bytes.concat(keccak256(abi.encode(src))));
        cp.policyHash = keccak256("policy [SIM]");
        cp.term = 365 days;
        cp.k = 2;
        cp.challengeWindow = 1 days;
        cp.contestWindow = 2 days;
        cp.linkWindow = 30 days;
        cp.minRateKgH = 100;
        cp.tierAmounts = [uint128(3 ether), 2 ether, 1 ether];
        vm.deal(seller, 10 ether);
        vm.prank(seller);
        cId = cov.createCovenant{value: 5 ether}(cp);
    }

    function _confirmedEpisode() internal returns (uint256 ep) {
        vm.warp(block.timestamp + 1 days);
        detectedAt = uint64(block.timestamp - 1 hours);
        bytes32 ev = keccak256("bundle [SIM]");
        Attestation[] memory atts = new Attestation[](2);
        bytes[] memory sigs = new bytes[](2);
        uint256[2] memory pks = [obsNewPk, obsAPk];
        for (uint256 i; i < 2; ++i) {
            atts[i] = Attestation(
                cId,
                src,
                ev,
                bytes32(0),
                2548,
                254,
                detectedAt,
                1,
                "carbonmapper",
                KIND_DETECT,
                uint64(i + 1),
                uint64(block.timestamp + 1 hours)
            );
            bytes32 digest = AttestationLib.toTypedDataHash(cov.DOMAIN_SEPARATOR(), AttestationLib.hashMemory(atts[i]));
            (uint8 v, bytes32 r, bytes32 sg) = vm.sign(pks[i], digest);
            sigs[i] = abi.encodePacked(r, sg, v);
        }
        address proposer = makeAddr("proposer");
        vm.deal(proposer, 1 ether);
        vm.prank(proposer);
        ep = cov.proposeEpisode{value: 0.0001 ether}(cId, src, new bytes32[](0), ev, atts, sigs);
        vm.warp(block.timestamp + 1 days);
        cov.finalize(ep);
    }

    function _attestPass(bytes32 claimId) internal returns (uint256 passId) {
        uint64 sceneAt = detectedAt + 30 days + 1 days;
        vm.warp(uint256(sceneAt) + 60 days);
        CleanPass[] memory atts = new CleanPass[](2);
        bytes[] memory sigs = new bytes[](2);
        uint256[2] memory pks = [obsNewPk, obsEPk];
        for (uint256 i; i < 2; ++i) {
            atts[i] = CleanPass(
                claimId,
                src,
                keccak256("scene"),
                keccak256("bundle"),
                sceneAt,
                sceneAt + 5 days,
                1,
                "carbonmapper",
                500,
                true,
                100,
                100,
                KIND_CLEAN,
                uint64(100 + i),
                uint64(block.timestamp + 1 hours)
            );
            bytes32 digest = AttestationLib.toTypedDataHash(pool.DOMAIN_SEPARATOR(), CleanPassLib.hash(atts[i]));
            (uint8 v, bytes32 r, bytes32 sg) = vm.sign(pks[i], digest);
            sigs[i] = abi.encodePacked(r, sg, v);
        }
        address relayer = makeAddr("relayer");
        vm.deal(relayer, 1 ether);
        vm.prank(relayer);
        passId = pool.attestCleanPass{value: 0.001 ether}(claimId, atts, sigs); // keyStatus read from V2
    }

    function test_v2_poolReadsV2_feeToRemovedOrgForfeited() public {
        _setupKeys();
        uint256 ep = _confirmedEpisode();
        IncentivePool.PoolParams memory p = _params();
        vm.deal(buyer, 20 ether);
        vm.prank(buyer);
        pool.fund{value: 10 ether}(cId, keccak256("policy-v2 [SIM]"), IncentivePool.Mode.ONCHAIN, p);
        bytes32 claimId = pool.openClaim(ep);
        uint256 passId = _attestPass(claimId);
        assertEq(pool.getPass(passId).orgs.length, 2);

        // E is voted out (6 members: 4 votes needed) before the pass is counted
        vm.prank(ctrls[0]);
        reg.proposeRemoval(members[4]);
        for (uint256 i = 1; i < 4; ++i) {
            vm.prank(ctrls[i]);
            reg.voteRemoval(members[4]);
        }
        assertEq(uint8(reg.orgKind(members[4])), uint8(KeyRegistryV2.OrgKind.REMOVED));

        vm.warp(block.timestamp + 1 days);
        pool.countPass(passId); // orgController read from V2 for each signing org
        uint256 share = 0.0001 ether / 2;
        assertEq(pool.withdrawable(newCtrl), share);
        assertEq(pool.withdrawable(ctrls[4]), 0); // the removed org's former controller is not paid
        (IncentivePool.Pool memory pl,) = pool.getPool(cId);
        assertEq(pl.balance, 10 ether - share); // E's share stays with the sponsors
    }
}

/// @notice Drives KeyRegistryV2 with random proposals, votes, removals, revocations and time, from members and
///         outsiders, and re-implements the admission rule independently as a ghost.
contract V2Handler is Test {
    KeyRegistryV2 internal reg;
    uint64 internal delay;
    uint256 internal k;
    address[] internal actors; // genesis controllers, then candidate controllers, then outsiders
    bytes32[] internal candidates;
    mapping(bytes32 => address) internal candidateCtrl;
    mapping(bytes32 => bool) public isGenesis;

    // ghost mirror of the registry's membership, built only from this handler's own observations
    mapping(bytes32 => bool) public ghostMember;
    mapping(bytes32 => uint64) public ghostActiveFrom;
    mapping(bytes32 => uint64) public ghostLastRevocation;
    mapping(address => bytes32) internal ghostOrgOf; // controller -> org (members only)
    uint256 public ghostMembers;
    mapping(bytes32 => bytes32[]) internal ghostVoters; // candidate -> voter orgs (as the handler saw them succeed)
    mapping(bytes32 => bool) public admittedWithMajority; // the ghost recount agreed at admission
    uint256 public violations;
    address[] public registeredKeys;
    uint256 internal keyNonce;

    constructor(
        KeyRegistryV2 reg_,
        bytes32[] memory genesis,
        address[] memory genesisCtrls,
        uint64 delay_,
        uint256 k_
    ) {
        reg = reg_;
        delay = delay_;
        k = k_;
        for (uint256 i; i < genesis.length; ++i) {
            isGenesis[genesis[i]] = true;
            ghostMember[genesis[i]] = true;
            ghostOrgOf[genesisCtrls[i]] = genesis[i];
            actors.push(genesisCtrls[i]);
            ++ghostMembers;
        }
        for (uint256 i; i < 6; ++i) {
            bytes32 c = keccak256(abi.encode("candidate", i));
            candidates.push(c);
            address ctrl = makeAddr(string.concat("candidateCtrl", vm.toString(i)));
            candidateCtrl[c] = ctrl;
            actors.push(ctrl);
        }
        actors.push(makeAddr("outsiderA"));
        actors.push(makeAddr("outsiderB"));
    }

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    function _candidate(uint256 seed) internal view returns (bytes32) {
        return candidates[seed % candidates.length];
    }

    function _ghostCounts(bytes32 org) internal view returns (bool) {
        return ghostMember[org] && ghostActiveFrom[org] <= block.timestamp
            && (ghostLastRevocation[org] == 0 || block.timestamp >= ghostLastRevocation[org] + delay);
    }

    /// @dev Votes are judged when cast (spec-v2 §15 A5 audit (2)): the registry must have refused any vote the ghost
    ///      would refuse; a vote it accepted keeps counting.
    function _recordVote(bytes32 cand, bytes32 voterOrg) internal {
        if (!_ghostCounts(voterOrg)) ++violations; // accepted a vote from a non-member, inactive or quarantined org
        ghostVoters[cand].push(voterOrg);
    }

    function _afterAdmissionAttempt(bytes32 cand) internal {
        if (ghostMember[cand] || reg.orgKind(cand) != KeyRegistryV2.OrgKind.MEMBER) return;
        // it has just been admitted: recount independently (accepted votes of the live proposal)
        uint256 counted = ghostVoters[cand].length;
        uint256 needed = ghostMembers / 2 + 1;
        if (needed < k) needed = k;
        if (counted < needed) ++violations;
        else admittedWithMajority[cand] = true;
        ghostMember[cand] = true;
        ghostActiveFrom[cand] = uint64(block.timestamp) + delay;
        ghostOrgOf[candidateCtrl[cand]] = cand;
        ++ghostMembers;
    }

    function propose(uint256 actorSeed, uint256 candSeed) external {
        address a = _actor(actorSeed);
        bytes32 cand = _candidate(candSeed);
        vm.prank(a);
        try reg.proposeOrg(cand, candidateCtrl[cand]) {
            delete ghostVoters[cand]; // a (re-)proposal starts with the proposer's vote only
            _recordVote(cand, ghostOrgOf[a]);
            _afterAdmissionAttempt(cand);
        } catch {}
    }

    function vote(uint256 actorSeed, uint256 candSeed) external {
        address a = _actor(actorSeed);
        bytes32 cand = _candidate(candSeed);
        vm.prank(a);
        try reg.voteOrg(cand) {
            _recordVote(cand, ghostOrgOf[a]);
            _afterAdmissionAttempt(cand);
        } catch {}
    }

    function remove(uint256 actorSeed, uint256 targetSeed, bool propose_) external {
        address a = _actor(actorSeed);
        bytes32 target = ghostOrgOf[_actor(targetSeed)];
        if (target == bytes32(0)) return;
        vm.prank(a);
        if (propose_) {
            try reg.proposeRemoval(target) {} catch {}
        } else {
            try reg.voteRemoval(target) {} catch {}
        }
        if (ghostMember[target] && reg.orgKind(target) == KeyRegistryV2.OrgKind.REMOVED) {
            ghostMember[target] = false;
            --ghostMembers;
            for (uint256 i; i < actors.length; ++i) {
                if (ghostOrgOf[actors[i]] == target) ghostOrgOf[actors[i]] = bytes32(0);
            }
        }
    }

    /// A member registers an OBSERVER key and (optionally) revokes it at once: a recent revocation.
    function registerAndMaybeRevoke(uint256 actorSeed, bool revoke) external {
        address a = _actor(actorSeed);
        bytes32 org = ghostOrgOf[a];
        if (org == bytes32(0)) org = keccak256(abi.encode("not an org", actorSeed)); // unauthorized attempt
        address key = makeAddr(string.concat("key", vm.toString(++keyNonce)));
        vm.prank(a);
        try reg.registerKey(org, KeyRegistry.Role.OBSERVER, key, 1, 0) {
            registeredKeys.push(key);
            if (revoke) {
                vm.prank(a);
                reg.revokeKey(key);
                ghostLastRevocation[org] = uint64(block.timestamp);
            }
        } catch {}
    }

    function warp(uint256 secondsAhead) external {
        vm.warp(block.timestamp + bound(secondsAhead, 1, 3 * uint256(delay)));
    }

    function registeredKeyCount() external view returns (uint256) {
        return registeredKeys.length;
    }

    function candidateAt(uint256 i) external view returns (bytes32) {
        return candidates[i];
    }
}

/// @notice R24: `invariant_V2_noOrgWithoutMajority` — every org that became a member after genesis did so with at
///         least max(k, floor(n/2) + 1) counted votes, as recounted independently; plus membership bookkeeping and
///         "observer keys only in member orgs".
contract KeyRegistryV2Invariants is V2Base {
    V2Handler internal handler;

    function setUp() public override {
        super.setUp();
        handler = new V2Handler(reg, members, ctrls, DELAY, K);
        targetContract(address(handler));
    }

    function invariant_V2_noOrgWithoutMajority() public view {
        assertEq(handler.violations(), 0);
        for (uint256 i; i < 6; ++i) {
            bytes32 c = handler.candidateAt(i);
            if (reg.orgKind(c) == KeyRegistryV2.OrgKind.MEMBER || reg.orgKind(c) == KeyRegistryV2.OrgKind.REMOVED) {
                assertTrue(handler.admittedWithMajority(c));
                (uint256 votes, uint256 membersAt,) = reg.admission(c);
                uint256 needed = membersAt / 2 + 1;
                if (needed < K) needed = K;
                assertGe(votes, needed);
            }
        }
    }

    function invariant_V2_memberCountMatches() public view {
        assertEq(reg.memberCount(), handler.ghostMembers());
        assertGe(reg.memberCount(), K);
    }

    function invariant_V2_observerKeysOnlyInMembers() public view {
        uint256 n = handler.registeredKeyCount();
        for (uint256 i; i < n; ++i) {
            address key = handler.registeredKeys(i);
            (bytes32 org, KeyRegistry.Role role, bool valid) = reg.keyStatus(key, block.timestamp);
            if (valid && role == KeyRegistry.Role.OBSERVER) {
                assertEq(uint8(reg.orgKind(org)), uint8(KeyRegistryV2.OrgKind.MEMBER));
            }
        }
    }
}

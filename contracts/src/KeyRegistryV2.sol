// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {KeyRegistry} from "./KeyRegistry.sol";

/// @title KeyRegistryV2 — how a new satellite operator joins, without an admin (spec-v2 §9.4; Anvil only)
/// @notice The v1 registry's key model (org-scoped keys with full history; rotation and revocation close intervals,
///         nothing is deleted) with governance instead of an admin:
///         - observer organisations are *members*; a new one is admitted when `votes >= max(k, floor(n/2) + 1)` of the
///           member organisations' controllers agree (n = members now, k = the constructor's floor);
///         - its keys are valid only from `admittedAt + admissionDelay` (production 7 days; Anvil 600 s), and it votes
///           from then on;
///         - a symmetric vote removes a member and closes all its keys at once;
///         - votes are judged when cast: an organisation that revoked one of its keys within the last
///           `admissionDelay` cannot vote (a compromised organisation cannot help admit a Sybil); a vote once cast
///           keeps counting;
///         - a proposal expires after `proposalTtl` (production 7 days; Anvil 1 h) and can then be proposed again; a
///           controller address is reserved while it is proposed, so no proposal can be left stuck;
///         - only member organisations hold OBSERVER keys; seller / buyer keys belong to *party* organisations, which
///           are fixed at genesis in this prototype.
///         The view interface (`keyStatus`, `orgController`, `isValidAt`, `orgOf`, `getKey`, `orgKeys`) is the v1
///         registry's, so the unchanged core (`new MethaneCovenant(KeyRegistry(address(v2)), ...)`) and the pool run on
///         it. Limit (stated): admission changes the trusted set of every covenant on the registry; a production clause
///         pins its observer set per covenant (not built). Not deployed to Sepolia (the main registry's admin is
///         renounced). Holds no funds.
contract KeyRegistryV2 {
    enum OrgKind {
        NONE,
        MEMBER, // observer organisation: votes, holds OBSERVER keys
        PARTY, // seller / buyer organisation (genesis): holds SELLER / BUYER keys, never votes
        REMOVED
    }

    struct Proposal {
        address controller; // admissions only
        uint64 openedAt;
        bytes32[] voters; // voting organisations, each once
    }

    /// @notice How a decision passed: counted votes and the member count at that moment.
    struct Decision {
        uint32 votes;
        uint32 members;
        uint64 decidedAt;
    }

    uint8 public constant ALG_ECDSA = 1;
    uint8 public constant ALG_HYBRID_MLDSA44 = 2;

    uint256 public immutable minVotes; // the k floor
    uint64 public immutable admissionDelay;
    uint64 public immutable proposalTtl;

    uint256 public memberCount;
    mapping(bytes32 orgId => address) public orgController;
    mapping(bytes32 orgId => OrgKind) public orgKind;
    mapping(bytes32 orgId => uint64) public activeFrom; // keys and votes of the org count from here
    mapping(bytes32 orgId => uint64) public lastRevocation; // last key revocation (not rotation)
    mapping(bytes32 orgId => Decision) public admission;
    mapping(bytes32 orgId => Decision) public removal;
    mapping(address controller => bytes32 orgId) public controllerOrg;
    mapping(address controller => bytes32 orgId) public reservedFor; // the admission proposal naming it (while live)
    mapping(address key => KeyRegistry.Key) private _keys;
    mapping(bytes32 orgId => address[]) private _orgKeys;
    mapping(bytes32 orgId => Proposal) private _admissions;
    mapping(bytes32 orgId => Proposal) private _removals;

    // v1 key events (same signatures)
    event OrgControllerSet(bytes32 indexed orgId, address indexed controller);
    event KeyRegistered(
        bytes32 indexed orgId,
        address indexed key,
        KeyRegistry.Role role,
        uint8 algId,
        bytes32 pqPubKeyHash,
        uint64 validFrom
    );
    event KeyRotated(bytes32 indexed orgId, address indexed oldKey, address indexed newKey, uint64 timestamp);
    event KeyRevoked(bytes32 indexed orgId, address indexed key, uint64 timestamp);
    // governance
    event OrgProposed(bytes32 indexed orgId, address indexed controller, bytes32 indexed proposerOrg);
    event OrgVoted(bytes32 indexed orgId, bytes32 indexed voterOrg, uint256 counted, uint256 needed);
    event OrgAdmitted(
        bytes32 indexed orgId, address indexed controller, uint64 activeFrom, uint256 votes, uint256 members
    );
    event RemovalProposed(bytes32 indexed orgId, bytes32 indexed proposerOrg);
    event RemovalVoted(bytes32 indexed orgId, bytes32 indexed voterOrg, uint256 counted, uint256 needed);
    event OrgRemoved(bytes32 indexed orgId, uint256 votes, uint256 members);

    error NotAuthorized();
    error InvalidParams();
    error KeyExists(address key);
    error UnknownKey(address key);
    error KeyNotActive(address key);
    error NotVoter();
    error AlreadyVoted();
    error ProposalExists();
    error NoProposal();
    error ProposalExpired();
    error VoterQuarantined();

    /// @param memberOrgs genesis observer organisations (at least `minVotes_`), with their controllers.
    /// @param partyOrgs genesis seller / buyer organisations, with their controllers.
    constructor(
        bytes32[] memory memberOrgs,
        address[] memory memberControllers,
        bytes32[] memory partyOrgs,
        address[] memory partyControllers,
        uint256 minVotes_,
        uint64 admissionDelay_,
        uint64 proposalTtl_
    ) {
        if (
            minVotes_ == 0 || proposalTtl_ == 0 || memberOrgs.length < minVotes_
                || memberOrgs.length != memberControllers.length || partyOrgs.length != partyControllers.length
        ) revert InvalidParams();
        minVotes = minVotes_;
        admissionDelay = admissionDelay_;
        proposalTtl = proposalTtl_;
        for (uint256 i; i < memberOrgs.length; ++i) {
            _addOrg(memberOrgs[i], memberControllers[i], OrgKind.MEMBER);
        }
        for (uint256 i; i < partyOrgs.length; ++i) {
            _addOrg(partyOrgs[i], partyControllers[i], OrgKind.PARTY);
        }
        memberCount = memberOrgs.length;
    }

    // ------------------------------------------------------------------ governance: admission

    /// @notice An active member controller proposes a new observer organisation (and votes for it).
    function proposeOrg(bytes32 orgId, address controller) external {
        bytes32 voterOrg = _voterOrg();
        if (orgId == bytes32(0) || controller == address(0) || orgKind[orgId] != OrgKind.NONE) revert InvalidParams();
        if (controllerOrg[controller] != bytes32(0) || _reserved(controller)) revert InvalidParams();
        Proposal storage p = _admissions[orgId];
        if (_live(p)) revert ProposalExists();
        _reset(orgId, p); // an expired proposal is replaced: its votes and its controller reservation end
        p.controller = controller;
        p.openedAt = uint64(block.timestamp);
        p.voters.push(voterOrg);
        reservedFor[controller] = orgId;
        emit OrgProposed(orgId, controller, voterOrg);
        _tryAdmit(orgId, p, voterOrg);
    }

    /// @notice An active member controller supports an open admission proposal.
    function voteOrg(bytes32 orgId) external {
        bytes32 voterOrg = _voterOrg();
        Proposal storage p = _admissions[orgId];
        _requireLive(p);
        _addVote(p, voterOrg);
        _tryAdmit(orgId, p, voterOrg);
    }

    // ------------------------------------------------------------------ governance: removal

    /// @notice An active member controller proposes removing another member (and votes for it). A removal that would
    ///         leave fewer members than the k floor is refused: the registry could no longer admit anyone.
    function proposeRemoval(bytes32 orgId) external {
        bytes32 voterOrg = _voterOrg();
        if (orgKind[orgId] != OrgKind.MEMBER || orgId == voterOrg || memberCount - 1 < minVotes) {
            revert InvalidParams();
        }
        Proposal storage p = _removals[orgId];
        if (_live(p)) revert ProposalExists();
        _reset(orgId, p);
        p.openedAt = uint64(block.timestamp);
        p.voters.push(voterOrg);
        emit RemovalProposed(orgId, voterOrg);
        _tryRemove(orgId, p, voterOrg);
    }

    /// @notice An active member controller (not the target's) supports an open removal proposal.
    function voteRemoval(bytes32 orgId) external {
        bytes32 voterOrg = _voterOrg();
        if (orgId == voterOrg) revert InvalidParams(); // the target does not vote on its own removal
        Proposal storage p = _removals[orgId];
        _requireLive(p);
        _addVote(p, voterOrg);
        _tryRemove(orgId, p, voterOrg);
    }

    /// @notice Votes needed now: max(k, floor(n/2) + 1).
    function threshold() public view returns (uint256) {
        uint256 majority = memberCount / 2 + 1;
        return majority > minVotes ? majority : minVotes;
    }

    /// @notice Votes cast on the live admission proposal (judged when cast) and votes needed now.
    function admissionVotes(bytes32 orgId) external view returns (uint256 counted, uint256 needed) {
        Proposal storage p = _admissions[orgId];
        return (_live(p) ? p.voters.length : 0, threshold());
    }

    // ------------------------------------------------------------------ keys (v1 model, roles by organisation kind)

    /// @notice The organisation's controller registers a key: OBSERVER keys only in member organisations, SELLER /
    ///         BUYER keys only in party organisations. Valid from now, or from the organisation's `activeFrom` if later.
    function registerKey(bytes32 orgId, KeyRegistry.Role role, address addr, uint8 algId, bytes32 pqPubKeyHash)
        external
    {
        _onlyController(orgId);
        _register(orgId, role, addr, algId, pqPubKeyHash);
    }

    /// @notice Closes `oldKey` now and registers `newKey` for the same organisation and role (not a revocation).
    function rotateKey(address oldKey, address newKey, uint8 algId, bytes32 pqPubKeyHash) external {
        KeyRegistry.Key storage k = _keys[oldKey];
        if (k.role == KeyRegistry.Role.NONE) revert UnknownKey(oldKey);
        _onlyController(k.orgId);
        if (!_validAt(k, block.timestamp)) revert KeyNotActive(oldKey);
        k.validUntil = uint64(block.timestamp);
        k.replacedBy = newKey;
        _register(k.orgId, k.role, newKey, algId, pqPubKeyHash);
        emit KeyRotated(k.orgId, oldKey, newKey, uint64(block.timestamp));
    }

    /// @notice Closes `key` now. By the organisation's controller or by the key itself (compromise report). Marks a
    ///         recent revocation for the organisation (its governance votes are ignored for `admissionDelay`).
    function revokeKey(address key) external {
        KeyRegistry.Key storage k = _keys[key];
        if (k.role == KeyRegistry.Role.NONE) revert UnknownKey(key);
        if (msg.sender != key) _onlyController(k.orgId);
        if (!_validAt(k, block.timestamp)) revert KeyNotActive(key); // v1 parity: closed or not yet valid
        k.validUntil = uint64(block.timestamp);
        lastRevocation[k.orgId] = uint64(block.timestamp);
        emit KeyRevoked(k.orgId, key, uint64(block.timestamp));
    }

    // ------------------------------------------------------------------ views (v1 interface)

    function keyStatus(address addr, uint256 ts)
        external
        view
        returns (bytes32 orgId, KeyRegistry.Role role, bool valid)
    {
        KeyRegistry.Key storage k = _keys[addr];
        return (k.orgId, k.role, k.role != KeyRegistry.Role.NONE && _validAt(k, ts));
    }

    function isValidAt(address addr, KeyRegistry.Role role, uint256 ts) external view returns (bool) {
        KeyRegistry.Key storage k = _keys[addr];
        return k.role == role && role != KeyRegistry.Role.NONE && _validAt(k, ts);
    }

    function orgOf(address addr) external view returns (bytes32) {
        return _keys[addr].orgId;
    }

    function getKey(address addr) external view returns (KeyRegistry.Key memory) {
        return _keys[addr];
    }

    function orgKeys(bytes32 orgId) external view returns (address[] memory) {
        return _orgKeys[orgId];
    }

    // ------------------------------------------------------------------ internal

    function _addOrg(bytes32 orgId, address controller, OrgKind kind) private {
        if (orgId == bytes32(0) || controller == address(0) || orgKind[orgId] != OrgKind.NONE) revert InvalidParams();
        if (controllerOrg[controller] != bytes32(0)) revert InvalidParams();
        orgKind[orgId] = kind;
        orgController[orgId] = controller;
        controllerOrg[controller] = orgId;
        emit OrgControllerSet(orgId, controller);
    }

    /// @dev The caller must control an active member organisation.
    function _voterOrg() private view returns (bytes32 orgId) {
        orgId = controllerOrg[msg.sender];
        if (orgId == bytes32(0) || orgKind[orgId] != OrgKind.MEMBER || block.timestamp < activeFrom[orgId]) {
            revert NotVoter();
        }
        uint64 revokedAt = lastRevocation[orgId];
        if (revokedAt != 0 && block.timestamp < uint256(revokedAt) + admissionDelay) revert VoterQuarantined();
    }

    function _live(Proposal storage p) private view returns (bool) {
        return p.openedAt != 0 && block.timestamp < uint256(p.openedAt) + proposalTtl;
    }

    function _requireLive(Proposal storage p) private view {
        if (p.openedAt == 0) revert NoProposal();
        if (!_live(p)) revert ProposalExpired();
    }

    /// @dev Clears a (dead) proposal for `orgId`: votes, and the controller reservation it held, only if that
    ///      reservation is still this org's (an expired proposal's controller may since be reserved by another org).
    function _reset(bytes32 orgId, Proposal storage p) private {
        address c = p.controller;
        if (c != address(0) && reservedFor[c] == orgId) delete reservedFor[c];
        delete p.controller;
        delete p.openedAt;
        delete p.voters;
    }

    /// @dev A controller address is reserved while a live admission proposal names it.
    function _reserved(address controller) private view returns (bool) {
        bytes32 orgId = reservedFor[controller];
        if (orgId == bytes32(0)) return false;
        Proposal storage p = _admissions[orgId];
        return p.controller == controller && _live(p);
    }

    function _addVote(Proposal storage p, bytes32 voterOrg) private {
        uint256 n = p.voters.length;
        for (uint256 i; i < n; ++i) {
            if (p.voters[i] == voterOrg) revert AlreadyVoted();
        }
        p.voters.push(voterOrg);
    }

    function _tryAdmit(bytes32 orgId, Proposal storage p, bytes32 voterOrg) private {
        uint256 counted = p.voters.length; // judged when cast
        uint256 needed = threshold();
        emit OrgVoted(orgId, voterOrg, counted, needed);
        if (counted < needed) return;
        address controller = p.controller;
        uint256 members = memberCount;
        _reset(orgId, p);
        _addOrg(orgId, controller, OrgKind.MEMBER);
        uint64 from = uint64(block.timestamp) + admissionDelay;
        activeFrom[orgId] = from;
        memberCount = members + 1;
        admission[orgId] = Decision(uint32(counted), uint32(members), uint64(block.timestamp));
        emit OrgAdmitted(orgId, controller, from, counted, members);
    }

    function _tryRemove(bytes32 orgId, Proposal storage p, bytes32 voterOrg) private {
        uint256 counted = p.voters.length; // judged when cast
        uint256 needed = threshold();
        emit RemovalVoted(orgId, voterOrg, counted, needed);
        if (counted < needed) return;
        uint256 members = memberCount;
        // the k floor is re-checked at execution: concurrent removals must not leave fewer members than k
        if (members - 1 < minVotes) revert InvalidParams();
        _reset(orgId, p);
        orgKind[orgId] = OrgKind.REMOVED;
        memberCount = members - 1;
        address controller = orgController[orgId];
        delete controllerOrg[controller];
        delete orgController[orgId];
        emit OrgControllerSet(orgId, address(0));
        address[] storage keys = _orgKeys[orgId];
        uint64 nowTs = uint64(block.timestamp);
        for (uint256 i; i < keys.length; ++i) {
            KeyRegistry.Key storage k = _keys[keys[i]];
            if (k.validUntil == 0 || k.validUntil > nowTs) {
                k.validUntil = nowTs;
                emit KeyRevoked(orgId, keys[i], nowTs);
            }
        }
        removal[orgId] = Decision(uint32(counted), uint32(members), nowTs);
        emit OrgRemoved(orgId, counted, members);
    }

    function _onlyController(bytes32 orgId) private view {
        address c = orgController[orgId];
        if (c == address(0) || msg.sender != c) revert NotAuthorized();
    }

    function _register(bytes32 orgId, KeyRegistry.Role role, address addr, uint8 algId, bytes32 pqPubKeyHash) private {
        if (addr == address(0) || role == KeyRegistry.Role.NONE) revert InvalidParams();
        OrgKind kind = orgKind[orgId];
        bool observer = role == KeyRegistry.Role.OBSERVER;
        if (observer ? kind != OrgKind.MEMBER : kind != OrgKind.PARTY) revert InvalidParams();
        if (algId == ALG_ECDSA) {
            if (pqPubKeyHash != bytes32(0)) revert InvalidParams();
        } else if (algId == ALG_HYBRID_MLDSA44) {
            if (pqPubKeyHash == bytes32(0)) revert InvalidParams();
        } else {
            revert InvalidParams();
        }
        if (_keys[addr].role != KeyRegistry.Role.NONE) revert KeyExists(addr);
        uint64 from = activeFrom[orgId];
        if (from < block.timestamp) from = uint64(block.timestamp);
        _keys[addr] = KeyRegistry.Key({
            orgId: orgId,
            role: role,
            algId: algId,
            validFrom: from,
            validUntil: 0,
            pqPubKeyHash: pqPubKeyHash,
            replacedBy: address(0)
        });
        _orgKeys[orgId].push(addr);
        emit KeyRegistered(orgId, addr, role, algId, pqPubKeyHash, from);
    }

    function _validAt(KeyRegistry.Key storage k, uint256 ts) private view returns (bool) {
        return ts >= k.validFrom && (k.validUntil == 0 || ts < k.validUntil);
    }
}

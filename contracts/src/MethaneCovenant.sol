// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {KeyRegistry} from "./KeyRegistry.sol";
import {Attestation, AttestationLib, KIND_DETECT, KIND_CONFIRM, KIND_WITHDRAW} from "./lib/Attestation.sol";
import {SigLib} from "./lib/SigLib.sol";

/// @title MethaneCovenant
/// @notice A methane clause for a gas supply contract. The seller posts a bond for a committed set of sources; a
///         positive re-detection at a covered source, attested by k distinct independent observer orgs, rebates the
///         buyer from the bond after a challenge window. There is no function that accepts "no plume".
/// @dev Spec: docs/spec.md §3 (mechanism) and §6 (on-chain design). Pull payments only; no owner, no admin.
///      Accounting per covenant: funded == paid + held + remaining + refunded.
contract MethaneCovenant {
    // ------------------------------------------------------------------ types

    enum State {
        NONE,
        PROPOSED,
        CHALLENGED,
        CONTESTED,
        CONFIRMED, // terminal; rebate credited to the buyer (pull payment = "PAID")
        VOID, // terminal; withdrawn by observers after an objective challenge
        EXPIRED, // terminal; unresolved challenge/contest; held rebate returned to bond, nobody slashed
        LINKED // terminal; detection inside linkWindow of a parent episode, recorded without payout
    }

    struct CovenantParams {
        address buyer;
        bytes32 sourceSetRoot; // Merkle root over SigLib.sourceLeaf(sourceId)
        bytes32 policyHash; // SHA-256 of canonical policy JSON, committed before evidence
        uint64 term; // seconds from `start`
        uint64 start; // 0 = now; else a backdated start <= MAX_LOOKBACK ago, which must also be inside policyHash
        uint8 k; // distinct observer orgs required
        uint64 challengeWindow;
        uint64 contestWindow;
        uint64 linkWindow;
        uint32 minRateKgH;
        uint128[3] tierAmounts; // [tier1 >= 5 t/h, tier2 1.5-5 t/h, tier3 minRate-1.5 t/h]
    }

    struct Covenant {
        address seller;
        uint64 start;
        uint32 openEpisodes;
        bool settled;
        address buyer;
        uint64 end;
        uint64 createdAt; // when the covenant reached the chain; start may be earlier (backdated, see createCovenant)
        uint8 k;
        uint32 minRateKgH;
        bytes32 sellerOrg;
        bytes32 buyerOrg;
        bytes32 sourceSetRoot;
        bytes32 policyHash;
        uint64 challengeWindow;
        uint64 contestWindow;
        uint64 linkWindow;
        uint128[3] tierAmounts;
        uint256 funded;
        uint256 remaining;
        uint256 held;
        uint256 paid;
        uint256 refunded;
    }

    struct Episode {
        bytes32 covenantId;
        bytes32 sourceId;
        bytes32 evidenceHash;
        address proposer;
        uint96 proposerBond; // open bond still held by this contract
        address challenger;
        uint96 challengerBond; // open bond still held by this contract
        uint64 detectedAt;
        uint64 proposedAt;
        uint64 deadline;
        uint32 emissionKgH;
        uint8 tier;
        uint8 quorumOrgs;
        State state;
        uint8 reasonCode;
        uint64 parentEpisode;
        uint128 rebate;
    }

    /// @dev Expected values every attestation in a batch must carry.
    struct Expect {
        bytes32 covenantId;
        bytes32 sourceId;
        bytes32 evidenceHash;
        bytes32 sellerOrg;
        bytes32 buyerOrg;
        uint8 kind;
        uint256 required;
    }

    // ------------------------------------------------------------------ constants

    /// @notice Objective, re-checkable challenge reasons. Scientific disputes go to `contest` (hold, never slash).
    uint8 public constant REASON_HASH_MISMATCH = 1;
    uint8 public constant REASON_SOURCE_NOT_COVERED = 2;
    uint8 public constant REASON_DUPLICATE_PLUME = 3;
    uint8 public constant REASON_REVOKED_KEY = 4;
    uint8 public constant REASON_STALE = 5;

    /// @notice IEA response levels (kg/h).
    uint32 public constant TIER1_MIN_KGH = 5000;
    uint32 public constant TIER2_MIN_KGH = 1500;

    uint256 public constant MAX_ATTESTATIONS = 16;
    uint256 public constant MAX_EXPIRY_HORIZON = 1 days;
    /// @notice How far back a covenant's start may be dated. A backdated covenant can cover detections that were
    ///         already public when it was created; `createdAt` records that on-chain so nobody can hide it.
    uint256 public constant MAX_LOOKBACK = 365 days;

    // ------------------------------------------------------------------ storage

    KeyRegistry public immutable registry;
    uint96 public immutable proposerBond;
    uint96 public immutable challengerBond;

    uint256 private immutable _cachedChainId;
    bytes32 private immutable _cachedDomainSeparator;

    uint256 public covenantCount;
    uint256 public episodeCount;

    mapping(bytes32 covenantId => Covenant) private _covenants;
    mapping(uint256 episodeId => Episode) private _episodes;
    /// @notice Latest non-linked episode per (covenant, source); anchor for linkWindow.
    mapping(bytes32 covenantId => mapping(bytes32 sourceId => uint256)) public latestEpisode;
    mapping(bytes32 evidenceHash => bool) public evidenceSeen;
    mapping(address signer => mapping(uint256 word => uint256)) public nonceBitmap;
    mapping(address account => uint256) public withdrawable;

    // ------------------------------------------------------------------ events

    event CovenantCreated(
        bytes32 indexed covenantId,
        address indexed seller,
        address indexed buyer,
        bytes32 sourceSetRoot,
        bytes32 policyHash,
        uint64 start,
        uint64 end,
        uint64 createdAt,
        uint8 k
    );
    event CovenantFunded(bytes32 indexed covenantId, address indexed from, uint256 amount);
    event EpisodeProposed(
        uint256 indexed episodeId,
        bytes32 indexed covenantId,
        bytes32 indexed sourceId,
        bytes32 evidenceHash,
        uint256 parentEpisode,
        uint32 emissionKgH,
        uint8 tier,
        uint256 rebate,
        uint8 quorumOrgs
    );
    event DetectionLinked(
        uint256 indexed episodeId,
        uint256 indexed parentEpisode,
        bytes32 indexed covenantId,
        bytes32 sourceId,
        bytes32 evidenceHash,
        uint32 emissionKgH
    );
    event EpisodeChallenged(uint256 indexed episodeId, address indexed challenger, uint8 reasonCode);
    event EpisodeContested(uint256 indexed episodeId);
    event EpisodeConfirmed(uint256 indexed episodeId, uint256 rebate);
    event EpisodeVoided(uint256 indexed episodeId, address indexed challenger, uint256 award);
    event EpisodeExpired(uint256 indexed episodeId);
    event MitigationClaimRecorded(
        bytes32 indexed covenantId, bytes32 indexed sourceId, bytes32 claimHash, address indexed seller
    );
    event Credited(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);
    event CovenantSettled(bytes32 indexed covenantId, uint256 refund);

    // ------------------------------------------------------------------ errors (spec §6)

    error NotCovered(bytes32 sourceId);
    error QuorumNotMet(uint256 distinctOrgs, uint256 required);
    error AttestorNotIndependent(address signer);
    error SelfAttestation(address signer);
    error Replay(address signer, uint64 nonce);
    error Expired(uint64 expiry);
    error RevokedKey(address signer);
    error EpisodeLinked(uint256 episodeId);
    error WindowOpen();
    error WindowClosed();
    error NothingToFinalize(uint256 episodeId);

    // ------------------------------------------------------------------ errors (additional)

    error InvalidSignature();
    error AttestationMismatch(uint256 index);
    error DuplicateEvidence(bytes32 evidenceHash);
    error DetectionOutOfTerm(uint64 detectedAt);
    error BelowMinRate(uint32 emissionKgH);
    error BadBond(uint256 sent, uint256 required);
    error InvalidParams();
    error InvalidState(uint256 episodeId);
    error InvalidReason(uint8 reasonCode);
    error NotAuthorized();
    error UnknownCovenant(bytes32 covenantId);
    error NothingToWithdraw();
    error TransferFailed();

    // ------------------------------------------------------------------ constructor

    constructor(KeyRegistry registry_, uint96 proposerBond_, uint96 challengerBond_) {
        if (address(registry_) == address(0)) revert InvalidParams();
        registry = registry_;
        proposerBond = proposerBond_;
        challengerBond = challengerBond_;
        _cachedChainId = block.chainid;
        _cachedDomainSeparator = AttestationLib.domainSeparator(block.chainid, address(this));
    }

    // ------------------------------------------------------------------ covenant lifecycle

    /// @notice Seller (registered SELLER key) creates a covenant for a registered BUYER key. Payable: initial bond.
    ///         `start` may be backdated by up to MAX_LOOKBACK (0 = now); the term runs from `start`.
    function createCovenant(CovenantParams calldata p) external payable returns (bytes32 covenantId) {
        (bytes32 sellerOrg, bytes32 buyerOrg) = _partyOrgs(msg.sender, p.buyer);
        uint64 start = p.start == 0 ? uint64(block.timestamp) : p.start;
        if (
            p.k == 0 || p.k >= MAX_ATTESTATIONS || p.term == 0 || p.challengeWindow == 0 || p.contestWindow == 0
                || p.sourceSetRoot == bytes32(0) || p.policyHash == bytes32(0) || p.minRateKgH == 0
                || start > block.timestamp || block.timestamp - start > MAX_LOOKBACK
                || uint256(start) + p.term <= block.timestamp
        ) revert InvalidParams();

        covenantId = bytes32(++covenantCount);
        Covenant storage c = _covenants[covenantId];
        c.seller = msg.sender;
        c.buyer = p.buyer;
        c.sellerOrg = sellerOrg;
        c.buyerOrg = buyerOrg;
        c.sourceSetRoot = p.sourceSetRoot;
        c.policyHash = p.policyHash;
        c.start = start;
        c.end = start + p.term;
        c.createdAt = uint64(block.timestamp);
        c.k = p.k;
        c.challengeWindow = p.challengeWindow;
        c.contestWindow = p.contestWindow;
        c.linkWindow = p.linkWindow;
        c.minRateKgH = p.minRateKgH;
        c.tierAmounts = p.tierAmounts;

        emit CovenantCreated(
            covenantId, msg.sender, p.buyer, p.sourceSetRoot, p.policyHash, c.start, c.end, c.createdAt, c.k
        );
        if (msg.value > 0) _fund(c, covenantId);
    }

    /// @notice Anyone may top up the bond before the term ends.
    function fund(bytes32 covenantId) external payable {
        Covenant storage c = _covenant(covenantId);
        if (msg.value == 0) revert InvalidParams();
        _fund(c, covenantId);
    }

    /// @notice After term + challengeWindow and with no open episodes, the remaining bond returns to the seller.
    function settle(bytes32 covenantId) external {
        Covenant storage c = _covenant(covenantId);
        if (c.settled) revert WindowClosed();
        if (block.timestamp < uint256(c.end) + c.challengeWindow || c.openEpisodes != 0) revert WindowOpen();
        c.settled = true;
        uint256 refund = c.remaining;
        c.remaining = 0;
        c.refunded += refund;
        _credit(c.seller, refund);
        emit CovenantSettled(covenantId, refund);
    }

    // ------------------------------------------------------------------ episodes

    /// @notice Propose a positive detection at a covered source with >= k attestations from distinct independent
    ///         observer orgs. Payable: exactly `proposerBond`. A detection within `linkWindow` of the latest live or
    ///         confirmed episode at the same source is recorded as LINKED (no payout, bond returned).
    function proposeEpisode(
        bytes32 covenantId,
        bytes32 sourceId,
        bytes32[] calldata merkleProof,
        bytes32 evidenceHash,
        Attestation[] calldata atts,
        bytes[] calldata sigs
    ) external payable returns (uint256 episodeId) {
        Covenant storage c = _covenant(covenantId);
        if (c.settled) revert WindowClosed();
        if (msg.value != proposerBond) revert BadBond(msg.value, proposerBond);
        if (evidenceSeen[evidenceHash]) revert DuplicateEvidence(evidenceHash);
        if (!SigLib.verifyProof(merkleProof, c.sourceSetRoot, SigLib.sourceLeaf(sourceId))) {
            revert NotCovered(sourceId);
        }

        uint256 orgs = _verifyQuorum(
            Expect({
                covenantId: covenantId,
                sourceId: sourceId,
                evidenceHash: evidenceHash,
                sellerOrg: c.sellerOrg,
                buyerOrg: c.buyerOrg,
                kind: KIND_DETECT,
                required: c.k
            }),
            atts,
            sigs
        );

        episodeId = _recordDetection(c, covenantId, sourceId, evidenceHash, atts[0], orgs);
    }

    /// @notice Anyone may challenge a PROPOSED episode within its window, on an objective reason, with a bond.
    function challenge(uint256 episodeId, uint8 reasonCode) external payable {
        Episode storage e = _openEpisode(episodeId);
        if (e.state != State.PROPOSED) revert InvalidState(episodeId);
        if (block.timestamp >= e.deadline) revert WindowClosed();
        if (reasonCode == 0 || reasonCode > REASON_STALE) revert InvalidReason(reasonCode);
        if (msg.value != challengerBond) revert BadBond(msg.value, challengerBond);

        e.state = State.CHALLENGED;
        e.challenger = msg.sender;
        e.challengerBond = uint96(msg.value);
        e.reasonCode = reasonCode;
        e.deadline = uint64(block.timestamp) + _covenants[e.covenantId].challengeWindow;
        emit EpisodeChallenged(episodeId, msg.sender, reasonCode);
    }

    /// @notice Seller disputes attribution (scientific, not objective): holds the rebate, never slashes.
    function contest(uint256 episodeId) external {
        Episode storage e = _openEpisode(episodeId);
        Covenant storage c = _covenants[e.covenantId];
        if (msg.sender != c.seller) revert NotAuthorized();
        if (e.state != State.PROPOSED) revert InvalidState(episodeId);
        if (block.timestamp >= e.deadline) revert WindowClosed();

        e.state = State.CONTESTED;
        e.deadline = uint64(block.timestamp) + c.contestWindow;
        emit EpisodeContested(episodeId);
    }

    /// @notice Observers resolve a CHALLENGED episode (k CONFIRM -> CONFIRMED, k WITHDRAW -> VOID) or a CONTESTED one
    ///         (k+1 CONFIRM -> CONFIRMED).
    function submitResolution(uint256 episodeId, Attestation[] calldata atts, bytes[] calldata sigs) external {
        Episode storage e = _openEpisode(episodeId);
        Covenant storage c = _covenants[e.covenantId];
        if (e.state != State.CHALLENGED && e.state != State.CONTESTED) revert InvalidState(episodeId);
        if (block.timestamp >= e.deadline) revert WindowClosed();
        if (atts.length == 0) revert QuorumNotMet(0, c.k);

        uint8 kind = atts[0].kind;
        uint256 required = c.k;
        if (e.state == State.CONTESTED) {
            if (kind != KIND_CONFIRM) revert InvalidParams();
            required += 1;
        } else if (kind != KIND_CONFIRM && kind != KIND_WITHDRAW) {
            revert InvalidParams();
        }

        _verifyQuorum(
            Expect({
                covenantId: e.covenantId,
                sourceId: e.sourceId,
                evidenceHash: e.evidenceHash,
                sellerOrg: c.sellerOrg,
                buyerOrg: c.buyerOrg,
                kind: kind,
                required: required
            }),
            atts,
            sigs
        );

        if (kind == KIND_CONFIRM) _confirm(c, e, episodeId);
        else _void(c, e, episodeId);
    }

    /// @notice Deadline transition, callable by anyone: PROPOSED -> CONFIRMED; CHALLENGED/CONTESTED -> EXPIRED.
    function finalize(uint256 episodeId) external {
        Episode storage e = _episodes[episodeId];
        State s = e.state;
        if (s != State.PROPOSED && s != State.CHALLENGED && s != State.CONTESTED) revert NothingToFinalize(episodeId);
        if (block.timestamp < e.deadline) revert WindowOpen();
        Covenant storage c = _covenants[e.covenantId];
        if (s == State.PROPOSED) _confirm(c, e, episodeId);
        else _expire(c, e, episodeId);
    }

    /// @notice The seller may record a mitigation claim. It is logged and has NO effect on any covenant or episode
    ///         state: non-detection is never evidence of repair.
    function recordMitigationClaim(bytes32 covenantId, bytes32 sourceId, bytes32 claimHash) external {
        Covenant storage c = _covenant(covenantId);
        if (msg.sender != c.seller) revert NotAuthorized();
        emit MitigationClaimRecorded(covenantId, sourceId, claimHash, msg.sender);
    }

    /// @notice Pull payment.
    function withdraw() external {
        uint256 amount = withdrawable[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        withdrawable[msg.sender] = 0;
        emit Withdrawn(msg.sender, amount);
        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();
    }

    // ------------------------------------------------------------------ views

    function getCovenant(bytes32 covenantId) external view returns (Covenant memory) {
        return _covenants[covenantId];
    }

    function getEpisode(uint256 episodeId) external view returns (Episode memory) {
        return _episodes[episodeId];
    }

    function isNonceUsed(address signer, uint64 nonce) external view returns (bool) {
        return nonceBitmap[signer][nonce >> 8] & (1 << (nonce & 0xff)) != 0;
    }

    // solhint-disable-next-line func-name-mixedcase
    function DOMAIN_SEPARATOR() public view returns (bytes32) {
        return block.chainid == _cachedChainId
            ? _cachedDomainSeparator
            : AttestationLib.domainSeparator(block.chainid, address(this));
    }

    /// @notice EIP-712 digest an observer signs for `a` on this chain and contract.
    function attestationDigest(Attestation calldata a) external view returns (bytes32) {
        return AttestationLib.toTypedDataHash(DOMAIN_SEPARATOR(), AttestationLib.hash(a));
    }

    /// @notice EIP-5267 domain introspection (for viem / ethers signTypedData).
    function eip712Domain()
        external
        view
        returns (
            bytes1 fields,
            string memory name,
            string memory version,
            uint256 chainId,
            address verifyingContract,
            bytes32 salt,
            uint256[] memory extensions
        )
    {
        return (
            hex"0f", AttestationLib.NAME, AttestationLib.VERSION, block.chainid, address(this), bytes32(0), extensions
        );
    }

    // ------------------------------------------------------------------ internal: verification

    /// @dev Fail-closed: any bad attestation reverts the whole call. Returns the number of distinct orgs.
    function _verifyQuorum(Expect memory ex, Attestation[] calldata atts, bytes[] calldata sigs)
        private
        returns (uint256 distinct)
    {
        uint256 n = atts.length;
        if (n != sigs.length || n > MAX_ATTESTATIONS) revert InvalidParams();
        if (n < ex.required) revert QuorumNotMet(0, ex.required);

        bytes32 ds = DOMAIN_SEPARATOR();
        bytes32[] memory seen = new bytes32[](n);
        for (uint256 i; i < n; ++i) {
            bytes32 org = _checkAttestation(ex, atts[i], atts[0], sigs[i], ds, i);
            bool dup;
            for (uint256 j; j < distinct; ++j) {
                if (seen[j] == org) {
                    dup = true;
                    break;
                }
            }
            if (!dup) seen[distinct++] = org;
        }
        if (distinct < ex.required) revert QuorumNotMet(distinct, ex.required);
    }

    function _checkAttestation(
        Expect memory ex,
        Attestation calldata a,
        Attestation calldata a0,
        bytes calldata sig,
        bytes32 ds,
        uint256 index
    ) private returns (bytes32 org) {
        // All attestations in a batch bind the same covenant, source, evidence, kind and measured values.
        if (
            a.covenantId != ex.covenantId || a.sourceId != ex.sourceId || a.evidenceHash != ex.evidenceHash
                || a.kind != ex.kind || a.emissionKgH != a0.emissionKgH || a.uncertaintyKgH != a0.uncertaintyKgH
                || a.detectedAt != a0.detectedAt || a.instrumentId != a0.instrumentId || a.providerId != a0.providerId
                || a.modelHash != a0.modelHash
        ) revert AttestationMismatch(index);
        if (a.expiry < block.timestamp || a.expiry > block.timestamp + MAX_EXPIRY_HORIZON) revert Expired(a.expiry);

        address signer = SigLib.recover(AttestationLib.toTypedDataHash(ds, AttestationLib.hash(a)), sig);
        if (signer == address(0)) revert InvalidSignature();

        KeyRegistry.Role role;
        bool valid;
        (org, role, valid) = registry.keyStatus(signer, block.timestamp);
        if (org == bytes32(0)) revert InvalidSignature(); // recovers to an unregistered address: data was edited
        if (org == ex.sellerOrg || org == ex.buyerOrg) revert SelfAttestation(signer);
        if (role != KeyRegistry.Role.OBSERVER) revert AttestorNotIndependent(signer);
        if (!valid) revert RevokedKey(signer);

        uint256 word = a.nonce >> 8;
        uint256 bit = 1 << (a.nonce & 0xff);
        uint256 bits = nonceBitmap[signer][word];
        if (bits & bit != 0) revert Replay(signer, a.nonce);
        nonceBitmap[signer][word] = bits | bit;
    }

    // ------------------------------------------------------------------ internal: episode recording

    function _recordDetection(
        Covenant storage c,
        bytes32 covenantId,
        bytes32 sourceId,
        bytes32 evidenceHash,
        Attestation calldata a0,
        uint256 orgs
    ) private returns (uint256 episodeId) {
        if (a0.detectedAt <= c.start || a0.detectedAt >= c.end || a0.detectedAt > block.timestamp) {
            revert DetectionOutOfTerm(a0.detectedAt);
        }
        if (a0.emissionKgH < c.minRateKgH) revert BelowMinRate(a0.emissionKgH);

        evidenceSeen[evidenceHash] = true;
        episodeId = ++episodeCount;

        Episode storage e = _episodes[episodeId];
        e.covenantId = covenantId;
        e.sourceId = sourceId;
        e.evidenceHash = evidenceHash;
        e.proposer = msg.sender;
        e.detectedAt = a0.detectedAt;
        e.proposedAt = uint64(block.timestamp);
        e.emissionKgH = a0.emissionKgH;
        e.quorumOrgs = uint8(orgs);

        (uint256 parent, bool linked) = _linkParent(c, covenantId, sourceId, a0.detectedAt);
        e.parentEpisode = uint64(parent);

        if (linked) {
            // Linked detection: recorded under the parent, never paid, bond returned at once.
            e.state = State.LINKED;
            _credit(msg.sender, msg.value);
            emit DetectionLinked(episodeId, parent, covenantId, sourceId, evidenceHash, a0.emissionKgH);
            return episodeId;
        }

        (uint8 tier, uint256 rebate) = _holdRebate(c, a0.emissionKgH);
        e.state = State.PROPOSED;
        e.tier = tier;
        e.rebate = uint128(rebate);
        e.proposerBond = uint96(msg.value);
        e.deadline = uint64(block.timestamp) + c.challengeWindow;
        latestEpisode[covenantId][sourceId] = episodeId;

        emit EpisodeProposed(
            episodeId, covenantId, sourceId, evidenceHash, parent, a0.emissionKgH, tier, rebate, e.quorumOrgs
        );
    }

    /// @dev Inside linkWindow of the latest live/confirmed episode -> (parent, linked=true). After the window ->
    ///      (parent, false): a new episode with `parentEpisode` set. VOID/EXPIRED episodes are never ancestors.
    function _linkParent(Covenant storage c, bytes32 covenantId, bytes32 sourceId, uint64 detectedAt)
        private
        view
        returns (uint256 parent, bool linked)
    {
        parent = latestEpisode[covenantId][sourceId];
        if (parent == 0) return (0, false);
        Episode storage pe = _episodes[parent];
        if (!_anchorsLinks(pe.state)) return (0, false);
        linked = detectedAt <= uint256(pe.detectedAt) + c.linkWindow;
    }

    function _holdRebate(Covenant storage c, uint32 emissionKgH) private returns (uint8 tier, uint256 rebate) {
        tier = _tierOf(emissionKgH);
        rebate = c.tierAmounts[tier - 1];
        if (rebate > c.remaining) rebate = c.remaining;
        c.remaining -= rebate;
        c.held += rebate;
        c.openEpisodes += 1;
    }

    // ------------------------------------------------------------------ internal: transitions

    function _confirm(Covenant storage c, Episode storage e, uint256 episodeId) private {
        uint256 rebate = e.rebate;
        e.state = State.CONFIRMED;
        c.held -= rebate;
        c.paid += rebate;
        c.openEpisodes -= 1;
        _credit(c.buyer, rebate);
        _releaseBond(e.proposer, e, true);
        // A challenge that failed: challenger bond goes to the buyer.
        if (e.challengerBond != 0) _releaseBond(c.buyer, e, false);
        emit EpisodeConfirmed(episodeId, rebate);
    }

    function _void(Covenant storage c, Episode storage e, uint256 episodeId) private {
        uint256 rebate = e.rebate;
        e.state = State.VOID;
        c.held -= rebate;
        c.remaining += rebate;
        c.openEpisodes -= 1;
        uint256 award = e.proposerBond;
        address challenger = e.challenger;
        _releaseBond(challenger, e, true); // proposer bond -> challenger
        _releaseBond(challenger, e, false); // challenger bond back
        emit EpisodeVoided(episodeId, challenger, award);
    }

    function _expire(Covenant storage c, Episode storage e, uint256 episodeId) private {
        uint256 rebate = e.rebate;
        e.state = State.EXPIRED;
        c.held -= rebate;
        c.remaining += rebate;
        c.openEpisodes -= 1;
        _releaseBond(e.proposer, e, true);
        if (e.challengerBond != 0) _releaseBond(e.challenger, e, false);
        emit EpisodeExpired(episodeId);
    }

    function _releaseBond(address to, Episode storage e, bool proposerSide) private {
        uint256 amount;
        if (proposerSide) {
            amount = e.proposerBond;
            e.proposerBond = 0;
        } else {
            amount = e.challengerBond;
            e.challengerBond = 0;
        }
        _credit(to, amount);
    }

    function _fund(Covenant storage c, bytes32 covenantId) private {
        if (c.settled || block.timestamp >= c.end) revert WindowClosed();
        c.funded += msg.value;
        c.remaining += msg.value;
        emit CovenantFunded(covenantId, msg.sender, msg.value);
    }

    function _credit(address to, uint256 amount) private {
        if (amount == 0) return;
        withdrawable[to] += amount;
        emit Credited(to, amount);
    }

    // ------------------------------------------------------------------ internal: lookups

    function _covenant(bytes32 covenantId) private view returns (Covenant storage c) {
        c = _covenants[covenantId];
        if (c.seller == address(0)) revert UnknownCovenant(covenantId);
    }

    function _openEpisode(uint256 episodeId) private view returns (Episode storage e) {
        e = _episodes[episodeId];
        if (e.state == State.LINKED) revert EpisodeLinked(episodeId);
        if (e.state == State.NONE) revert InvalidState(episodeId);
    }

    /// @dev Seller must be a valid SELLER key, buyer a valid BUYER key, in different orgs.
    function _partyOrgs(address seller, address buyer) private view returns (bytes32 sellerOrg, bytes32 buyerOrg) {
        KeyRegistry.Role role;
        bool valid;
        (sellerOrg, role, valid) = registry.keyStatus(seller, block.timestamp);
        if (role != KeyRegistry.Role.SELLER || !valid) revert NotAuthorized();
        (buyerOrg, role, valid) = registry.keyStatus(buyer, block.timestamp);
        if (role != KeyRegistry.Role.BUYER || !valid || sellerOrg == buyerOrg) revert InvalidParams();
    }

    function _anchorsLinks(State s) private pure returns (bool) {
        return s == State.PROPOSED || s == State.CHALLENGED || s == State.CONTESTED || s == State.CONFIRMED;
    }

    function _tierOf(uint32 kgh) private pure returns (uint8) {
        if (kgh >= TIER1_MIN_KGH) return 1;
        if (kgh >= TIER2_MIN_KGH) return 2;
        return 3;
    }
}

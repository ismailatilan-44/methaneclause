// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {KeyRegistry} from "./KeyRegistry.sol";
import {MethaneCovenant} from "./MethaneCovenant.sol";
import {AttestationLib} from "./lib/Attestation.sol";
import {PoolVerify} from "./lib/PoolVerify.sol";
import {PoolSetup} from "./lib/PoolSetup.sol";
import {PoolEpisodes} from "./lib/PoolEpisodes.sol";
import {PoolCorroboration} from "./lib/PoolCorroboration.sol";
import {PoolInstallments} from "./lib/PoolInstallments.sol";
import {
    CleanPass,
    Corroboration,
    KIND_CLEAN,
    KIND_CONFIRM_CLEAN,
    KIND_WITHDRAW_CLEAN,
    KIND_CORROBORATE
} from "./lib/CleanPass.sol";

/// @title IncentivePool — the two-sided clause's cure refund (spec-v2 §3)
/// @notice Reads the unchanged core's CONFIRMED episodes and, when k independent observer orgs attest a run of qualified
///         clean passes at that source, releases a capped, cancellable cure refund in installments: never more than the
///         rebate the episode cost, frozen while a new detection is under review, cancelled by a later one. The frozen
///         surface of spec-v2 §3.1 (+ §15 additions) is declared here and pinned by IncentivePoolSurfaceTest; changing a
///         signature, event, error or struct needs a spec-v2 §15 amendment first. To stay under EIP-170, five linked
///         libraries hold part of the logic and run by DELEGATECALL on this contract's storage, with this contract's
///         error selectors and event topics: PoolVerify (attestation and scene checks, observer fee), PoolSetup
///         (opening checks), PoolEpisodes (reads of core episodes), PoolCorroboration (pending / challenge cycle of a
///         MARS corroboration), PoolInstallments (installment scheduling and cancellation).
/// @dev Reads the unchanged core (`MethaneCovenant`) and trusts exactly `covenant.registry()`. No admin, no owner.
contract IncentivePool {
    // ------------------------------------------------------------------ types (frozen, spec-v2 §3.1)
    enum Mode {
        ONCHAIN,
        VERDICT_ONLY
    }
    enum PoolState {
        NONE,
        OPEN,
        CLOSING,
        CLOSED
    }
    enum ClaimState {
        NONE,
        OPEN,
        CANCELLED,
        CLOSED
    }
    enum PassState {
        NONE,
        PENDING,
        CHALLENGED,
        COUNTED,
        VOID,
        EXPIRED
    }
    enum InstState {
        NONE,
        UNFUNDED,
        PENDING,
        RELEASED,
        VOID
    }

    struct InstallmentRule {
        uint8 afterPasses;
        uint16 shareBp;
        bool twoInstruments;
        bool isFinal;
    }

    struct PoolParams {
        uint32 publicationLag;
        uint32 cleanWindow;
        uint32 resolveWindow;
        uint32 holdWindow;
        uint32 cureTerm;
        uint96 passBond;
        uint96 challengerBond;
        uint96 observerFeeWei;
        uint128[3] unitTierAmounts; // VERDICT_ONLY cap per core tier, policy units
        InstallmentRule[3] installments;
        uint16[32] upperBoundBp; // u(N) = 1 - 0.025^(1/N), basis points, N = 1..32
    }

    struct Pool {
        PoolState state;
        Mode mode;
        bytes32 policyHash;
        bytes32 paramsHash;
        uint256 balance;
        uint256 reserved;
        address opener;
    }

    struct Claim {
        uint256 episodeId;
        bytes32 covenantId;
        bytes32 sourceId;
        uint64 since; // episode.detectedAt + covenant.linkWindow
        uint32 rateKgH;
        uint8 tier;
        uint256 cap;
        uint256 paid;
        uint256 reserved;
        uint32 counted;
        uint8 instrumentsSeen;
        uint8 pending;
        uint64 corroboratedTo;
        ClaimState state;
    }

    struct Pass {
        bytes32 claimId;
        bytes32 sceneId;
        bytes32 sceneHash;
        uint64 sceneAt;
        uint64 publishedAt;
        uint8 instrumentId;
        uint16 cloudBp;
        address submitter;
        uint96 bond;
        bytes32[] orgs; // distinct signing observer orgs (observer-fee recipients via their controllers)
        address challenger;
        uint96 challengerBond;
        uint8 reason;
        uint64 window;
        uint64 resolveBy;
        PassState state;
    }

    struct Installment {
        bytes32 claimId;
        uint8 index;
        uint256 amount;
        uint64 claimableAt;
        InstState state;
        bytes32 ref;
    }

    // ------------------------------------------------------------------ constants
    uint256 public constant MAX_PENDING = 32;
    uint256 public constant MAX_ATTESTATIONS = 16;
    uint256 public constant MAX_EXPIRY_HORIZON = 1 days;
    uint8 public constant REASON_CLOUD_FLAG = 1;
    uint8 public constant REASON_NO_ORIGIN_INTERSECT = 2;
    uint8 public constant REASON_PLUME_ON_SCENE = 3;
    uint8 public constant REASON_SCENE_NOT_FOUND = 4;
    uint8 public constant REASON_MARS_PLUME_NEARBY = 5;
    uint8 public constant REASON_FLOOR_MISMATCH = 6;

    // ------------------------------------------------------------------ immutable wiring
    MethaneCovenant public immutable covenant;
    KeyRegistry public immutable registry;
    uint256 private immutable _cachedChainId;
    bytes32 private immutable _cachedDomainSeparator;

    /// @dev Lane-private facts cached when a pool opens. Every field read from the core is immutable there once the
    ///      covenant exists (`end`, `linkWindow`), so caching cannot drift from the core.
    struct PoolAux {
        uint64 end; // covenant.end
        uint64 linkWindow; // covenant.linkWindow
        uint64 closingAt; // end + publicationLag + cleanWindow: no new passes from here on (CLOSING)
        uint32 openClaims; // claims in state OPEN; the pool is CLOSED once closingAt is reached and this is 0
        uint256 contributionsOutstanding; // sum of `contributions` not yet taken back by sponsorWithdraw
        uint8 k; // covenant.k: distinct observer orgs required
        address seller; // covenant.seller: receives a failed challenger's bond and (A3) the refunds
        bytes32 sellerOrg; // covenant.sellerOrg: may not sign
        bytes32 buyerOrg; // covenant.buyerOrg: may not sign
        uint64 relapseGrace; // publicationLag + challengeWindow + contestWindow: when a relapse is publishable and confirmable
    }

    // ------------------------------------------------------------------ storage (may be extended, never reorder views)
    mapping(bytes32 covenantId => PoolAux) internal _aux;
    mapping(bytes32 covenantId => Pool) internal _pools;
    mapping(bytes32 covenantId => PoolParams) internal _params;
    mapping(bytes32 claimId => Claim) internal _claims;
    mapping(uint256 passId => Pass) internal _passes;
    mapping(address account => uint256) public withdrawable;
    /// @notice ETH each sponsor put into a covenant's pool and has not yet taken back through `sponsorWithdraw`.
    mapping(bytes32 covenantId => mapping(address sponsor => uint256)) public contributions;
    uint256 internal _passCount;
    /// @dev Pool-local nonce bitmap per observer key (the core keeps its own; a nonce used there is free here).
    mapping(address signer => mapping(uint256 word => uint256)) internal _nonceBitmap;
    /// @dev A scene counts at most once per claim; cleared only when its pass ends VOID or EXPIRED.
    mapping(bytes32 claimId => mapping(bytes32 sceneId => bool)) internal _seenScene;
    /// @dev At most one corroboration per claim between attestation and effect (spec-v2 §15 A3 audit (3)).
    mapping(bytes32 claimId => PoolCorroboration.Pending) internal _corroborations;
    /// @dev Installments, per-claim bookkeeping and the per-pool FIFO of UNFUNDED installments (`PoolInstallments`).
    PoolInstallments.Book internal _book;

    // ------------------------------------------------------------------ events (frozen)
    event PoolFunded(
        bytes32 indexed covenantId,
        address indexed sponsor,
        uint256 amount,
        bytes32 policyHash,
        Mode mode,
        bytes32 paramsHash
    );
    event ClaimOpened(
        bytes32 indexed claimId, uint256 indexed episodeId, bytes32 covenantId, bytes32 sourceId, uint256 cap
    );
    event CleanPassAttested(
        bytes32 indexed claimId,
        uint256 indexed passId,
        bytes32 sceneId,
        bytes32 sceneHash,
        uint64 sceneAt,
        uint8 instrumentId
    );
    event CleanPassChallenged(uint256 indexed passId, address indexed challenger, uint8 reason);
    event CleanPassCounted(bytes32 indexed claimId, uint256 indexed passId, uint32 counted, uint16 upperBoundBp);
    event CleanPassVoided(uint256 indexed passId, address indexed beneficiary, uint256 award);
    event CleanPassExpired(uint256 indexed passId);
    event ObserverFeePaid(uint256 indexed passId, address indexed observer, uint256 amount);
    event InstallmentScheduled(
        bytes32 indexed claimId, uint256 indexed installmentId, uint8 index, uint256 amount, uint64 claimableAt
    );
    event InstallmentUnfunded(bytes32 indexed claimId, uint256 indexed installmentId, uint256 amount);
    event InstallmentReleased(uint256 indexed installmentId, uint256 amount);
    event CreditNoteDue(bytes32 indexed claimId, uint256 indexed installmentId, uint256 amount, bytes32 ref);
    /// @notice Reserved, not emitted in v2 (spec-v2 §15 A3 audit (4)): supersession of a credit note is an off-chain
    ///         export step; under D1 no on-chain party may supersede a released verdict.
    event CreditNoteSuperseded(bytes32 indexed ref, bytes32 newRef);
    event ClaimCancelled(bytes32 indexed claimId, uint256 indexed relapseEpisodeId, uint256 releasedReserve);
    event ClaimClosed(bytes32 indexed claimId);
    event ClaimCorroborated(bytes32 indexed claimId, bytes32 marsSnapshotHash, uint64 windowFrom, uint64 windowTo);
    event FinalOutOfTerm(bytes32 indexed claimId);
    /// @notice spec-v2 §15 A3b: a corroboration was attested and takes effect at `effectiveAt` unless challenged.
    event CorroborationPending(bytes32 indexed claimId, bytes32 marsSnapshotHash, uint64 effectiveAt);
    /// @notice spec-v2 §15 A3b: a pending corroboration was challenged; observers answer before `resolveBy`.
    event CorroborationChallenged(bytes32 indexed claimId, address indexed challenger, uint64 resolveBy);
    event Credited(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);

    // ------------------------------------------------------------------ errors (frozen)
    error PoolNotOpenedByParty();
    error PolicyMismatch();
    error BadParams();
    error PoolClosed();
    error PoolNotClosed();
    error NotConfirmed(uint256 episodeId);
    error WrongCovenant();
    error ZeroCap();
    error ClaimExists();
    error ClaimNotOpen();
    error QuorumNotMet(uint256 have, uint256 need);
    error AttestorNotIndependent(address signer);
    error SelfAttestation(address signer);
    error RevokedKey(address signer);
    error InvalidSignature();
    error AttestationMismatch(uint256 index);
    error Replay(address signer, uint64 nonce);
    error Expired(uint64 expiry);
    error BadKind();
    error Cloudy();
    error FloorAboveRate();
    error BeforePublicationLag();
    error SceneOutOfRange();
    error DuplicateScene();
    error TooManyPending();
    error SourceUnderReview(uint256 episodeId);
    error RelapsePending(uint256 episodeId);
    /// @notice Reserved, never raised (spec-v2 §15 A3 audit (4)): an out-of-term final tranche is not an error; the
    ///         event `FinalOutOfTerm(claimId)` is emitted instead and the claim stays capped at the non-final shares.
    error FinalOutOfTermError();
    error NotCorroborated();
    error MarsPlumeInWindow();
    error WindowOpen();
    error WindowClosed();
    error InvalidState();
    error BadBond();
    error InvalidReason();
    error NotARelapse(uint256 episodeId);
    error NothingToWithdraw();
    error TransferFailed();

    constructor(MethaneCovenant covenant_) {
        covenant = covenant_;
        registry = covenant_.registry(); // the pool trusts exactly the covenant's registry (R20)
        _cachedChainId = block.chainid;
        _cachedDomainSeparator = AttestationLib.domainSeparator(block.chainid, address(this));
    }

    /// @notice Plain ETH transfers are refused: funds enter only through `fund` (spec-v2 §3).
    receive() external payable {
        revert InvalidState();
    }

    // ------------------------------------------------------------------ pool: fund, lifecycle, sponsor exit

    /// @notice Opens the covenant's pool (first call, by the covenant's buyer or seller only) or tops it up (anyone,
    ///         same policyHash / mode / params). The first call fixes `policyHash`, `mode` and `p`; the event carries
    ///         `paramsHash = keccak256(abi.encode(p))`; `test/unit/PolicyParity.t.sol` (in `make check`) rebuilds the pipeline's
    ///         POLICY_V2 vector into this layout and asserts that hash equals POLICY_V2.onchainParamsHash.
    ///         ONCHAIN needs `msg.value > 0`. VERDICT_ONLY holds no ETH (cap, reserved and paid are policy units), so
    ///         it is opened with `msg.value == 0` and accepts no top-up.
    function fund(bytes32 covenantId, bytes32 policyHash, Mode mode, PoolParams calldata p) external payable {
        Pool storage pl = _pools[covenantId];
        if (pl.state == PoolState.NONE) {
            _openPool(pl, covenantId, policyHash, mode, p);
        } else {
            if (_poolState(covenantId) == PoolState.CLOSED) revert PoolClosed();
            if (policyHash != pl.policyHash || mode != pl.mode || keccak256(abi.encode(p)) != pl.paramsHash) {
                revert PolicyMismatch();
            }
            if (mode == Mode.VERDICT_ONLY || msg.value == 0) revert BadParams();
        }
        if (msg.value != 0) {
            pl.balance += msg.value;
            contributions[covenantId][msg.sender] += msg.value;
            _aux[covenantId].contributionsOutstanding += msg.value;
        }
        emit PoolFunded(covenantId, msg.sender, msg.value, policyHash, mode, pl.paramsHash);
        if (msg.value != 0) {
            PoolInstallments.scheduleUnfunded(_book, _claims, pl, _params[covenantId], _aux[covenantId], covenantId);
        }
    }

    /// @notice After the pool is CLOSED, a sponsor takes back its pro-rata share of the unreserved balance (credited;
    ///         pull with `withdraw`).
    function sponsorWithdraw(bytes32 covenantId) external {
        if (_poolState(covenantId) != PoolState.CLOSED) revert PoolNotClosed();
        uint256 contributed = contributions[covenantId][msg.sender];
        if (contributed == 0) revert NothingToWithdraw();
        Pool storage pl = _pools[covenantId];
        PoolAux storage a = _aux[covenantId];
        uint256 unreserved = pl.balance - pl.reserved;
        uint256 outstanding = a.contributionsOutstanding;
        // Shares of what is left, by what is left: the last sponsor out (contributed == outstanding) receives exactly
        // the remaining unreserved balance, so no dust stays behind.
        uint256 share = unreserved * contributed / outstanding;

        contributions[covenantId][msg.sender] = 0;
        a.contributionsOutstanding = outstanding - contributed;
        pl.balance -= share;
        _credit(msg.sender, share);
    }

    /// @notice Permissionless once the pool is past `closingAt`: moves an OPEN claim to CLOSED. When the last OPEN
    ///         claim closes, the pool is CLOSED and sponsors may withdraw.
    function closeClaim(bytes32 claimId) external {
        Claim storage c = _claims[claimId];
        if (c.state == ClaimState.NONE) revert ClaimNotOpen();
        if (c.state != ClaimState.OPEN) revert InvalidState();
        PoolAux storage a = _aux[c.covenantId];
        if (block.timestamp < a.closingAt) revert WindowOpen();
        // Open passes (PENDING or CHALLENGED) must settle first; their bonds are not sponsor money (§15 A1 audit).
        // So must installments (PENDING or UNFUNDED): sponsors cannot walk away from an earned installment.
        if (c.pending != 0 || _book.claims[claimId].open != 0) revert InvalidState();
        c.state = ClaimState.CLOSED;
        a.openClaims -= 1;
        emit ClaimClosed(claimId);
    }

    // ------------------------------------------------------------------ claims

    /// @notice Anyone opens the refund claim of a CONFIRMED core episode whose covenant has an OPEN pool here. The
    ///         clean run starts when the core's link window closes (`since = detectedAt + linkWindow`). The cap is the
    ///         episode's actual rebate (ONCHAIN; already clipped to the bond by the core) or the policy-unit tier
    ///         amount (VERDICT_ONLY). The core never leaves CONFIRMED, so the claim's basis cannot be voided later.
    function openClaim(uint256 episodeId) external returns (bytes32 claimId) {
        PoolEpisodes.ClaimBasis memory e = PoolEpisodes.claimBasis(covenant, episodeId); // NotConfirmed
        bytes32 covenantId = e.covenantId;
        PoolState s = _poolState(covenantId);
        if (s == PoolState.NONE) revert WrongCovenant();
        if (s != PoolState.OPEN) revert PoolClosed();

        claimId = keccak256(abi.encode(address(this), episodeId));
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.NONE) revert ClaimExists();
        uint256 cap =
            _pools[covenantId].mode == Mode.ONCHAIN ? e.rebate : _params[covenantId].unitTierAmounts[e.tier - 1];
        if (cap == 0) revert ZeroCap();

        PoolAux storage a = _aux[covenantId];
        c.episodeId = episodeId;
        c.covenantId = covenantId;
        c.sourceId = e.sourceId;
        c.since = e.detectedAt + a.linkWindow;
        c.rateKgH = e.emissionKgH;
        c.tier = e.tier;
        c.cap = cap;
        c.state = ClaimState.OPEN;
        a.openClaims += 1;
        emit ClaimOpened(claimId, episodeId, covenantId, e.sourceId, cap);
    }

    // ------------------------------------------------------------------ clean passes

    /// @notice k distinct observer orgs (outside the seller's and buyer's orgs) attest one qualified clean pass for an
    ///         OPEN claim, with exactly `passBond`. The scene must be after `since`, published, older than
    ///         `publicationLag`, before the covenant's end, not cloudy by the provider's own flag, seen by an
    ///         instrument whose floor is at most the episode's rate, and new for this claim. No later detection at
    ///         the source may be under review or confirmed (P9). The pass then waits `cleanWindow` for a challenge.
    function attestCleanPass(bytes32 claimId, CleanPass[] calldata atts, bytes[] calldata sigs)
        external
        payable
        returns (uint256 passId)
    {
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        bytes32 covenantId = c.covenantId;
        if (_poolState(covenantId) != PoolState.OPEN) revert PoolClosed();
        PoolParams storage p = _params[covenantId];
        if (msg.value != p.passBond) revert BadBond();
        if (c.pending >= MAX_PENDING) revert TooManyPending();
        PoolAux storage a = _aux[covenantId];
        _checkBatchShape(atts.length, sigs.length, a.k);

        CleanPass calldata a0 = atts[0];
        PoolVerify.checkScene(a0, c.since, c.rateKgH, a.end, p.publicationLag); // spec-v2 §2 rules 5-6
        if (_seenScene[claimId][a0.sceneId]) revert DuplicateScene();
        _requireNoLaterDetection(c);
        bytes32[] memory orgs = PoolVerify.cleanQuorum(
            PoolVerify.Expect({
                claimId: claimId,
                sourceId: c.sourceId,
                sceneId: a0.sceneId,
                sceneHash: a0.sceneHash,
                kind: KIND_CLEAN,
                required: a.k,
                sellerOrg: a.sellerOrg,
                buyerOrg: a.buyerOrg
            }),
            atts,
            sigs,
            DOMAIN_SEPARATOR(),
            registry,
            _nonceBitmap
        );

        passId = ++_passCount;
        Pass storage ps = _passes[passId];
        ps.claimId = claimId;
        ps.sceneId = a0.sceneId;
        ps.sceneHash = a0.sceneHash;
        ps.sceneAt = a0.sceneAt;
        ps.publishedAt = a0.publishedAt;
        ps.instrumentId = a0.instrumentId;
        ps.cloudBp = a0.cloudBp;
        ps.submitter = msg.sender;
        ps.bond = p.passBond; // == msg.value (checked above)
        ps.orgs = orgs;
        ps.window = uint64(block.timestamp) + p.cleanWindow;
        ps.state = PassState.PENDING;
        _seenScene[claimId][a0.sceneId] = true;
        c.pending += 1;
        emit CleanPassAttested(claimId, passId, a0.sceneId, a0.sceneHash, a0.sceneAt, a0.instrumentId);
    }

    // ------------------------------------------------------------------ MARS corroboration (D5; §15 A3 audit (3))

    /// @notice k distinct observer orgs attest that the pinned UNEP MARS snapshot shows no MARS plume at the facility in
    ///         [windowFrom <= since, windowTo >= the latest counted scene], with no later detection at the source live or
    ///         confirmed (P9). With nothing pending this starts a PENDING corroboration that takes effect after
    ///         `cleanWindow` unless challenged (`finalizeCorroboration`). With a CHALLENGED one it is the answer: the
    ///         same snapshot and window before `resolveBy` make it effective at once and the challenger's bond goes to
    ///         the seller. On effect `corroboratedTo` moves forward and, if the final installment's count and
    ///         instrument rules are met and this covers its triggering pass, the final share is scheduled (R28).
    function attestCorroboration(bytes32 claimId, Corroboration[] calldata atts, bytes[] calldata sigs) external {
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        _requireNoLaterDetection(c);
        PoolAux storage a = _aux[c.covenantId];
        _checkBatchShape(atts.length, sigs.length, a.k);
        Corroboration calldata a0 = atts[0];
        if (a0.marsPlumesInWindow != 0) revert MarsPlumeInWindow();
        if (a0.windowFrom > c.since || a0.windowTo > block.timestamp) revert SceneOutOfRange();
        PoolCorroboration.Pending storage pc = _corroborations[claimId];
        if (pc.state == PoolCorroboration.PENDING) revert InvalidState(); // one at a time
        if (pc.state == PoolCorroboration.NONE) {
            // an answer to a challenge restores the window already checked when it was first attested
            if (a0.windowTo < _book.claims[claimId].latestCountedSceneAt) revert NotCorroborated();
            if (a0.windowTo <= c.corroboratedTo) revert InvalidState(); // nothing new: a repeat
        }
        PoolVerify.corroborationQuorum(
            PoolVerify.Expect({
                claimId: claimId,
                sourceId: c.sourceId,
                sceneId: bytes32(0),
                sceneHash: bytes32(0),
                kind: KIND_CORROBORATE,
                required: a.k,
                sellerOrg: a.sellerOrg,
                buyerOrg: a.buyerOrg
            }),
            atts,
            sigs,
            DOMAIN_SEPARATOR(),
            registry,
            _nonceBitmap
        );
        uint64 effectiveTo =
            PoolCorroboration.record(pc, claimId, a0, _params[c.covenantId].cleanWindow, a.seller, withdrawable);
        if (effectiveTo != 0) _corroborated(claimId, effectiveTo);
    }

    /// @notice Anyone challenges a PENDING corroboration inside its clean window on reason 5 (MARS_PLUME_NEARBY,
    ///         re-checkable against the pinned snapshot) with exactly `challengerBond`; observers answer within
    ///         `resolveWindow` with a fresh attestation of the same snapshot and window.
    function challengeCorroboration(bytes32 claimId, uint8 reason) external payable {
        PoolCorroboration.challenge(
            _corroborations[claimId],
            claimId,
            reason,
            msg.value,
            _params[_claims[claimId].covenantId].challengerBond,
            _params[_claims[claimId].covenantId].resolveWindow
        );
    }

    /// @notice Permissionless, O(1) (ABI addition, spec-v2 §15 A3 audit (3)): an unchallenged corroboration after
    ///         `cleanWindow` takes effect (and schedules the final share if due); a challenge unanswered by
    ///         `resolveWindow` drops it and refunds the challenger. Nobody is slashed (D11).
    function finalizeCorroboration(bytes32 claimId) external {
        uint64 effectiveTo = PoolCorroboration.finalize(_corroborations[claimId], claimId, withdrawable);
        if (effectiveTo != 0) _corroborated(claimId, effectiveTo);
    }

    /// @notice Anyone challenges a PENDING pass inside its window on an objective, re-checkable reason (1 CLOUD_FLAG,
    ///         2 NO_ORIGIN_INTERSECT, 3 PLUME_ON_SCENE, 4 SCENE_NOT_FOUND, 5 MARS_PLUME_NEARBY, 6 FLOOR_MISMATCH), with
    ///         exactly `challengerBond`. Observers answer within `resolveWindow`.
    function challengePass(uint256 passId, uint8 reason) external payable {
        Pass storage ps = _passes[passId];
        if (ps.state != PassState.PENDING) revert InvalidState();
        if (block.timestamp >= ps.window) revert WindowClosed();
        if (reason == 0 || reason > REASON_FLOOR_MISMATCH) revert InvalidReason();
        PoolParams storage p = _params[_claims[ps.claimId].covenantId];
        if (msg.value != p.challengerBond) revert BadBond();

        ps.state = PassState.CHALLENGED;
        ps.challenger = msg.sender;
        ps.challengerBond = p.challengerBond; // == msg.value (checked above)
        ps.reason = reason;
        ps.resolveBy = uint64(block.timestamp) + p.resolveWindow;
        emit CleanPassChallenged(passId, msg.sender, reason);
    }

    /// @notice k observer orgs answer a challenge on the same claim and scene: k CONFIRM_CLEAN counts the pass and
    ///         sends the challenger's bond to the covenant seller; k WITHDRAW_CLEAN voids it and sends the pass bond
    ///         (and the challenger's own bond) to the challenger. Nobody else is slashed (D11).
    function resolvePass(uint256 passId, CleanPass[] calldata atts, bytes[] calldata sigs) external {
        Pass storage ps = _passes[passId];
        if (ps.state != PassState.CHALLENGED) revert InvalidState();
        if (block.timestamp >= ps.resolveBy) revert WindowClosed();
        Claim storage c = _claims[ps.claimId];
        PoolAux storage a = _aux[c.covenantId];
        _checkBatchShape(atts.length, sigs.length, a.k);
        uint8 kind = atts[0].kind;
        if (kind != KIND_CONFIRM_CLEAN && kind != KIND_WITHDRAW_CLEAN) revert BadKind();
        PoolVerify.cleanQuorum(
            PoolVerify.Expect({
                claimId: ps.claimId,
                sourceId: c.sourceId,
                sceneId: ps.sceneId,
                sceneHash: ps.sceneHash, // §15 A2 audit (2): a resolution answers for the very bundle that was attested
                kind: kind,
                required: a.k,
                sellerOrg: a.sellerOrg,
                buyerOrg: a.buyerOrg
            }),
            atts,
            sigs,
            DOMAIN_SEPARATOR(),
            registry,
            _nonceBitmap
        );

        uint256 challengerBond = ps.challengerBond;
        ps.challengerBond = 0;
        if (kind == KIND_CONFIRM_CLEAN) {
            _credit(a.seller, challengerBond); // a failed challenge pays the seller (spec-v2 §15, 2026-10-16)
            _count(passId, ps, c);
        } else {
            uint256 award = ps.bond;
            ps.bond = 0;
            _closePass(ps, c, PassState.VOID);
            _credit(ps.challenger, award + challengerBond);
            emit CleanPassVoided(passId, ps.challenger, award);
        }
    }

    /// @notice After `cleanWindow`, anyone counts an unchallenged pass: the submitter's bond comes back, the signing
    ///         orgs' controllers share the observer fee (ONCHAIN, if the unreserved balance covers it), and the
    ///         claim's count and instrument set grow. Blocked while a later detection at the source is live (P9).
    function countPass(uint256 passId) external {
        Pass storage ps = _passes[passId];
        if (ps.state != PassState.PENDING) revert InvalidState();
        if (block.timestamp < ps.window) revert WindowOpen();
        _count(passId, ps, _claims[ps.claimId]);
    }

    /// @notice After `resolveBy`, anyone ends an unresolved challenge: EXPIRED, not counted, both bonds returned,
    ///         nobody slashed (v1's contest rule, D11); the scene may be attested again.
    function finalizePass(uint256 passId) external {
        Pass storage ps = _passes[passId];
        if (ps.state != PassState.CHALLENGED) revert InvalidState();
        if (block.timestamp < ps.resolveBy) revert WindowOpen();
        uint256 bond = ps.bond;
        uint256 challengerBond = ps.challengerBond;
        ps.bond = 0;
        ps.challengerBond = 0;
        _closePass(ps, _claims[ps.claimId], PassState.EXPIRED);
        _credit(ps.submitter, bond);
        _credit(ps.challenger, challengerBond);
        emit CleanPassExpired(passId);
    }

    /// @notice Lazy, permissionless, O(1): a PENDING or CHALLENGED pass of a CANCELLED claim becomes VOID and its bonds
    ///         go back (submitter; challenger if any). Nobody is slashed.
    function voidPass(uint256 passId) external {
        Pass storage ps = _passes[passId];
        PassState s = ps.state;
        if (s != PassState.PENDING && s != PassState.CHALLENGED) revert InvalidState();
        Claim storage c = _claims[ps.claimId];
        if (c.state != ClaimState.CANCELLED) revert InvalidState();
        uint256 bond = ps.bond;
        uint256 challengerBond = ps.challengerBond;
        ps.bond = 0;
        ps.challengerBond = 0;
        _closePass(ps, c, PassState.VOID);
        _credit(ps.submitter, bond);
        _credit(ps.challenger, challengerBond);
        emit CleanPassVoided(passId, ps.submitter, bond);
    }

    // ------------------------------------------------------------------ installments: release, relapse

    /// @notice After `claimableAt`, anyone releases a PENDING installment if no later detection at the source is live
    ///         or confirmed (P9): ONCHAIN credits the covenant seller (pull); VERDICT_ONLY emits `CreditNoteDue` in
    ///         policy units and moves no funds.
    function release(uint256 installmentId) external {
        Installment storage it = _book.installments[installmentId];
        if (it.state != InstState.PENDING) revert InvalidState();
        Claim storage c = _claims[it.claimId];
        // spec-v2 §8.9 g / §15 C3 audit: state -> later detection (live or confirmed) -> claimableAt
        _requireNoLaterDetection(c);
        if (block.timestamp < it.claimableAt) revert WindowOpen();
        uint256 amount = it.amount;
        it.state = InstState.RELEASED;
        c.reserved -= amount;
        c.paid += amount;
        _book.claims[it.claimId].open -= 1;
        bytes32 covenantId = c.covenantId;
        Pool storage pl = _pools[covenantId];
        if (pl.mode == Mode.ONCHAIN) {
            pl.reserved -= amount;
            pl.balance -= amount;
            _credit(_aux[covenantId].seller, amount);
        } else {
            bytes32 ref = keccak256(abi.encode(block.chainid, address(this), installmentId));
            it.ref = ref;
            emit CreditNoteDue(it.claimId, installmentId, amount, ref);
        }
        emit InstallmentReleased(installmentId, amount);
    }

    /// @notice Anyone cancels an OPEN claim with a later core episode at the same source that is CONFIRMED or EXPIRED
    ///         and detected after `since` (a seller who contests a relapse to EXPIRED gains nothing; R19). O(1): the
    ///         claim's at most three installments are voided and their reserve freed; PENDING passes are voided
    ///         lazily by `voidPass` (R17). Released installments stay released (D1). Accepting EXPIRED here also
    ///         closes R23's second form: the caller names the episode, so a displaced `latestEpisode` slot hides
    ///         nothing from `relapse`.
    function relapse(bytes32 claimId, uint256 episodeId2) external {
        Claim storage c = _claims[claimId];
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen();
        // NotARelapse / SourceUnderReview (a live episode may still end VOID: wait)
        PoolEpisodes.requireRelapse(covenant, episodeId2, c.covenantId, c.sourceId, c.since);

        c.state = ClaimState.CANCELLED;
        bytes32 covenantId = c.covenantId;
        _aux[covenantId].openClaims -= 1;
        uint256 releasedReserve = PoolInstallments.cancel(_book, c, _pools[covenantId], claimId);
        emit ClaimCancelled(claimId, episodeId2, releasedReserve);
    }

    /// @notice Pull payment (checks-effects-interactions; a failed transfer reverts and leaves the credit intact).
    function withdraw() external {
        uint256 amount = withdrawable[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        withdrawable[msg.sender] = 0;
        emit Withdrawn(msg.sender, amount);
        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();
    }

    // ------------------------------------------------------------------ views (frozen)

    /// @notice The pool with its effective state: CLOSING and CLOSED follow from time and open claims, so they show
    ///         here without any transaction.
    function getPool(bytes32 covenantId) external view returns (Pool memory pl, PoolParams memory) {
        pl = _pools[covenantId];
        pl.state = _poolState(covenantId);
        return (pl, _params[covenantId]);
    }

    function getClaim(bytes32 claimId) external view returns (Claim memory) {
        return _claims[claimId];
    }

    function getPass(uint256 passId) external view returns (Pass memory) {
        return _passes[passId];
    }

    function getInstallment(uint256 installmentId) external view returns (Installment memory) {
        return _book.installments[installmentId];
    }

    // solhint-disable-next-line func-name-mixedcase
    function DOMAIN_SEPARATOR() public view returns (bytes32) {
        return block.chainid == _cachedChainId
            ? _cachedDomainSeparator
            : AttestationLib.domainSeparator(block.chainid, address(this));
    }

    /// @notice EIP-712 digest an observer signs for a clean pass on this chain and pool.
    function cleanPassDigest(CleanPass calldata a) external view returns (bytes32) {
        return PoolVerify.cleanPassDigest(DOMAIN_SEPARATOR(), a);
    }

    /// @notice EIP-712 digest an observer signs for a MARS corroboration on this chain and pool.
    function corroborationDigest(Corroboration calldata a) external view returns (bytes32) {
        return PoolVerify.corroborationDigest(DOMAIN_SEPARATOR(), a);
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

    // ------------------------------------------------------------------ internal: pool

    /// @dev First `fund`: `PoolSetup.openingFacts` checks the opener, the params, the value rule and the clock (reverting
    ///      with this contract's error selectors) and returns the immutable covenant facts cached here.
    function _openPool(Pool storage pl, bytes32 covenantId, bytes32 policyHash, Mode mode, PoolParams calldata p)
        private
    {
        PoolSetup.Facts memory f =
            PoolSetup.openingFacts(covenant, covenantId, policyHash, mode == Mode.ONCHAIN, msg.value, p);
        pl.state = PoolState.OPEN;
        pl.mode = mode;
        pl.policyHash = policyHash;
        pl.paramsHash = keccak256(abi.encode(p));
        pl.opener = msg.sender;
        _params[covenantId] = p;

        PoolAux storage a = _aux[covenantId];
        a.end = f.end;
        a.linkWindow = f.linkWindow;
        a.closingAt = f.closingAt;
        a.k = f.k;
        a.seller = f.seller;
        a.sellerOrg = f.sellerOrg;
        a.buyerOrg = f.buyerOrg;
        a.relapseGrace = f.relapseGrace;
    }

    /// @dev Effective pool state. Only NONE and OPEN are ever stored; CLOSING / CLOSED follow from the clock and the
    ///      open-claim count. CLOSED is permanent: no claim opens after OPEN and the clock only moves forward.
    function _poolState(bytes32 covenantId) internal view returns (PoolState) {
        PoolState s = _pools[covenantId].state;
        if (s != PoolState.OPEN) return s;
        PoolAux storage a = _aux[covenantId];
        if (block.timestamp < a.closingAt) return PoolState.OPEN;
        return a.openClaims == 0 ? PoolState.CLOSED : PoolState.CLOSING;
    }

    // ------------------------------------------------------------------ internal: passes

    /// @dev COUNTED: bond back to the submitter, observer fee, count and instrument set; P9 re-checked here so the
    ///      resolution path cannot count past a live detection either.
    function _count(uint256 passId, Pass storage ps, Claim storage c) private {
        if (c.state != ClaimState.OPEN) revert ClaimNotOpen(); // §15 A2 audit (3)
        _requireNoLaterDetection(c);
        ps.state = PassState.COUNTED;
        uint32 counted = c.counted + 1;
        c.counted = counted;
        c.instrumentsSeen |= uint8(1 << ps.instrumentId);
        c.pending -= 1;
        uint256 bond = ps.bond;
        ps.bond = 0;
        _credit(ps.submitter, bond);
        bytes32 covenantId = c.covenantId;
        _payObserverFee(passId, ps.orgs, covenantId);
        emit CleanPassCounted(
            ps.claimId, passId, counted, _params[covenantId].upperBoundBp[(counted < 32 ? counted : 32) - 1]
        );
        PoolInstallments.afterCount(
            _book, _claims, _pools[covenantId], _params[covenantId], _aux[covenantId], ps.claimId, ps.sceneAt
        );
    }

    // ------------------------------------------------------------------ internal: installments

    /// @dev An effective corroboration: `corroboratedTo` moves forward; the final share is scheduled if now covered.
    function _corroborated(bytes32 claimId, uint64 windowTo) private {
        Claim storage c = _claims[claimId];
        c.corroboratedTo = windowTo;
        bytes32 covenantId = c.covenantId;
        PoolInstallments.afterCorroboration(
            _book, _claims, _pools[covenantId], _params[covenantId], _aux[covenantId], claimId
        );
    }

    /// @dev A pass leaves the claim's open set without counting (VOID / EXPIRED): its scene may be attested again.
    function _closePass(Pass storage ps, Claim storage c, PassState terminal) private {
        ps.state = terminal;
        c.pending -= 1;
        _seenScene[ps.claimId][ps.sceneId] = false;
    }

    /// @dev Observer fee (ONCHAIN only): `observerFeeWei` split equally among the controllers of the distinct orgs that
    ///      signed the pass, paid from the sponsors' unreserved balance only when it covers the whole fee. An org
    ///      without a controller forfeits its share to the pool; integer-division dust stays in the pool.
    function _payObserverFee(uint256 passId, bytes32[] storage orgs, bytes32 covenantId) private {
        Pool storage pl = _pools[covenantId];
        uint256 fee = _params[covenantId].observerFeeWei;
        if (pl.mode != Mode.ONCHAIN || fee == 0 || pl.balance - pl.reserved < fee) return;
        pl.balance -= PoolVerify.payObserverFee(passId, orgs, fee, registry, withdrawable);
    }

    /// @dev P9 (see `PoolEpisodes.requireNoLaterDetection`): SourceUnderReview / RelapsePending.
    function _requireNoLaterDetection(Claim storage c) private view {
        PoolEpisodes.requireNoLaterDetection(covenant, c.covenantId, c.sourceId, c.episodeId, c.since);
    }

    function _checkBatchShape(uint256 n, uint256 nSigs, uint256 required) private pure {
        if (n != nSigs || n > MAX_ATTESTATIONS) revert BadParams();
        if (n < required) revert QuorumNotMet(0, required);
    }

    function _credit(address to, uint256 amount) internal {
        if (amount == 0) return;
        withdrawable[to] += amount;
        emit Credited(to, amount);
    }
}

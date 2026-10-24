// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {MethaneCovenant} from "./MethaneCovenant.sol";
import {IncentivePool} from "./IncentivePool.sol";

/// @title SuretyBond — a seller's bond posted by a surety (spec-v2 §3 "SuretyBond"; §9.7 R25)
/// @notice The bond can be posted by a surety; settlement and refunds are assigned to it on chain. This contract's
///         address is registered as the seller org's SELLER key by that org's controller (it never signs: the core
///         gates seller functions on `msg.sender`); the funder needs no registry role. Roles:
///         - `operator` (the real seller [SIM in the demo]) may only `createCovenant`, `contest` and
///           `recordMitigationClaim` through it;
///         - `funder` (the surety [SIM]) posts the bond with the core's own `fund(covenantId)` (anyone may), receives
///           everything this contract ever holds through `withdraw()`, and can `revokeSelf()` to stop new covenants;
///         - anyone may `pull()` the core's and the pool's credits (settlement refund, cure refunds, a failed
///           challenger's bond) into this contract.
///         No function sends ETH anywhere except to `funder`. No admin, no upgrade path.
contract SuretyBond {
    MethaneCovenant public immutable covenant;
    IncentivePool public immutable pool; // may be address(0): a core-only surety
    address public immutable operator;
    address public immutable funder;

    /// @notice A covenant was created through the bond; `bond` is the ETH the operator sent with it (0 if the surety
    ///         funds it afterwards with `covenant.fund`).
    event CovenantCreatedThroughBond(bytes32 indexed covenantId, address indexed operator, uint256 bond);
    /// @notice Credits brought home from the core and the pool; they now belong to `funder`.
    event Pulled(address indexed caller, uint256 fromCovenant, uint256 fromPool);
    event FunderWithdrawn(address indexed funder, uint256 amount);
    /// @notice The funder closed this contract's SELLER key: no new covenants; existing ones keep running.
    event SelfRevoked(address indexed funder);

    error ZeroAddress();
    error PoolMismatch();
    error NotOperator();
    error NotFunder();
    error NothingToWithdraw();
    error TransferFailed();

    constructor(MethaneCovenant covenant_, IncentivePool pool_, address operator_, address funder_) {
        if (address(covenant_) == address(0) || operator_ == address(0) || funder_ == address(0)) revert ZeroAddress();
        if (address(pool_) != address(0) && address(pool_.covenant()) != address(covenant_)) revert PoolMismatch();
        covenant = covenant_;
        pool = pool_;
        operator = operator_;
        funder = funder_;
    }

    /// @notice Accepts ETH and does nothing else, so the core's and the pool's `withdraw()` can never revert on it.
    receive() external payable {}

    // ------------------------------------------------------------------ operator: seller functions only

    /// @notice The operator creates a covenant whose seller is this contract (`p.buyer` must be a registered BUYER key
    ///         of another org; the core checks). ETH sent along becomes the initial bond.
    function createCovenant(MethaneCovenant.CovenantParams calldata p) external payable returns (bytes32 covenantId) {
        if (msg.sender != operator) revert NotOperator();
        covenantId = covenant.createCovenant{value: msg.value}(p);
        emit CovenantCreatedThroughBond(covenantId, msg.sender, msg.value);
    }

    /// @notice The operator disputes an episode's attribution on one of its covenants (holds the rebate, never slashes).
    function contest(uint256 episodeId) external {
        if (msg.sender != operator) revert NotOperator();
        covenant.contest(episodeId);
    }

    /// @notice Logged by the core and inert there: non-detection is never evidence of repair.
    function recordMitigationClaim(bytes32 covenantId, bytes32 sourceId, bytes32 claimHash) external {
        if (msg.sender != operator) revert NotOperator();
        covenant.recordMitigationClaim(covenantId, sourceId, claimHash);
    }

    // ------------------------------------------------------------------ money: only ever to the funder

    /// @notice Permissionless: brings this contract's credits home from the core and the pool (pull payments there).
    function pull() external returns (uint256 fromCovenant, uint256 fromPool) {
        fromCovenant = covenant.withdrawable(address(this));
        if (fromCovenant != 0) covenant.withdraw();
        if (address(pool) != address(0)) {
            fromPool = pool.withdrawable(address(this));
            if (fromPool != 0) pool.withdraw();
        }
        if (fromCovenant == 0 && fromPool == 0) revert NothingToWithdraw();
        emit Pulled(msg.sender, fromCovenant, fromPool);
    }

    /// @notice The funder takes everything this contract holds. A failed transfer reverts and keeps the balance here.
    function withdraw() external {
        if (msg.sender != funder) revert NotFunder();
        uint256 amount = address(this).balance;
        if (amount == 0) revert NothingToWithdraw();
        emit FunderWithdrawn(msg.sender, amount);
        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();
    }

    /// @notice The funder withdraws its backing for new covenants: this contract revokes its own SELLER key in the
    ///         registry (a key may revoke itself). Existing covenants are unaffected.
    function revokeSelf() external {
        if (msg.sender != funder) revert NotFunder();
        covenant.registry().revokeKey(address(this));
        emit SelfRevoked(msg.sender);
    }
}

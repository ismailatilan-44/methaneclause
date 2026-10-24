// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Script} from "forge-std/Script.sol";
import {KeyRegistry} from "../src/KeyRegistry.sol";
import {MethaneCovenant} from "../src/MethaneCovenant.sol";

/// @notice The judge sandbox [SIM]: a separate KeyRegistry + MethaneCovenant pair whose keys are *published*
///         (keccak256 of public strings, `sandboxKey`), so a judge with a fresh wallet can play every role.
///
/// Orgs: SANDBOX_SELLER, SANDBOX_BUYER, SANDBOX_OBS_1, SANDBOX_OBS_2 (controller = deployer, so a griefer holding a
/// published key can revoke it but not take the org over; the deployer registers the next key generation), and
/// **OBS_JUDGE**, whose controller is itself a published key: any judge uses it to register their *own* wallet as an
/// OBSERVER key of OBS_JUDGE, so one of the two signatures on their episode is theirs (`registerKey`, not
/// `rotateKey`, so several judges can hold valid keys at once).
///
/// **OBS_INDEPENDENT** (spec-v2 §1 "open observer slot"): another observer org whose controller is a published key,
/// so anyone can register a key there; a signature from it shows "registered through the open slot by a wallet we do
/// not hold — see the registration tx". It claims no independence by itself (§9.7 R26: disclosure, no mechanism).
///
/// Separate from the main deployment on purpose: every covenant trusts every OBSERVER org of its registry, so
/// published observer keys in the main registry would let anyone reach the main covenant's quorum.
abstract contract SandboxBase is Script {
    string internal constant SANDBOX_KEY_PREFIX = "MethaneClause judge sandbox v1 [SIM] testnet-only / ";
    uint96 internal constant SANDBOX_PROPOSER_BOND = 0.00002 ether;
    uint96 internal constant SANDBOX_CHALLENGER_BOND = 0.00004 ether;

    struct Sandbox {
        KeyRegistry registry;
        MethaneCovenant covenant;
        address seller;
        address buyer;
        address obs1;
        address obs2;
        address judgeController;
        address independentController;
    }

    function sandboxKey(string memory role) public pure returns (uint256) {
        return uint256(keccak256(bytes(string.concat(SANDBOX_KEY_PREFIX, role))));
    }

    /// @dev Called inside a broadcast by the deployer.
    function _sandbox(address deployer, bool renounce) internal returns (Sandbox memory sb) {
        sb.registry = new KeyRegistry(deployer);
        sb.covenant = new MethaneCovenant(sb.registry, SANDBOX_PROPOSER_BOND, SANDBOX_CHALLENGER_BOND);
        sb.seller = vm.addr(sandboxKey("SELLER"));
        sb.buyer = vm.addr(sandboxKey("BUYER"));
        sb.obs1 = vm.addr(sandboxKey("OBS_1"));
        sb.obs2 = vm.addr(sandboxKey("OBS_2"));
        sb.judgeController = vm.addr(sandboxKey("JUDGE_CONTROLLER"));
        sb.independentController = vm.addr(sandboxKey("INDEPENDENT_CONTROLLER"));
        _sandboxOrg(sb.registry, "SANDBOX_SELLER", KeyRegistry.Role.SELLER, sb.seller, deployer);
        _sandboxOrg(sb.registry, "SANDBOX_BUYER", KeyRegistry.Role.BUYER, sb.buyer, deployer);
        _sandboxOrg(sb.registry, "SANDBOX_OBS_1", KeyRegistry.Role.OBSERVER, sb.obs1, deployer);
        _sandboxOrg(sb.registry, "SANDBOX_OBS_2", KeyRegistry.Role.OBSERVER, sb.obs2, deployer);
        sb.registry.setOrgController("OBS_JUDGE", sb.judgeController); // no key yet: each judge registers their own
        sb.registry.setOrgController("OBS_INDEPENDENT", sb.independentController); // the open observer slot
        if (renounce) sb.registry.renounceAdmin();
    }

    function _sandboxOrg(KeyRegistry registry, bytes32 orgId, KeyRegistry.Role role, address key, address controller)
        internal
    {
        registry.setOrgController(orgId, controller);
        registry.registerKey(orgId, role, key, 1, bytes32(0));
    }

    function _sandboxJson(Sandbox memory sb) internal returns (string memory) {
        string memory s = "sandbox";
        vm.serializeAddress(s, "keyRegistry", address(sb.registry));
        vm.serializeAddress(s, "methaneCovenant", address(sb.covenant));
        vm.serializeAddress(s, "seller", sb.seller);
        vm.serializeAddress(s, "buyer", sb.buyer);
        vm.serializeAddress(s, "observer1", sb.obs1);
        vm.serializeAddress(s, "observer2", sb.obs2);
        vm.serializeAddress(s, "judgeController", sb.judgeController);
        vm.serializeAddress(s, "independentController", sb.independentController);
        vm.serializeString(s, "keyDerivation", string.concat("keccak256(\"", SANDBOX_KEY_PREFIX, "<ROLE>\")"));
        vm.serializeUint(s, "proposerBond", sb.covenant.proposerBond());
        vm.serializeUint(s, "challengerBond", sb.covenant.challengerBond());
        return vm.serializeAddress(s, "registryAdmin", sb.registry.admin());
    }
}

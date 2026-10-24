// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {console} from "forge-std/Script.sol";
import {KeyRegistry} from "../src/KeyRegistry.sol";
import {MethaneCovenant} from "../src/MethaneCovenant.sol";
import {SandboxBase} from "./SandboxBase.sol";

/// @notice Deploys KeyRegistry + MethaneCovenant, registers the seller/buyer/observer keys, renounces the registry
///         admin, and writes `deployments/<chainId>.json`.
///
/// It also deploys a second, separate pair: the **judge sandbox** [SIM] (`SandboxBase`; `DeploySandbox.s.sol`
/// redeploys only that pair). No function lets the deployer move funds in either pair (invariant I7).
///
/// Anvil (chainId 31337): no env needed; uses the public Anvil test mnemonic (account 0 deployer, 1 seller,
///   2 buyer, 3-5 observers A-C). These are well-known throwaway keys, never use them on a public network.
///     forge script script/Deploy.s.sol --rpc-url http://127.0.0.1:8545 --broadcast
///
/// Base Sepolia (chainId 84532) or any other chain: everything comes from env, nothing is defaulted.
///     PRIVATE_KEY_DEPLOYER   deployer / temporary registry admin (uint, 0x-prefixed)
///     SELLER_ADDR BUYER_ADDR OBSERVER_A_ADDR OBSERVER_B_ADDR OBSERVER_C_ADDR
///     optional: OBSERVER_{A,B,C}_PQ_HASH (bytes32, registers algId 2 = ECDSA + ML-DSA-44)
///     optional: PROPOSER_BOND_WEI, CHALLENGER_BOND_WEI, RENOUNCE_ADMIN (default true)
///     forge script script/Deploy.s.sol --rpc-url $SEPOLIA_RPC_URL --broadcast --verify
contract Deploy is SandboxBase {
    string internal constant ANVIL_MNEMONIC = "test test test test test test test test test test test junk";

    bytes32 internal constant ORG_SELLER = "ORG_SELLER_SIM";
    bytes32 internal constant ORG_BUYER = "ORG_BUYER_SIM";
    bytes32 internal constant ORG_OBS_A = "OBS_A";
    bytes32 internal constant ORG_OBS_B = "OBS_B";
    bytes32 internal constant ORG_OBS_C = "OBS_C";

    struct Parties {
        address seller;
        address buyer;
        address obsA;
        address obsB;
        address obsC;
        bytes32 pqA;
        bytes32 pqB;
        bytes32 pqC;
    }

    function run() external returns (KeyRegistry registry, MethaneCovenant covenant) {
        bool anvil = block.chainid == 31337;
        uint256 deployerPk = anvil ? vm.deriveKey(ANVIL_MNEMONIC, 0) : vm.envUint("PRIVATE_KEY_DEPLOYER");
        address deployer = vm.addr(deployerPk);
        Parties memory p = anvil ? _anvilParties() : _envParties();
        uint96 proposerBond = uint96(vm.envOr("PROPOSER_BOND_WEI", uint256(0.0001 ether)));
        uint96 challengerBond = uint96(vm.envOr("CHALLENGER_BOND_WEI", uint256(0.0002 ether)));
        bool renounce = vm.envOr("RENOUNCE_ADMIN", true);

        vm.startBroadcast(deployerPk);
        registry = new KeyRegistry(deployer);
        covenant = new MethaneCovenant(registry, proposerBond, challengerBond);

        _org(registry, ORG_SELLER, KeyRegistry.Role.SELLER, p.seller, bytes32(0));
        _org(registry, ORG_BUYER, KeyRegistry.Role.BUYER, p.buyer, bytes32(0));
        _org(registry, ORG_OBS_A, KeyRegistry.Role.OBSERVER, p.obsA, p.pqA);
        _org(registry, ORG_OBS_B, KeyRegistry.Role.OBSERVER, p.obsB, p.pqB);
        _org(registry, ORG_OBS_C, KeyRegistry.Role.OBSERVER, p.obsC, p.pqC);
        if (renounce) registry.renounceAdmin();
        Sandbox memory sb = _sandbox(deployer, renounce);
        vm.stopBroadcast();

        _write(registry, covenant, p, deployer, sb);
        console.log("KeyRegistry    ", address(registry));
        console.log("MethaneCovenant", address(covenant));
        console.log("Sandbox KeyRegistry    ", address(sb.registry));
        console.log("Sandbox MethaneCovenant", address(sb.covenant));
    }

    /// @dev Each org controls its own keys (controller = its first key), so rotation works after renouncing.
    function _org(KeyRegistry registry, bytes32 orgId, KeyRegistry.Role role, address key, bytes32 pqHash) internal {
        registry.setOrgController(orgId, key);
        registry.registerKey(orgId, role, key, pqHash == bytes32(0) ? 1 : 2, pqHash);
    }

    /// @dev OBSERVER_{A,B,C}_PQ_HASH are optional here too: scripts/demo.sh sets them (observer/pq.py public test
    ///      seeds) so the local demo registers its observers as algId 2 = ECDSA + ML-DSA-44.
    function _anvilParties() internal view returns (Parties memory p) {
        p.seller = vm.addr(vm.deriveKey(ANVIL_MNEMONIC, 1));
        p.buyer = vm.addr(vm.deriveKey(ANVIL_MNEMONIC, 2));
        p.obsA = vm.addr(vm.deriveKey(ANVIL_MNEMONIC, 3));
        p.obsB = vm.addr(vm.deriveKey(ANVIL_MNEMONIC, 4));
        p.obsC = vm.addr(vm.deriveKey(ANVIL_MNEMONIC, 5));
        p.pqA = vm.envOr("OBSERVER_A_PQ_HASH", bytes32(0));
        p.pqB = vm.envOr("OBSERVER_B_PQ_HASH", bytes32(0));
        p.pqC = vm.envOr("OBSERVER_C_PQ_HASH", bytes32(0));
    }

    function _envParties() internal view returns (Parties memory p) {
        p.seller = vm.envAddress("SELLER_ADDR");
        p.buyer = vm.envAddress("BUYER_ADDR");
        p.obsA = vm.envAddress("OBSERVER_A_ADDR");
        p.obsB = vm.envAddress("OBSERVER_B_ADDR");
        p.obsC = vm.envAddress("OBSERVER_C_ADDR");
        p.pqA = vm.envOr("OBSERVER_A_PQ_HASH", bytes32(0));
        p.pqB = vm.envOr("OBSERVER_B_PQ_HASH", bytes32(0));
        p.pqC = vm.envOr("OBSERVER_C_PQ_HASH", bytes32(0));
    }

    function _write(KeyRegistry registry, MethaneCovenant covenant, Parties memory p, address deployer, Sandbox memory sb)
        internal
    {
        string memory sbJson = _sandboxJson(sb);

        string memory o = "deployment";
        vm.serializeString(o, "sandbox", sbJson);
        vm.serializeUint(o, "chainId", block.chainid);
        vm.serializeAddress(o, "keyRegistry", address(registry));
        vm.serializeAddress(o, "methaneCovenant", address(covenant));
        vm.serializeAddress(o, "deployer", deployer);
        vm.serializeAddress(o, "seller", p.seller);
        vm.serializeAddress(o, "buyer", p.buyer);
        vm.serializeAddress(o, "observerA", p.obsA);
        vm.serializeAddress(o, "observerB", p.obsB);
        vm.serializeAddress(o, "observerC", p.obsC);
        vm.serializeUint(o, "proposerBond", covenant.proposerBond());
        vm.serializeUint(o, "challengerBond", covenant.challengerBond());
        string memory json = vm.serializeAddress(o, "registryAdmin", registry.admin());
        vm.writeJson(json, string.concat(vm.projectRoot(), "/deployments/", vm.toString(block.chainid), ".json"));
    }
}

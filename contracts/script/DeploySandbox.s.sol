// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {console} from "forge-std/Script.sol";
import {SandboxBase} from "./SandboxBase.sol";

/// @notice Redeploys only the judge sandbox [SIM] pair and replaces the `sandbox` entry of
///         `deployments/<chainId>.json`; the main deployment is untouched. Env: PRIVATE_KEY_DEPLOYER.
///     forge script script/DeploySandbox.s.sol --rpc-url $SEPOLIA_RPC_URL --broadcast --verify
contract DeploySandbox is SandboxBase {
    function run() external {
        uint256 deployerPk = vm.envUint("PRIVATE_KEY_DEPLOYER");
        vm.startBroadcast(deployerPk);
        Sandbox memory sb = _sandbox(vm.addr(deployerPk), true);
        vm.stopBroadcast();
        string memory path = string.concat(vm.projectRoot(), "/deployments/", vm.toString(block.chainid), ".json");
        vm.writeJson(_sandboxJson(sb), path, ".sandbox");
        console.log("Sandbox KeyRegistry    ", address(sb.registry));
        console.log("Sandbox MethaneCovenant", address(sb.covenant));
    }
}

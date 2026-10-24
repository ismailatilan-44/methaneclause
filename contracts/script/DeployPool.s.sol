// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {console} from "forge-std/Script.sol";
import {MethaneCovenant} from "../src/MethaneCovenant.sol";
import {IncentivePool} from "../src/IncentivePool.sol";
import {SandboxBase} from "./SandboxBase.sol";

/// @notice Deploys the IncentivePool (spec-v2 §3, §10) with its five linked libraries, records every address and the
///         deployed bytecode sizes in `deployments/<chainId>.json`, and prints the Etherscan verify commands.
///
/// `run()`: the main pool over the deployment record's `methaneCovenant`. With `POOL_COVENANT_ID` set it is opened by
///   the buyer [SIM] (`BUYER_PK`) under the POLICY_V2 on-chain params (pipeline/tests/vectors/policy_v2.json:
///   ONCHAIN, `paramsHash == onchainParamsHash`) with `POOL_FUND_WEI` (default 0.001 ETH [SIM]). Writes the new keys
///   `incentivePool`, `incentivePoolLibraries`, `incentivePoolSizes` (replaced on a re-run); other entries are left
///   as they are.
/// `runSandbox()`: redeploys the judge sandbox [SIM] pair (SandboxBase: published keys, OBS_JUDGE and the
///   OBS_INDEPENDENT open slot) with its pool, moves the previous `sandbox` entry to `sandboxPrevious` (that pair stays
///   on chain; the record keeps one earlier pair, so a second redeploy overwrites it) and replaces `sandbox` with the
///   new pair and its `incentivePool` (the key judge-cure reads). The sandbox pool is not opened here: each judge's
///   cure opens it for their own covenant.
///
/// Anvil (chain id 31337): Anvil's public test accounts (0 deployer, 2 buyer); no env needed.
/// Any other chain: `PRIVATE_KEY_DEPLOYER` (and `BUYER_PK` to open the main pool) from the operator's key file; a
///   missing key stops the script before anything is sent. `DEPLOYMENT_FILE` overrides the record path (dry runs).
///     forge script script/DeployPool.s.sol --rpc-url $SEPOLIA_RPC_URL --broadcast --slow --verify
///     forge script script/DeployPool.s.sol --sig "runSandbox()" --rpc-url $SEPOLIA_RPC_URL --broadcast --slow --verify
/// Forge deploys and links the libraries itself; the addresses recorded here are read back from the pool's deployed
/// bytecode at the artifact's link offsets, so they are exactly the libraries the pool calls, and each library's code
/// is checked against its artifact. The record is written while the script runs, before the broadcast is confirmed:
/// the make targets therefore check every recorded address on chain afterwards (`script/check_pool_record.py`) and
/// stop with the restore command if one is missing.
contract DeployPool is SandboxBase {
    string internal constant ANVIL_MNEMONIC = "test test test test test test test test test test test junk";
    string internal constant MISSING_KEY =
        "DeployPool: PRIVATE_KEY_DEPLOYER is not set - the operator links the key file (names in .env.example); nothing was sent";
    string internal constant MISSING_BUYER =
        "DeployPool: BUYER_PK is not set but POOL_COVENANT_ID is - the buyer [SIM] opens the main pool; nothing was sent";
    string internal constant LIBRARY_MISMATCH = "DeployPool: linked library code does not match its artifact";
    string internal constant PARAMS_DRIFT = "DeployPool: params drift";

    struct Config {
        uint256 deployerPk;
        uint256 buyerPk; // opens the pool for `covenantId` (0: deploy only)
        bytes32 covenantId; // 0: do not open
        uint256 fundWei;
        string recordPath;
    }

    struct Deployed {
        IncentivePool pool;
        address[5] libs;
        uint256[5] libSizes;
        uint256 poolSize;
    }

    function _libNames() internal pure returns (string[5] memory) {
        return ["PoolVerify", "PoolSetup", "PoolEpisodes", "PoolCorroboration", "PoolInstallments"];
    }

    // ------------------------------------------------------------------ entry points (env)

    function run() external {
        deployMain(_envConfig(true));
    }

    function runSandbox() external {
        deploySandboxWithPool(_envConfig(false));
    }

    function _envConfig(bool main) internal view returns (Config memory c) {
        c.recordPath = vm.envOr(
            "DEPLOYMENT_FILE", string.concat(vm.projectRoot(), "/deployments/", vm.toString(block.chainid), ".json")
        );
        c.fundWei = vm.envOr("POOL_FUND_WEI", uint256(0.001 ether));
        if (main) c.covenantId = vm.envOr("POOL_COVENANT_ID", bytes32(0));
        if (block.chainid == 31337) {
            c.deployerPk = vm.deriveKey(ANVIL_MNEMONIC, 0);
            c.buyerPk = vm.deriveKey(ANVIL_MNEMONIC, 2);
            return c;
        }
        (c.deployerPk, c.buyerPk) = operatorKeys(
            main && c.covenantId != bytes32(0),
            vm.envOr("PRIVATE_KEY_DEPLOYER", string("")),
            vm.envOr("BUYER_PK", string(""))
        );
    }

    /// @notice Off Anvil: the deployer key, and the buyer [SIM] key when the main pool is opened. Unset or empty stops
    ///         the script with a clear message before anything is sent (instead of a parse error).
    function operatorKeys(bool needBuyer, string memory deployerRaw, string memory buyerRaw)
        public
        pure
        returns (uint256 deployerPk, uint256 buyerPk)
    {
        require(bytes(deployerRaw).length != 0, MISSING_KEY);
        deployerPk = vm.parseUint(deployerRaw);
        require(deployerPk != 0, MISSING_KEY);
        if (needBuyer) {
            require(bytes(buyerRaw).length != 0, MISSING_BUYER);
            buyerPk = vm.parseUint(buyerRaw);
            require(buyerPk != 0, MISSING_BUYER);
        }
    }

    // ------------------------------------------------------------------ main pool

    function deployMain(Config memory c) public returns (address) {
        string memory rec = vm.readFile(c.recordPath);
        MethaneCovenant cov = MethaneCovenant(vm.parseJsonAddress(rec, ".methaneCovenant"));
        vm.startBroadcast(c.deployerPk);
        IncentivePool pool = new IncentivePool(cov);
        vm.stopBroadcast();
        require(address(pool.registry()) == address(cov.registry()), "DeployPool: pool registry != covenant registry");

        if (c.covenantId != bytes32(0)) {
            (IncentivePool.PoolParams memory p, bytes32 policyHash) =
                policyV2From(string.concat(vm.projectRoot(), "/../pipeline/tests/vectors/policy_v2.json"));
            vm.startBroadcast(c.buyerPk);
            pool.fund{value: c.fundWei}(c.covenantId, policyHash, IncentivePool.Mode.ONCHAIN, p);
            vm.stopBroadcast();
            console.log("pool opened by the buyer [SIM] for covenant", vm.toString(c.covenantId));
        }

        Deployed memory d = _inspect(pool);
        string memory libs = _libsJson("mainLibs", d);
        string memory sizes = _sizesJson("mainSizes", d);
        vm.writeJson(string.concat('"', vm.toString(address(pool)), '"'), c.recordPath, ".incentivePool");
        vm.writeJson(libs, c.recordPath, ".incentivePoolLibraries");
        vm.writeJson(sizes, c.recordPath, ".incentivePoolSizes");
        _logVerify(d, address(cov));
        return address(pool);
    }

    // ------------------------------------------------------------------ sandbox pair + pool + OBS_INDEPENDENT

    function deploySandboxWithPool(Config memory c) public returns (address) {
        string memory rec = vm.readFile(c.recordPath);
        // `forge script --broadcast` runs this function twice (simulation, then again once the libraries are linked);
        // the first pass leaves a phantom `sandbox` entry whose contracts do not exist on chain. Only a pair that is
        // really deployed becomes `sandboxPrevious`; otherwise the entry written by the earlier pass is kept.
        address prevRegistry = vm.parseJsonAddress(rec, ".sandbox.keyRegistry");
        bool realPrevious = prevRegistry.code.length != 0;
        string memory prevJson = "";
        if (realPrevious) {
            string memory prev = "sandboxPrevious";
            vm.serializeAddress(prev, "keyRegistry", prevRegistry);
            prevJson =
                vm.serializeAddress(prev, "methaneCovenant", vm.parseJsonAddress(rec, ".sandbox.methaneCovenant"));
        }

        vm.startBroadcast(c.deployerPk);
        Sandbox memory sb = _sandbox(vm.addr(c.deployerPk), true);
        IncentivePool pool = new IncentivePool(sb.covenant);
        vm.stopBroadcast();

        Deployed memory d = _inspect(pool);
        string memory libs = _libsJson("sandboxLibs", d);
        string memory sizes = _sizesJson("sandboxSizes", d);
        _sandboxJson(sb);
        vm.serializeString("sandbox", "incentivePoolLibraries", libs);
        vm.serializeString("sandbox", "incentivePoolSizes", sizes);
        string memory sbJson = vm.serializeAddress("sandbox", "incentivePool", address(pool));
        if (realPrevious) vm.writeJson(prevJson, c.recordPath, ".sandboxPrevious");
        vm.writeJson(sbJson, c.recordPath, ".sandbox");
        console.log("Sandbox KeyRegistry    ", address(sb.registry));
        console.log("Sandbox MethaneCovenant", address(sb.covenant));
        _logVerify(d, address(sb.covenant));
        return address(pool);
    }

    // ------------------------------------------------------------------ helpers

    /// @dev Library addresses read from the pool's deployed bytecode at the artifact's link offsets, each checked
    ///      against its own artifact (`checkLibrary`); sizes from the deployed bytecode.
    function _inspect(IncentivePool pool) internal view returns (Deployed memory d) {
        string memory art = vm.readFile(string.concat(vm.projectRoot(), "/out/IncentivePool.sol/IncentivePool.json"));
        string[5] memory names = _libNames();
        bytes memory code = address(pool).code;
        d.pool = pool;
        d.poolSize = code.length;
        for (uint256 i; i < 5; ++i) {
            uint256 start = vm.parseJsonUint(
                art,
                string.concat(".deployedBytecode.linkReferences.['src/lib/", names[i], ".sol'].", names[i], "[0].start")
            );
            address lib;
            assembly ("memory-safe") {
                lib := shr(96, mload(add(add(code, 32), start)))
            }
            checkLibrary(names[i], lib);
            d.libs[i] = lib;
            d.libSizes[i] = lib.code.length;
        }
    }

    /// @notice `lib` holds exactly the compiled library `name`: its deployed code equals the artifact's
    ///         `deployedBytecode` once the library's own address (the call guard `PUSH20 <address>` at bytes 1..20,
    ///         zero in the artifact) is masked. Any other contract, or another of the five libraries, is refused.
    function checkLibrary(string memory name, address lib) public view {
        bytes memory expected = vm.parseJsonBytes(
            vm.readFile(string.concat(vm.projectRoot(), "/out/", name, ".sol/", name, ".json")),
            ".deployedBytecode.object"
        );
        bytes memory got = lib.code;
        require(got.length == expected.length && got.length > 21 && got[0] == 0x73, LIBRARY_MISMATCH);
        for (uint256 i = 1; i <= 20; ++i) {
            got[i] = 0;
        }
        require(keccak256(got) == keccak256(expected), LIBRARY_MISMATCH);
    }

    function _libsJson(string memory key, Deployed memory d) internal returns (string memory out) {
        string[5] memory names = _libNames();
        for (uint256 i; i < 5; ++i) {
            out = vm.serializeAddress(key, names[i], d.libs[i]);
        }
    }

    function _sizesJson(string memory key, Deployed memory d) internal returns (string memory out) {
        string[5] memory names = _libNames();
        for (uint256 i; i < 5; ++i) {
            vm.serializeUint(key, names[i], d.libSizes[i]);
        }
        out = vm.serializeUint(key, "IncentivePool", d.poolSize);
    }

    /// @notice POLICY_V2's on-chain PoolParams, rebuilt from the pipeline's vector at `path` in the frozen layout (as
    ///         PolicyParity.t.sol); refuses a vector whose fields no longer hash to its `onchainParamsHash`.
    function policyV2From(string memory path)
        public
        view
        returns (IncentivePool.PoolParams memory p, bytes32 policyHash)
    {
        string memory v = vm.readFile(path);
        p.publicationLag = uint32(vm.parseJsonUint(v, ".fields[0].value"));
        p.cleanWindow = uint32(vm.parseJsonUint(v, ".fields[1].value"));
        p.resolveWindow = uint32(vm.parseJsonUint(v, ".fields[2].value"));
        p.holdWindow = uint32(vm.parseJsonUint(v, ".fields[3].value"));
        p.cureTerm = uint32(vm.parseJsonUint(v, ".fields[4].value"));
        p.passBond = uint96(vm.parseJsonUint(v, ".fields[5].value"));
        p.challengerBond = uint96(vm.parseJsonUint(v, ".fields[6].value"));
        p.observerFeeWei = uint96(vm.parseJsonUint(v, ".fields[7].value"));
        for (uint256 i; i < 3; ++i) {
            string memory idx = vm.toString(i);
            p.unitTierAmounts[i] = uint128(vm.parseJsonUint(v, string.concat(".fields[8].value[", idx, "]")));
            string memory r = string.concat(".fields[9].value[", idx, "]");
            p.installments[i] = IncentivePool.InstallmentRule({
                afterPasses: uint8(vm.parseJsonUint(v, string.concat(r, ".afterPasses"))),
                shareBp: uint16(vm.parseJsonUint(v, string.concat(r, ".shareBp"))),
                twoInstruments: vm.parseJsonBool(v, string.concat(r, ".twoInstruments")),
                isFinal: vm.parseJsonBool(v, string.concat(r, ".isFinal"))
            });
        }
        for (uint256 i; i < 32; ++i) {
            p.upperBoundBp[i] = uint16(vm.parseJsonUint(v, string.concat(".fields[10].value[", vm.toString(i), "]")));
        }
        require(keccak256(abi.encode(p)) == vm.parseJsonBytes32(v, ".onchainParamsHash"), PARAMS_DRIFT);
        policyHash = vm.parseJsonBytes32(v, ".policyHash");
    }

    /// @dev The Etherscan verify commands for this chain (`--verify` on the broadcast does the same automatically).
    function _logVerify(Deployed memory d, address covenantAddr) internal view {
        string[5] memory names = _libNames();
        string memory verify = string.concat("forge verify-contract --chain ", vm.toString(block.chainid), " ");
        string memory linkArgs = "";
        for (uint256 i; i < 5; ++i) {
            console.log(string.concat(verify, vm.toString(d.libs[i]), " src/lib/", names[i], ".sol:", names[i]));
            linkArgs = string.concat(
                linkArgs, " --libraries src/lib/", names[i], ".sol:", names[i], ":", vm.toString(d.libs[i])
            );
        }
        console.log(
            string.concat(
                verify,
                vm.toString(address(d.pool)),
                " src/IncentivePool.sol:IncentivePool --constructor-args ",
                vm.toString(abi.encode(covenantAddr)),
                linkArgs
            )
        );
    }
}

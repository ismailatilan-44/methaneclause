// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {PoolFixture} from "../utils/PoolFixture.sol";
import {KeyRegistry} from "../../src/KeyRegistry.sol";
import {MethaneCovenant} from "../../src/MethaneCovenant.sol";
import {IncentivePool} from "../../src/IncentivePool.sol";
import {DeployPool} from "../../script/DeployPool.s.sol";

/// @notice A dry run of `DeployPool.s.sol` on the Sepolia path (chain id 11155111, nothing broadcast to any
///         network): the main pool over the main covenant, opened by the buyer [SIM] with the pipeline's POLICY_V2 params;
///         the five linked libraries and the deployed sizes recorded from the pool's own bytecode; the judge sandbox
///         redeployed with its pool and the OBS_INDEPENDENT open slot, the previous sandbox kept in the record; a
///         missing key stops before anything is sent. Records go to a throwaway copy, never the committed file.
contract DeployPoolTest is PoolFixture {
    DeployPool internal dp;
    string internal record;
    string internal vec;
    KeyRegistry internal oldSbRegistry;
    MethaneCovenant internal oldSbCovenant;

    string internal constant MISSING_KEY =
        "DeployPool: PRIVATE_KEY_DEPLOYER is not set - the operator links the key file (names in .env.example); nothing was sent";
    string internal constant MISSING_BUYER =
        "DeployPool: BUYER_PK is not set but POOL_COVENANT_ID is - the buyer [SIM] opens the main pool; nothing was sent";
    string internal constant LIBRARY_MISMATCH = "DeployPool: linked library code does not match its artifact";

    string[5] internal names = ["PoolVerify", "PoolSetup", "PoolEpisodes", "PoolCorroboration", "PoolInstallments"];

    function setUp() public override {
        super.setUp();
        vm.chainId(11_155_111);
        dp = new DeployPool();
        vec = vm.readFile(string.concat(vm.projectRoot(), "/../pipeline/tests/vectors/policy_v2.json"));
    }

    /// @dev Forge runs tests in parallel: each test writes its own throwaway record (removed at the end).
    function _record(string memory name) internal {
        record = string.concat(vm.projectRoot(), "/deployments/test-deploypool-", name, ".json");
        // a minimal record shaped like the committed one: the main pair and an earlier sandbox pair
        // the earlier sandbox pair is real (deployed): only a deployed pair becomes `sandboxPrevious`
        oldSbRegistry = new KeyRegistry(address(this));
        oldSbCovenant = new MethaneCovenant(oldSbRegistry, PROPOSER_BOND, CHALLENGER_BOND);
        string memory sb = "sb";
        vm.serializeAddress(sb, "keyRegistry", address(oldSbRegistry));
        string memory sbJson = vm.serializeAddress(sb, "methaneCovenant", address(oldSbCovenant));
        string memory o = "rec";
        vm.serializeString(o, "sandbox", sbJson);
        vm.serializeAddress(o, "keyRegistry", address(registry));
        vm.serializeAddress(o, "seller", seller);
        string memory json = vm.serializeAddress(o, "methaneCovenant", address(cov));
        vm.writeJson(json, record);
    }

    function tearDown() internal {
        vm.removeFile(record);
    }

    function _cfg(uint256 deployerPk, uint256 buyerPk_, bytes32 covenantId)
        internal
        view
        returns (DeployPool.Config memory c)
    {
        c.deployerPk = deployerPk;
        c.buyerPk = buyerPk_;
        c.covenantId = covenantId;
        c.fundWei = 0.001 ether;
        c.recordPath = record;
    }

    function _codeAddressAt(address target, uint256 offset) internal view returns (address a) {
        bytes memory code = target.code;
        assembly ("memory-safe") {
            a := shr(96, mload(add(add(code, 32), offset)))
        }
    }

    function test_deployPool_mainPool_openedByBuyer_recordsLibrariesAndSizes() public {
        _record("main");
        (, uint256 deployerPk) = makeAddrAndKey("operator deployer [SIM]");
        vm.deal(vm.addr(deployerPk), 10 ether);
        address poolAddr = dp.deployMain(_cfg(deployerPk, buyerPk, covId));
        IncentivePool p = IncentivePool(payable(poolAddr));

        // the pool trusts exactly the covenant's registry and is open under POLICY_V2's on-chain params
        assertEq(address(p.covenant()), address(cov));
        assertEq(address(p.registry()), address(registry));
        (IncentivePool.Pool memory pl,) = p.getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.OPEN));
        assertEq(uint8(pl.mode), uint8(IncentivePool.Mode.ONCHAIN));
        assertEq(pl.opener, buyer);
        assertEq(pl.paramsHash, vm.parseJsonBytes32(vec, ".onchainParamsHash"));
        assertEq(pl.policyHash, vm.parseJsonBytes32(vec, ".policyHash"));

        // the record: pool, each library as linked into the pool's bytecode, deployed sizes; main entries unchanged
        string memory r = vm.readFile(record);
        assertEq(vm.parseJsonAddress(r, ".incentivePool"), poolAddr);
        assertEq(vm.parseJsonAddress(r, ".methaneCovenant"), address(cov));
        string memory art = vm.readFile(string.concat(vm.projectRoot(), "/out/IncentivePool.sol/IncentivePool.json"));
        for (uint256 i; i < 5; ++i) {
            address lib = vm.parseJsonAddress(r, string.concat(".incentivePoolLibraries.", names[i]));
            uint256 start = vm.parseJsonUint(
                art,
                string.concat(".deployedBytecode.linkReferences.['src/lib/", names[i], ".sol'].", names[i], "[0].start")
            );
            assertEq(lib, _codeAddressAt(poolAddr, start), names[i]);
            assertGt(lib.code.length, 0);
            assertEq(vm.parseJsonUint(r, string.concat(".incentivePoolSizes.", names[i])), lib.code.length);
        }
        assertEq(vm.parseJsonUint(r, ".incentivePoolSizes.IncentivePool"), poolAddr.code.length);
        assertLt(poolAddr.code.length, 24_577); // EIP-170, on the deployed bytecode
        tearDown();
    }

    function test_deployPool_mainPool_withoutCovenant_deploysOnly() public {
        _record("deployonly");
        (, uint256 deployerPk) = makeAddrAndKey("operator deployer [SIM]");
        vm.deal(vm.addr(deployerPk), 10 ether);
        address poolAddr = dp.deployMain(_cfg(deployerPk, 0, bytes32(0)));
        (IncentivePool.Pool memory pl,) = IncentivePool(payable(poolAddr)).getPool(covId);
        assertEq(uint8(pl.state), uint8(IncentivePool.PoolState.NONE));
        tearDown();
    }

    function test_deployPool_sandboxRedeploy_withPoolAndOpenSlot() public {
        _record("sandbox");
        (, uint256 deployerPk) = makeAddrAndKey("operator deployer [SIM]");
        vm.deal(vm.addr(deployerPk), 10 ether);
        address poolAddr = dp.deploySandboxWithPool(_cfg(deployerPk, 0, bytes32(0)));

        string memory r = vm.readFile(record);
        KeyRegistry sbReg = KeyRegistry(vm.parseJsonAddress(r, ".sandbox.keyRegistry"));
        MethaneCovenant sbCov = MethaneCovenant(vm.parseJsonAddress(r, ".sandbox.methaneCovenant"));
        assertEq(vm.parseJsonAddress(r, ".sandbox.incentivePool"), poolAddr); // the key judge-cure reads
        assertEq(address(IncentivePool(payable(poolAddr)).covenant()), address(sbCov));
        assertEq(address(sbCov.registry()), address(sbReg));
        // the open observer slot: a published controller, no key yet
        address indep = vm.parseJsonAddress(r, ".sandbox.independentController");
        assertEq(indep, vm.addr(dp.sandboxKey("INDEPENDENT_CONTROLLER")));
        assertEq(sbReg.orgController("OBS_INDEPENDENT"), indep);
        assertEq(sbReg.admin(), address(0)); // renounced
        // anyone holding the published controller key registers their own wallet
        address stranger_ = makeAddr("a wallet we do not hold");
        vm.prank(indep);
        sbReg.registerKey("OBS_INDEPENDENT", KeyRegistry.Role.OBSERVER, stranger_, 1, 0);
        assertTrue(sbReg.isValidAt(stranger_, KeyRegistry.Role.OBSERVER, block.timestamp));
        // the earlier sandbox pair stays recorded; the main pair is untouched
        assertEq(vm.parseJsonAddress(r, ".sandboxPrevious.keyRegistry"), address(oldSbRegistry));
        assertEq(vm.parseJsonAddress(r, ".sandboxPrevious.methaneCovenant"), address(oldSbCovenant));
        assertEq(vm.parseJsonAddress(r, ".methaneCovenant"), address(cov));
        for (uint256 i; i < 5; ++i) {
            assertGt(vm.parseJsonAddress(r, string.concat(".sandbox.incentivePoolLibraries.", names[i])).code.length, 0);
        }
        tearDown();
    }

    /// `forge script --broadcast` executes the function twice; a second pass must not record the first pass's phantom
    /// sandbox (no code on chain) as `sandboxPrevious`.
    function test_deployPool_sandboxRedeploy_secondPass_keepsRealPrevious() public {
        _record("sandbox2pass");
        (, uint256 deployerPk) = makeAddrAndKey("operator deployer [SIM]");
        vm.deal(vm.addr(deployerPk), 10 ether);
        uint256 snap = vm.snapshotState();
        dp.deploySandboxWithPool(_cfg(deployerPk, 0, bytes32(0))); // pass 1 (simulation): writes a sandbox entry
        vm.revertToState(snap); // its contracts are gone again, as on the real chain before pass 2
        dp = new DeployPool();
        dp.deploySandboxWithPool(_cfg(deployerPk, 0, bytes32(0))); // pass 2
        string memory r = vm.readFile(record);
        assertEq(vm.parseJsonAddress(r, ".sandboxPrevious.keyRegistry"), address(oldSbRegistry));
        tearDown();
    }

    /// Opening the main pool needs the buyer [SIM] key: an unset or empty one stops before anything is sent.
    function test_deployPool_missingBuyer_stopsBeforeSending() public {
        (uint256 d, uint256 b) = dp.operatorKeys(false, "7", "");
        assertEq(d, 7);
        assertEq(b, 0); // deploy only: no buyer needed
        vm.expectRevert(bytes(MISSING_BUYER));
        dp.operatorKeys(true, "7", "");
        vm.expectRevert(bytes(MISSING_BUYER));
        dp.operatorKeys(true, "7", "0");
        vm.expectRevert(bytes(MISSING_KEY));
        dp.operatorKeys(true, "0", "9");
    }

    /// A POLICY_V2 vector whose fields no longer hash to its `onchainParamsHash` is refused before the pool opens.
    function test_deployPool_paramsDrift_refused() public {
        _record("drift");
        string memory copy = string.concat(vm.projectRoot(), "/deployments/test-deploypool-drift-vector.json");
        vm.copyFile(string.concat(vm.projectRoot(), "/../pipeline/tests/vectors/policy_v2.json"), copy);
        (, bytes32 policyHash) = dp.policyV2From(copy); // unchanged copy: accepted
        assertEq(policyHash, vm.parseJsonBytes32(vec, ".policyHash"));
        string memory hold = vm.toString(vm.parseJsonUint(vec, ".fields[3].value")); // holdWindow, changed
        string memory drifted = vm.replace(vec, string.concat('"value": ', hold), string.concat('"value": ', hold, "1"));
        assertTrue(keccak256(bytes(drifted)) != keccak256(bytes(vec)));
        vm.writeFile(copy, drifted);
        vm.expectRevert(bytes("DeployPool: params drift"));
        dp.policyV2From(copy);
        vm.removeFile(copy);
        tearDown();
    }

    /// The recorded libraries are the compiled ones: another library, a non-library contract or an empty address
    /// under a library's name is refused.
    function test_deployPool_checkLibrary_refusesWrongCode() public {
        _record("libcheck");
        (, uint256 deployerPk) = makeAddrAndKey("operator deployer [SIM]");
        vm.deal(vm.addr(deployerPk), 10 ether);
        dp.deployMain(_cfg(deployerPk, 0, bytes32(0)));
        string memory r = vm.readFile(record);
        address verifyLib = vm.parseJsonAddress(r, ".incentivePoolLibraries.PoolVerify");
        address setupLib = vm.parseJsonAddress(r, ".incentivePoolLibraries.PoolSetup");
        dp.checkLibrary("PoolVerify", verifyLib);
        dp.checkLibrary("PoolSetup", setupLib);
        vm.expectRevert(bytes(LIBRARY_MISMATCH));
        dp.checkLibrary("PoolVerify", setupLib); // names swapped
        vm.expectRevert(bytes(LIBRARY_MISMATCH));
        dp.checkLibrary("PoolVerify", address(registry)); // a contract, not the library
        vm.expectRevert(bytes(LIBRARY_MISMATCH));
        dp.checkLibrary("PoolVerify", makeAddr("no code"));
        tearDown();
    }

    /// Off Anvil, a missing key stops the script before anything is sent (the operator links the key file).
    function test_deployPool_missingKey_stopsBeforeSending() public {
        _record("missingkey");
        vm.setEnv("PRIVATE_KEY_DEPLOYER", "");
        vm.setEnv("BUYER_PK", "");
        vm.expectRevert(bytes(MISSING_KEY));
        dp.run();
        vm.expectRevert(bytes(MISSING_KEY));
        dp.runSandbox();
        tearDown();
    }
}

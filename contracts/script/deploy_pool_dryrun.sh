#!/usr/bin/env bash
# make deploy-pool-dryrun (part of make check): DeployPool's operator path (the one Sepolia takes) on a LOCAL Anvil.
# No network, no operator key: Anvil's public test accounts stand in for the operator, buyer and parties [SIM].
#  - chain id 99911155 (a local stand-in, not 31337), so the script takes the non-Anvil branch: keys from env, a missing
#    key stops it before anything is sent;
#  - every file it writes is throwaway: deployments/99911155.json (removed at the end) and broadcast logs under
#    out/dryrun-broadcast (gitignored). The committed Sepolia record is never touched.
# Checks: core deployed; a covenant created; the main pool deployed, linked and opened by the buyer [SIM] under
# POLICY_V2 (ONCHAIN, paramsHash == onchainParamsHash); five libraries recorded exactly as linked (the 20 bytes at each
# link offset of both pools' code), sizes from the deployed bytecode (script/check_pool_record.py, which also refuses
# a record with swapped library names or an address without code); the judge sandbox redeployed with its pool and
# the OBS_INDEPENDENT open slot, the earlier pair kept as sandboxPrevious; the missing-key stop; and the verify-retry
# guard (script/check_resume.py: --resume only on a finished run).
set -euo pipefail
export PATH="$HOME/.foundry/bin:$PATH"
cd "$(dirname "$0")/.."
PORT="${ANVIL_PORT:-8561}"
RPC="http://127.0.0.1:$PORT"
CHAIN=99911155
REC="$PWD/deployments/$CHAIN.json"
export FOUNDRY_BROADCAST="out/dryrun-broadcast"
if curl -s -o /dev/null "$RPC"; then
  echo "port $PORT is busy; stop the other node or set ANVIL_PORT" >&2
  exit 1
fi
anvil --port "$PORT" --chain-id "$CHAIN" --silent >/dev/null 2>&1 &
APID=$!
cleanup() { kill "$APID" 2>/dev/null || true; rm -f "$REC"; }
trap cleanup EXIT
for _ in $(seq 50); do curl -s -o /dev/null "$RPC" && break; sleep 0.1; done

M="test test test test test test test test test test test junk"
key() { cast wallet private-key --mnemonic "$M" --mnemonic-index "$1"; }
addr() { cast wallet address --private-key "$(key "$1")"; }
DEPLOYER_PK=$(key 0)
BUYER_KEY=$(key 2)
export SELLER_ADDR=$(addr 1) BUYER_ADDR=$(addr 2) OBSERVER_A_ADDR=$(addr 3) OBSERVER_B_ADDR=$(addr 4) OBSERVER_C_ADDR=$(addr 5)

echo "== missing key: the script stops before sending anything"
if PRIVATE_KEY_DEPLOYER="" forge script script/DeployPool.s.sol --rpc-url "$RPC" >out/dryrun-missing.log 2>&1; then
  echo "   FAIL  DeployPool ran without PRIVATE_KEY_DEPLOYER"; exit 1
fi
grep -q "PRIVATE_KEY_DEPLOYER is not set" out/dryrun-missing.log || { cat out/dryrun-missing.log; exit 1; }
echo "   ok    stopped: PRIVATE_KEY_DEPLOYER is not set; nothing was sent"

echo "== core (Deploy.s.sol, operator path) + one covenant [SIM]"
PRIVATE_KEY_DEPLOYER="$DEPLOYER_PK" forge script script/Deploy.s.sol --rpc-url "$RPC" --broadcast -q >/dev/null
COV=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['methaneCovenant'])" "$REC")
ROOT=$(cast keccak "$(cast keccak "$(cast abi-encode 'f(bytes32)' "$(cast keccak TKM_S_276)")")")
cast send "$COV" "createCovenant((address,bytes32,bytes32,uint64,uint64,uint8,uint64,uint64,uint64,uint32,uint128[3]))" \
  "($BUYER_ADDR,$ROOT,$(cast keccak 'policy [SIM dry run]'),31536000,0,2,86400,172800,2592000,100,[3000000000000000000,2000000000000000000,1000000000000000000])" \
  --private-key "$(key 1)" --value 1ether --rpc-url "$RPC" >/dev/null
echo "   ok    core deployed, covenant 1 created"

echo "== main pool, opened by the buyer [SIM]"
PRIVATE_KEY_DEPLOYER="$DEPLOYER_PK" BUYER_PK="$BUYER_KEY" \
  POOL_COVENANT_ID=0x0000000000000000000000000000000000000000000000000000000000000001 \
  forge script script/DeployPool.s.sol --rpc-url "$RPC" --broadcast >out/dryrun-main.log 2>&1 || { cat out/dryrun-main.log; exit 1; }
echo "== sandbox redeploy with its pool and OBS_INDEPENDENT"
PRIVATE_KEY_DEPLOYER="$DEPLOYER_PK" \
  forge script script/DeployPool.s.sol --sig "runSandbox()" --rpc-url "$RPC" --broadcast >out/dryrun-sandbox.log 2>&1 \
  || { cat out/dryrun-sandbox.log; exit 1; }

echo "== the verify-retry guard: --resume only on a finished run"
RUN="$FOUNDRY_BROADCAST/DeployPool.s.sol/$CHAIN/run-latest.json"
python3 -I script/check_resume.py "$RUN" >/dev/null
echo "   ok    the finished main-pool run passes script/check_resume.py"
python3 -I - "$RUN" "$PWD/out/dryrun-pending-run.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); r["pending"] = [r["transactions"][-1]["hash"]]; r["receipts"] = r["receipts"][:-1]
json.dump(r, open(sys.argv[2], "w"))
PY
if python3 -I script/check_resume.py "$PWD/out/dryrun-pending-run.json" >/dev/null 2>&1; then
  echo "   FAIL  a run with a pending transaction passed the --resume guard"; exit 1
fi
echo "   ok    a run with a pending transaction and a missing receipt: --resume refused"

echo "== the record against the chain (the operator targets' post-step)"
python3 -I script/check_pool_record.py "$REC" "$RPC"

echo "== the post-step refuses a record the chain does not back"
BAD="$PWD/out/dryrun-bad-record.json"
python3 -I - "$REC" "$BAD" swap <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); libs = r["incentivePoolLibraries"]
libs["PoolVerify"], libs["PoolSetup"] = libs["PoolSetup"], libs["PoolVerify"]
json.dump(r, open(sys.argv[2], "w"))
PY
if python3 -I script/check_pool_record.py "$BAD" "$RPC" >out/dryrun-bad.log 2>&1; then
  echo "   FAIL  a record with two library names swapped passed"; exit 1
fi
echo "   ok    two library names swapped: refused"
python3 -I - "$REC" "$BAD" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); r["sandbox"]["incentivePool"] = "0x" + "11" * 20  # never deployed (a failed broadcast)
json.dump(r, open(sys.argv[2], "w"))
PY
if python3 -I script/check_pool_record.py "$BAD" "$RPC" >out/dryrun-bad.log 2>&1; then
  echo "   FAIL  a record naming an address without code passed"; exit 1
fi
echo "   ok    a recorded pool with no code on chain: refused"

python3 - "$REC" "$RPC" ../pipeline/tests/vectors/policy_v2.json <<'PY'
import json, subprocess, sys
rec, rpc, vec = json.load(open(sys.argv[1])), sys.argv[2], json.load(open(sys.argv[3]))
def cast(*a):
    return subprocess.run(["cast", *a, "--rpc-url", rpc], capture_output=True, text=True, check=True).stdout.strip()
def size(a):
    return int(cast("codesize", a))
def check(cond, msg):
    if not cond:
        print("   FAIL  " + msg); sys.exit(1)
    print("   ok    " + msg)
covid = "0x" + "00" * 31 + "01"
pool_word = cast("call", rec["incentivePool"], "getPool(bytes32)", covid)[2:]
words = [pool_word[i:i + 64] for i in range(0, len(pool_word), 64)]
check(int(words[0], 16) == 1 and int(words[1], 16) == 0, "main pool OPEN, mode ONCHAIN")
check("0x" + words[3] == vec["onchainParamsHash"], "paramsHash == POLICY_V2.onchainParamsHash")
check("0x" + words[2] == vec["policyHash"], "policyHash == POLICY_V2 policyHash")
check(cast("call", rec["sandbox"]["incentivePool"], "covenant()(address)") == rec["sandbox"]["methaneCovenant"],
      "sandbox pool reads the new sandbox covenant")
ctrl = cast("call", rec["sandbox"]["keyRegistry"], "orgController(bytes32)(address)",
            "0x" + b"OBS_INDEPENDENT".hex().ljust(64, "0"))
check(ctrl == rec["sandbox"]["independentController"], "OBS_INDEPENDENT open slot: published controller set")
prev = rec["sandboxPrevious"]["keyRegistry"]
check(prev != rec["sandbox"]["keyRegistry"] and size(prev) > 0, "the earlier sandbox pair stays on chain as sandboxPrevious")
PY
echo "DEPLOY-POOL DRY RUN OK (local chain $CHAIN; nothing sent to any network)"

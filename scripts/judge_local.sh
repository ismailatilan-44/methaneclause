#!/usr/bin/env bash
# make judge-local: the judge walk-through (scripts/judge.sh) end to end on a local Anvil, no test ETH needed.
# Same code path as the public testnet; the judge wallet is Anvil account 7, observers read the committed snapshot.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"
PORT="${ANVIL_PORT:-8547}"
RPC="http://127.0.0.1:$PORT"
if curl -s -o /dev/null "$RPC"; then
  echo "port $PORT is busy; stop the other node or set ANVIL_PORT" >&2
  exit 1
fi
mkdir -p out/judge
anvil --port "$PORT" --silent >out/judge/anvil.log 2>&1 &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
for _ in $(seq 50); do curl -s -o /dev/null "$RPC" && break; sleep 0.1; done
(cd contracts && forge build -q && forge script script/Deploy.s.sol --rpc-url "$RPC" --broadcast --slow -q \
  >../out/judge/deploy.log 2>&1) || { cat out/judge/deploy.log; exit 1; }
rm -f out/judge/31337.json out/judge/31337.txlog.jsonl out/judge/run.json out/judge/pool-31337.json
# the sandbox's refund pool (Sepolia gets it with the sandbox redeploy): IncentivePool + its linked libraries
(cd pipeline && RPC_URL="$RPC" .venv/bin/python - <<'PY'
import json, os
from common import ROOT
from observer.chain import Chain
from observer.demo import deploy_pool
from observer.service import anvil_key
addrs = deploy_pool(Chain(os.environ["RPC_URL"], sandbox=True), anvil_key(0))
(ROOT / "out" / "judge" / "pool-31337.json").write_text(json.dumps(addrs, indent=1, sort_keys=True) + "\n")
print("   ok    sandbox IncentivePool " + addrs["IncentivePool"] + " (+ " + str(len(addrs) - 1) + " linked libraries)")
PY
)
for c in attest propose tamper challenge finalize; do
  echo; echo "######## make judge-$c"
  (cd pipeline && RPC_URL="$RPC" LIVE="${LIVE:-0}" .venv/bin/python -m observer.judge "$c")
done

# judge-cure exactly as on Sepolia: nothing skipped inside the command, it exits at each window and resumes; between
# runs this script moves the Anvil clock forward [SIM]. The first pool funding is swept from the published BUYER key
# (simulated) to test that message; the next run resumes and succeeds.
echo; echo "######## make judge-cure (exit-and-resume, as on a public chain)"
advance() {  # forward-only clock move on this Anvil, labelled [SIM]
  (cd pipeline && RPC_URL="$RPC" .venv/bin/python -c "
import os
from observer.chain import Chain
from observer.demo import advance_clock
ch = Chain(os.environ['RPC_URL'], sandbox=True)
advance_clock(ch, ch.now() + $1, '$2')")
}
cure() {  # returns the cure command's own exit status (not tee's)
  set +e
  (cd pipeline && RPC_URL="$RPC" LIVE=0 JUDGE_NO_SKIP=1 JUDGE_SIMULATE_SWEEP="${1:-0}" .venv/bin/python -m observer.judge cure) >out/judge/cure.out 2>&1
  local rc=$?
  set -e
  cat out/judge/cure.out
  return $rc
}
cure 0; grep -q "challenge window closes" out/judge/cure.out || { echo "   FAIL  cure did not stop at the challenge window"; exit 1; }
echo "   ok    cure stopped at the challenge window (as on Sepolia)"; advance 601 "challenge window"
rc=0; cure 1 || rc=$?
if [ "$rc" != 3 ] || ! grep -q "stopped as expected \[SIM sweep\]" out/judge/cure.out; then
  echo "   FAIL  a swept BUYER transfer [SIM sweep] should stop the cure with exit 3 and its message"; exit 1
fi
echo "   ok    a swept transfer to the published BUYER key [SIM sweep] stops the cure with a clear message"
cure 0; grep -q "clean window closes" out/judge/cure.out || { echo "   FAIL  cure did not stop at the clean window"; exit 1; }
echo "   ok    cure resumed: pool opened, claim, pass attested; stopped at the clean window"; advance 601 "clean window"
cure 0; grep -q "The cure arc ran with your key" out/judge/cure.out || { echo "   FAIL  cure did not finish"; exit 1; }
echo "   ok    cure finished on the third resume: 1 qualified clean pass counted"

echo; echo "######## griefing: someone holding the published key revokes SANDBOX_OBS_1, then the operator re-keys"
(cd pipeline && RPC_URL="$RPC" .venv/bin/python - <<'PY'
import os
from observer import sandbox
from observer.chain import Chain
from observer.service import anvil_key
ch = Chain(os.environ["RPC_URL"], sandbox=True)
ch.transfer(anvil_key(9), sandbox.address("OBS_1"), 10**17)  # the griefer funds the published key's gas
rcpt, _ = ch.send(ch.reg.functions.revokeKey(sandbox.address("OBS_1")), sandbox.key("OBS_1"))
assert rcpt.status == 1 and sandbox.current(ch.reg, "OBS_1") is None
print("   ok    OBS_1 generation 1 revoked by its own (published) key")
PY
)
(cd pipeline && RPC_URL="$RPC" .venv/bin/python -m observer.judge reset >/dev/null)
if (cd pipeline && RPC_URL="$RPC" .venv/bin/python -m observer.judge attest >out.tmp 2>&1); then
  echo "   FAIL  attest should refuse while every OBS_1 key is revoked"; exit 1
fi
grep -q "make sandbox-rekey ROLE=OBS_1" pipeline/out.tmp || { cat pipeline/out.tmp; echo "   FAIL  no re-key instruction"; exit 1; }
echo "   ok    attest refuses with the re-key instruction"
rm -f pipeline/out.tmp
DEPLOYER_PK=$(cd pipeline && .venv/bin/python -c "from observer.service import anvil_key; print(anvil_key(0))")
(cd pipeline && PRIVATE_KEY_DEPLOYER="$DEPLOYER_PK" RPC_URL="$RPC" .venv/bin/python -m observer.testnet sandbox-rekey --role OBS_1)
for c in attest propose; do
  (cd pipeline && RPC_URL="$RPC" LIVE=0 .venv/bin/python -m observer.judge "$c" | grep -E "ok|FAIL")
done
python3 -c "import json; print('   skippedSeconds [SIM clock]:', json.load(open('out/judge/run.json'))['skippedSeconds'])"
echo; echo "JUDGE LOCAL OK (incl. re-key after griefing)"

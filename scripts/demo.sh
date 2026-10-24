#!/usr/bin/env bash
# make demo: local Anvil -> deploy -> covenant -> two observers -> TKM_S_276 episode -> rejections -> rebate.
# The chain clock is pinned [SIM]: genesis at DEMO_TS (default 1792000000 = 2026-10-14) and every block one second
# after the previous one (anvil_setBlockTimestampInterval), so nonces, expiries, signatures and gas are the same on
# every run and `make app` after `make demo` reproduces the committed app/data/app.json. The covenant start is
# backdated to 2026-01-01 [SIM]; the contract records createdAt. Chain time only moves forward, through
# pipeline/observer/demo.py advance_clock, which prints "[SIM: chain clock advanced ...]" and records skippedSeconds in
# out/demo/run.json. REALTIME=1: Anvil on the wall clock and every window is waited out in real time (no skips);
# it refuses to start once the wall clock is past the 2026-01-01 start + MethaneCovenant.MAX_LOOKBACK (the date is
# printed by observer.demo.realtime_refusal). The DZA replay runs first, on ANVIL_PORT+1 (out/demo/replay.json).
# LIVE=1: observers fetch Carbon Mapper now (then hashes, not timestamps, are what stays equal).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"
PORT="${ANVIL_PORT:-8545}"
RPC="http://127.0.0.1:$PORT"

if curl -s -o /dev/null "$RPC"; then
  echo "port $PORT is busy; stop the other node or set ANVIL_PORT" >&2
  exit 1
fi
mkdir -p out/demo
if [ "${REALTIME:-0}" = "1" ]; then  # the wall clock must stay inside the contract's lookback from the v1 start
  MSG=$(cd pipeline && .venv/bin/python -c "import time; from observer.demo import realtime_refusal as r; print(r(int(time.time())) or '')")
  if [ -n "$MSG" ]; then echo "$MSG" >&2; exit 1; fi
fi
eval "$(cd pipeline && .venv/bin/python -m observer.pq env)"  # public test seeds -> pqPubKeyHash (algId 2)

start_anvil() {  # $1 port, $2 genesis timestamp (empty = wall clock), $3 log name
  local clock=""
  if [ -n "$2" ]; then clock="--timestamp $2"; fi
  # shellcheck disable=SC2086
  anvil --port "$1" $clock --silent >"out/demo/$3.log" 2>&1 &
  echo $!
}
wait_rpc() { for _ in $(seq 50); do curl -s -o /dev/null "$1" && return 0; sleep 0.1; done; return 1; }
deploy_core() {  # $1 rpc, $2 log name
  (cd contracts && forge script script/Deploy.s.sol --rpc-url "$1" --broadcast --slow -q >"../out/demo/$2" 2>&1) \
    || { cat "out/demo/$2"; exit 1; }
}

# ---- 1. DZA replay first (spec-v2 §10), on its own Anvil whose genesis is the replay covenant's start [SIM clock]
if [ "${REALTIME:-0}" != "1" ]; then
  RPORT=$((PORT + 1)); RRPC="http://127.0.0.1:$RPORT"
  if curl -s -o /dev/null "$RRPC"; then echo "port $RPORT is busy (the replay uses ANVIL_PORT+1)" >&2; exit 1; fi
  REPLAY_TS=$(cd pipeline && .venv/bin/python -c "from policy import REPLAY_START; print(REPLAY_START)")
  REPLAY_PID=$(start_anvil "$RPORT" "$REPLAY_TS" anvil-replay)
  trap 'kill $REPLAY_PID 2>/dev/null || true' EXIT
  wait_rpc "$RRPC"
  cast rpc anvil_setBlockTimestampInterval 1 --rpc-url "$RRPC" >/dev/null
  echo "== DZA replay chain: Anvil genesis $REPLAY_TS [SIM clock]; deploy the core (Deploy.s.sol)"
  deploy_core "$RRPC" deploy-replay.log
  (cd pipeline && DEMO_CLOCK_PINNED=1 RPC_URL="$RRPC" .venv/bin/python -m observer.demo replay)
  kill "$REPLAY_PID" 2>/dev/null || true
else
  echo "== DZA replay skipped: it needs the pinned 2024-09-01 clock (REALTIME=1 runs v1 only)"
fi

# ---- 2. v1's walking skeleton, unchanged, on the pinned 2026-10-14 clock [SIM]
CLOCK_TS=""
if [ "${REALTIME:-0}" != "1" ]; then CLOCK_TS="${DEMO_TS:-1792000000}"; export DEMO_CLOCK_PINNED=1; fi
ANVIL_PID=$(start_anvil "$PORT" "$CLOCK_TS" anvil)
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
wait_rpc "$RPC"
if [ -n "${DEMO_CLOCK_PINNED:-}" ]; then  # each block = previous + 1 s, independent of how fast this machine is
  cast rpc anvil_setBlockTimestampInterval 1 --rpc-url "$RPC" >/dev/null
fi

echo "== Deploy (KeyRegistry + MethaneCovenant, keys for seller/buyer [SIM] and OBS_A/B/C as ECDSA + ML-DSA-44, admin renounced)"
deploy_core "$RPC" deploy.log

cd pipeline && RPC_URL="$RPC" .venv/bin/python -m observer.demo
# ---- 3. TKM verdict-only covenant + credit-note export, on the same chain after v1's checks
RPC_URL="$RPC" .venv/bin/python -m observer.demo verdict
# ---- 4. portfolio covenant over portfolio set v3, same chain
RPC_URL="$RPC" .venv/bin/python -m observer.demo portfolio

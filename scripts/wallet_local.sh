#!/usr/bin/env bash
# make wallet-local: app/wallet.js (the browser-wallet back end) end to end on a local Anvil judge sandbox.
# Deploys the core + the judge sandbox [SIM] (contracts/script/Deploy.s.sol) and the sandbox refund pool, then runs
# scripts/tests/wallet.test.mjs with an injected EIP-1193 provider backed by Anvil test accounts 8 and 9.
# Offline once `make wallet-setup` has installed viem + @metamask/eth-sig-util into out/e2e-tools (not committed).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"
PORT="${WALLET_PORT:-${ANVIL_PORT:-8548}}"
RPC="http://127.0.0.1:$PORT"
if curl -s -o /dev/null "$RPC"; then
  echo "port $PORT is busy; stop the other node or set WALLET_PORT" >&2
  exit 1
fi
mkdir -p out/wallet
anvil --port "$PORT" --silent >out/wallet/anvil.log 2>&1 &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
for _ in $(seq 50); do curl -s -o /dev/null "$RPC" && break; sleep 0.1; done
(cd contracts && forge build -q && forge script script/Deploy.s.sol --rpc-url "$RPC" --broadcast --slow -q \
  >../out/wallet/deploy.log 2>&1) || { cat out/wallet/deploy.log; exit 1; }
(cd pipeline && RPC_URL="$RPC" .venv/bin/python - <<'PY'
import json, os
from common import ROOT
from observer.chain import Chain
from observer.demo import deploy_pool
from observer.service import anvil_key
ch = Chain(os.environ["RPC_URL"], sandbox=True)
addrs = deploy_pool(ch, anvil_key(0))
net = {k: ch.dep[k] for k in ("keyRegistry", "methaneCovenant", "proposerBond", "challengerBond")}
net["incentivePool"] = addrs["IncentivePool"]
(ROOT / "out" / "wallet" / "network-31337.json").write_text(json.dumps(net, indent=1, sort_keys=True) + "\n")
print("   ok    local judge sandbox: registry " + net["keyRegistry"] + ", covenant " + net["methaneCovenant"]
      + ", pool " + net["incentivePool"])
PY
)
RPC_URL="$RPC" node scripts/tests/wallet.test.mjs

#!/usr/bin/env bash
# Judge walk-through on Ethereum Sepolia, in the judge sandbox [SIM] (separate registry, published keys).
#   export JUDGE_PK=0x...      a fresh testnet wallet (cast wallet new) with ~0.001 test ETH from a faucet
#   scripts/judge.sh attest [SOURCE] | propose | tamper | challenge | finalize | cure | status
# RPC_URL defaults to a public Sepolia endpoint. No test ETH? `make judge-local` runs the same code on Anvil.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"
export RPC_URL="${RPC_URL:-https://ethereum-sepolia-rpc.publicnode.com}"
[ -x pipeline/.venv/bin/python ] || { echo "run 'make setup' first" >&2; exit 1; }
[ -f contracts/out/MethaneCovenant.sol/MethaneCovenant.json ] || (cd contracts && forge build -q)
cmd="${1:?usage: scripts/judge.sh attest|propose|tamper|challenge|finalize|cure|status [SOURCE]}"
if [ "$cmd" = attest ]; then
  cd pipeline && exec .venv/bin/python -m observer.judge attest --source "${2:-TKM_S_276}"
fi
cd pipeline && exec .venv/bin/python -m observer.judge "$cmd"

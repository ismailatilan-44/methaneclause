#!/usr/bin/env bash
# make halmos-mutants (dev tool, not in make check; a few minutes): each planted fault in this directory must be
# caught by the Halmos run named for it. A mutant is applied to a throwaway copy of the contracts under
# out/mutants/<name>/ (src/ and halmos/ copied, lib/ linked); the working tree is never patched. Exit 1 if any
# mutant survives (its run passes) or cannot be applied.
set -uo pipefail
export PATH="$HOME/.foundry/bin:$PATH"
cd "$(dirname "$0")/../.." || exit 1 # contracts/
ROOT="$PWD"
OUT="$ROOT/out/mutants"
mkdir -p "$OUT"

# mutant -> the Halmos run that must find a counterexample
run_for() {
  case "$1" in
    1-relapse-keeps-pool-reserve) echo "--contract PoolPropertiesHalmos --function invariant_P1 --loop 4 --invariant-depth 2" ;;
    2-relapse-keeps-unfunded) echo "--contract PoolPropertiesHalmos --function invariant_P3 --loop 4 --invariant-depth 2" ;;
    3-share-divided-by-9999) echo "--contract InstallmentArithmeticHalmos --loop 32" ;;
    4-settle-refund-not-booked) echo "--contract CoreScenarioHalmos --function check_I4_settle --loop 4" ;;
    5-confirm-payment-not-booked) echo "--contract CoreScenarioHalmos --function check_I4_rebatePaid --loop 4 --default-bytes-lengths 65" ;;
    *) echo "" ;;
  esac
}

caught=0
survived=0
for patch in "$ROOT"/halmos/mutants/*.patch; do
  name="$(basename "$patch" .patch)"
  args="$(run_for "$name")"
  dir="$OUT/$name"
  if [ -z "$args" ]; then
    echo "   FAIL    $name: no run assigned in run.sh"
    survived=$((survived + 1))
    continue
  fi
  rm -rf "$dir" && mkdir -p "$dir"
  cp -R "$ROOT/src" "$ROOT/halmos" "$ROOT/foundry.toml" "$dir/" && ln -s "$ROOT/lib" "$dir/lib"
  if ! (cd "$dir" && patch -s -p1 < "$patch"); then
    echo "   FAIL    $name: the patch does not apply (the code moved; refresh the mutant)"
    survived=$((survived + 1))
    continue
  fi
  # shellcheck disable=SC2086
  (cd "$dir" && FOUNDRY_DYNAMIC_TEST_LINKING=false FOUNDRY_TEST=halmos FOUNDRY_OUT=out FOUNDRY_CACHE_PATH=cache \
    halmos --forge-build-out out $args --early-exit) > "$dir/halmos.log" 2>&1
  plain="$(sed -E $'s/\x1b\\[[0-9;]*m//g' "$dir/halmos.log")"
  if grep -q '^\[FAIL\]' <<< "$plain"; then
    echo "   ok      $name: caught by $(grep -m1 -oE '^\[FAIL\] [a-zA-Z0-9_]+' <<< "$plain" | cut -d' ' -f2)"
    caught=$((caught + 1))
  else
    echo "   FAIL    $name: survived (log: out/mutants/$name/halmos.log)"
    survived=$((survived + 1))
  fi
done
echo "halmos mutants: $caught caught, $survived survived"
[ "$survived" -eq 0 ]

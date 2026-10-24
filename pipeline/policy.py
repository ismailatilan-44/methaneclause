"""The demo covenant's policy, shared by bond sizing (model/bond.py) and the demo (observer/demo.py).

Committed before any evidence as SHA-256 of its RFC 8785 form (`policyHash`, spec §3). Amounts are testnet-sized so
the same numbers work on Base Sepolia.
"""
from __future__ import annotations

DAY = 86_400
ETH = 10**18
COVENANT_START = 1767225600  # 2026-01-01T00:00:00Z, [SIM] backdated start (contract: <= MAX_LOOKBACK = 365 days)
COVENANT_START_DATE = "2026-01-01"

TIER_MIN_KGH = [5000, 1500, 100]  # IEA response levels; index 0 = tier 1
TIER_AMOUNTS_WEI = [5 * ETH // 1000, 3 * ETH // 1000, 1 * ETH // 1000]  # 0.005 / 0.003 / 0.001 ETH

POLICY = {
    "start": COVENANT_START,
    "k": 2, "n": 3,
    "challengeWindow": 1 * DAY, "contestWindow": 2 * DAY, "linkWindow": 30 * DAY, "term": 365 * DAY,
    "minRateKgH": TIER_MIN_KGH[-1],
    "rebateSchedule": {"tierMinKgH": TIER_MIN_KGH, "tierAmountsWei": [str(x) for x in TIER_AMOUNTS_WEI]},
    "gwp100": 29.8,
}


def tier_of(kgh: float) -> int | None:
    """1, 2, 3, or None below the covenant's minimum rate (no episode)."""
    for i, lo in enumerate(TIER_MIN_KGH):
        if kgh >= lo:
            return i + 1
    return None

# Judge sandbox [SIM] (scripts/judge.sh): same rule, amounts / 100 so a judge's faucet drip covers every role, and
# 10-minute windows so a judge can watch an episode confirm. Published keys; see pipeline/observer/sandbox.py.
SANDBOX_SCALE = 100
SANDBOX_TIER_AMOUNTS_WEI = [x // SANDBOX_SCALE for x in TIER_AMOUNTS_WEI]
SANDBOX_POLICY = {
    **POLICY,
    "challengeWindow": 600, "contestWindow": 600,
    "rebateSchedule": {"tierMinKgH": TIER_MIN_KGH, "tierAmountsWei": [str(x) for x in SANDBOX_TIER_AMOUNTS_WEI]},
    "sandbox": "judge sandbox [SIM]: published keys, amounts / 100",
}


# ---------------------------------------------------------------------------------------------------------------------
# v2 (spec-v2 §4): the IncentivePool policy. The core part is v1's POLICY byte for byte (its hash does not change); the
# pool part fixes the clean-pass rule and the on-chain PoolParams. `onchainParamsHash` = keccak256(abi.encode(PoolParams))
# in the exact struct layout of contracts/src/IncentivePool.sol (tests/test_policy_v2.py parses the struct, so a contract
# edit that changes the layout fails the test). Hashing: policy_hash = SHA-256 of RFC 8785 (as v1).
# GWP-20 is deliberately NOT in any hashed policy (a later citation correction must not change a pool's hash).
from cleanruns import UPPER_BOUND_BP  # noqa: E402  (u(1..32) in bp, written by `make cleanruns` too)

POOL_PARAMS_FIELDS = ["publicationLag", "cleanWindow", "resolveWindow", "holdWindow", "cureTerm", "passBond",
                      "challengerBond", "observerFeeWei", "unitTierAmounts", "installments", "upperBoundBp"]
POOL_PARAMS_ABI = ("(uint32,uint32,uint32,uint32,uint32,uint96,uint96,uint96,uint128[3],"
                   "(uint8,uint16,bool,bool)[3],uint16[32])")

REPLAY_START = 1725148800  # 2024-09-01T00:00:00Z: DZA replay covenant start [SIM] (Anvil only)
REPLAY_START_DATE = "2024-09-01"

# Pool deposits mirror the core deployment's bonds (Deploy.s.sol: proposer 0.0001 ETH, challenger 0.0002 ETH);
# the observer fee is a [SIM] amount, a tenth of the pass bond.
POOL_V2 = {
    "settlementMode": "ONCHAIN",
    "publicationLag": 60 * DAY, "cleanWindow": 1 * DAY, "resolveWindow": 7 * DAY,
    "holdWindow": 64 * DAY,  # = publicationLag + challengeWindow + contestWindow + cleanWindow (>= the 63-d fund rule)
    "cureTerm": 540 * DAY,
    "passBondWei": str(ETH // 10_000), "challengerBondWei": str(2 * ETH // 10_000),
    "observerFeeWei": str(ETH // 100_000),  # [SIM]
    "unitTierAmounts": [500, 300, 100],  # policy units x100 per core tier (VERDICT_ONLY cap; spec-v2 §15 2026-10-16:
    #                                      every 30/30/40 share is a whole unit); a parameter, not a price
    "installments": [[3, 3000, False, False], [6, 3000, False, False], [10, 4000, True, True]],  # [D4]
    "upperBoundBp": list(UPPER_BOUND_BP),
    # 90 % probability-of-detection floors, conservative end of each published range (spec-v2 §2, §15 2026-10-16);
    # citations and their status: pipeline/floors.py -> data/derived/floors.json
    "instrumentFloorKgH": {"tan": 180, "emi": 700},
    "maxCloudBp": 2500, "cloudRule": "not_cloudy",  # [D3]
    "marsWindowDays": 7, "euSuperEmitterKgH": 100,
}


def pool_params(policy: dict) -> tuple:
    """PoolParams tuple in Solidity field order (POOL_PARAMS_FIELDS)."""
    p = policy["pool"]
    return (p["publicationLag"], p["cleanWindow"], p["resolveWindow"], p["holdWindow"], p["cureTerm"],
            int(p["passBondWei"]), int(p["challengerBondWei"]), int(p["observerFeeWei"]),
            list(p["unitTierAmounts"]), [tuple(r) for r in p["installments"]], list(p["upperBoundBp"]))


def onchain_params_hash(policy: dict) -> str:
    from eth_abi import encode
    from eth_utils import keccak

    return "0x" + keccak(encode([POOL_PARAMS_ABI], [pool_params(policy)])).hex()


def policy_hash(policy: dict) -> str:
    from canon import evidence_hash

    return evidence_hash(policy)


def _v2(core: dict, pool: dict) -> dict:
    pol = {"v": 2, "core": core, "pool": pool}
    pol["onchainParamsHash"] = onchain_params_hash(pol)
    return pol


POLICY_V2 = _v2(POLICY, POOL_V2)
POLICY_V2_VERDICT_ONLY = _v2(POLICY, {**POOL_V2, "settlementMode": "VERDICT_ONLY"})
POLICY_V2_REPLAY = _v2({**POLICY, "start": REPLAY_START, "term": 730 * DAY}, POOL_V2)  # ends 2026-09-01
# A multi-year supply contract's Term (spec-v2 §15 2026-10-21): the final installment needs 573 days between the episode
# and the Term's end (pipeline/final_reach.py), so a one-year Term never schedules it. Three contract years is the
# shortest whole-year Term in which every episode of the first year can still reach it (365 + 573 <= 1095); only
# `core.term` differs from POLICY, so the pool part and its onchainParamsHash are POLICY_V2's.
MULTIYEAR_TERM = 3 * 365 * DAY
POLICY_MULTIYEAR = {**POLICY, "term": MULTIYEAR_TERM}
POLICY_V2_MULTIYEAR = _v2(POLICY_MULTIYEAR, POOL_V2)
POLICY_V2_SANDBOX = _v2(SANDBOX_POLICY, {
    **POOL_V2,
    "publicationLag": 600, "cleanWindow": 600, "resolveWindow": 600,
    "holdWindow": 1800,  # = 600 + 600 + 600, the fund rule with the sandbox's 600 s challenge / contest windows
    "cureTerm": 1 * DAY,
    "passBondWei": str(int(POOL_V2["passBondWei"]) // SANDBOX_SCALE),
    "challengerBondWei": str(int(POOL_V2["challengerBondWei"]) // SANDBOX_SCALE),
    "observerFeeWei": str(int(POOL_V2["observerFeeWei"]) // SANDBOX_SCALE),
    "sandbox": "judge sandbox [SIM]: published keys, wei amounts / 100, 10-minute windows",
})


VECTOR = __import__("common").ROOT / "pipeline" / "tests" / "vectors" / "policy_v2.json"


def parity_vector(policy: dict) -> dict:
    """Shared vector (spec-v2 §15 2026-10-16 (9)): lane A's forge test rebuilds PoolParams from these fields in layout
    order and asserts keccak256(abi.encode(p)) == onchainParamsHash; pytest asserts the file matches POLICY_V2."""
    from eth_abi import encode

    t = pool_params(policy)
    vals = dict(zip(POOL_PARAMS_FIELDS, t))
    vals["installments"] = [{"afterPasses": a, "shareBp": b, "twoInstruments": c, "isFinal": d} for a, b, c, d in t[9]]
    for k in ("passBond", "challengerBond", "observerFeeWei"):
        vals[k] = str(vals[k])  # uint96: decimal strings, so no JSON reader rounds them
    vals["unitTierAmounts"] = [str(x) for x in t[8]]
    return {"schema": "methaneclause.policy_v2_vector/1", "policy": "POLICY_V2", "abiType": POOL_PARAMS_ABI,
            "fields": [{"name": n, "value": vals[n]} for n in POOL_PARAMS_FIELDS],
            "abiEncoded": "0x" + encode([POOL_PARAMS_ABI], [t]).hex(),
            "onchainParamsHash": policy["onchainParamsHash"], "policyHash": policy_hash(policy)}


MULTIYEAR_VECTOR = VECTOR.with_name("policy_v2_multiyear.json")


def multiyear_vector() -> dict:
    """What contracts/test/unit/MultiYearTerm.t.sol reads: POLICY_V2_MULTIYEAR's term and the windows that decide when
    the final installment can be scheduled, with final_reach's answer for the boundary day."""
    from final_reach import final_reach

    pol = POLICY_V2_MULTIYEAR
    core, pool = pol["core"], pol["pool"]
    return {"schema": "methaneclause.policy_v2_multiyear_vector/1", "policy": "POLICY_V2_MULTIYEAR",
            "term": core["term"], "linkWindow": core["linkWindow"], "challengeWindow": core["challengeWindow"],
            "contestWindow": core["contestWindow"], "publicationLag": pool["publicationLag"],
            "cleanWindow": pool["cleanWindow"], "holdWindow": pool["holdWindow"], "cureTerm": pool["cureTerm"],
            "finalReach": final_reach(pol), "onchainParamsHash": pol["onchainParamsHash"],
            "policyHash": policy_hash(pol)}


def main() -> None:
    import sys

    from common import DERIVED, write_json

    out = {"schema": "methaneclause.policy_v2/1", "poolParamsAbi": POOL_PARAMS_ABI, "fields": POOL_PARAMS_FIELDS,
           "policies": {}}
    for name, pol in (("POLICY_V2", POLICY_V2), ("POLICY_V2_VERDICT_ONLY", POLICY_V2_VERDICT_ONLY),
                      ("POLICY_V2_REPLAY", POLICY_V2_REPLAY), ("POLICY_V2_SANDBOX", POLICY_V2_SANDBOX),
                      ("POLICY_V2_MULTIYEAR", POLICY_V2_MULTIYEAR)):
        out["policies"][name] = {"policyHash": policy_hash(pol), "onchainParamsHash": pol["onchainParamsHash"],
                                 "settlementMode": pol["pool"]["settlementMode"], "policy": pol}
        print(f"{name:24s} policyHash {policy_hash(pol)}  onchainParamsHash {pol['onchainParamsHash']}")
    if "--write" in sys.argv:
        write_json(DERIVED / "policy_v2.json", out)
        write_json(VECTOR, parity_vector(POLICY_V2))
        write_json(MULTIYEAR_VECTOR, multiyear_vector())


if __name__ == "__main__":
    main()

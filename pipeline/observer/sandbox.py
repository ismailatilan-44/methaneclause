"""Judge sandbox keys [SIM]: published, testnet-only, identical to `sandboxKey` in contracts/script/Deploy.s.sol.

The sandbox is a separate KeyRegistry + MethaneCovenant pair. Its seller, buyer and two observer keys are derived
from public strings so a judge can play every role from a fresh wallet. They protect nothing: anyone can sign with
them. That is why they live in their own registry; the main deployment's observer keys stay private.

Anyone holding a published key can also revoke it (a key may revoke itself). The registry never re-registers an
address, so recovery is a new *generation*: generation 1 is the role string itself ("OBS_1"), generation n > 1 is
"OBS_1#n". The deployer, controller of every sandbox org, registers the next generation (`make sandbox-rekey`), and
the judge scripts use the first generation the registry reports valid (`current`).
"""
from __future__ import annotations

from eth_account import Account
from web3 import Web3

PREFIX = "MethaneClause judge sandbox v1 [SIM] testnet-only / "
ROLES = ("SELLER", "BUYER", "OBS_1", "OBS_2")  # registered keys (generation-aware)
JUDGE_CONTROLLER = "JUDGE_CONTROLLER"  # controller of org OBS_JUDGE: any judge registers their own wallet with it
JUDGE_ORG = "OBS_JUDGE"
ORGS = {"SELLER": "SANDBOX_SELLER", "BUYER": "SANDBOX_BUYER", "OBS_1": "SANDBOX_OBS_1", "OBS_2": "SANDBOX_OBS_2"}
REGISTRY_ROLE = {"SELLER": 2, "BUYER": 3, "OBS_1": 1, "OBS_2": 1}  # KeyRegistry.Role
DEPLOYMENT_FIELD = {"SELLER": "seller", "BUYER": "buyer", "OBS_1": "observer1", "OBS_2": "observer2"}
MAX_GENERATION = 20


def key(role: str, generation: int = 1) -> str:
    if role not in ROLES and role != JUDGE_CONTROLLER:
        raise ValueError(f"unknown sandbox role {role}")
    label = role if generation == 1 else f"{role}#{generation}"
    return "0x" + Web3.keccak(text=PREFIX + label).hex().removeprefix("0x")


def address(role: str, generation: int = 1) -> str:
    return Account.from_key(key(role, generation)).address


def current(registry, role: str) -> int | None:
    """First generation whose key the sandbox registry reports valid now; None if every generation is revoked."""
    want = REGISTRY_ROLE[role]
    for g in range(1, MAX_GENERATION + 1):
        org, r, valid = registry.functions.keyStatus(address(role, g), 2**63).call()
        if r == 0:  # never registered: no later generation exists either
            return None
        if valid and r == want:
            return g
    return None

"""Observer service: one instance per observer org (OBS_A, OBS_B, optional OBS_C), each with its own key.

Each observer fetches the provider itself (LIVE=1: Carbon Mapper API now; default: the committed case snapshot),
builds the canonical bundle, validates it, hashes it (RFC 8785 + SHA-256) and signs an EIP-712 DETECT attestation.
It attests what it received, from whom and when; not what the satellite saw.

Keys: OBSERVER_{A,B,C}_PK from the environment. On Anvil (chainId 31337) only, the public Anvil test mnemonic
accounts 3-5 are used when no key is set; they are throwaway keys and are refused on any other chain.

CLI (prints the signed attestation as JSON). Covenant start, minimum rate, chain id, contract and "now" are read from
the chain (getCovenant + latest block), never typed by hand:
    python -m observer.service --org OBS_A --case TKM_S_276 --plume tan20260611t083132c23s4001-AB \
        --covenant 0x..01 [--rpc <url>]        # default: the RPC_URL environment variable, else local Anvil
    --sandbox signs with a published judge-sandbox key (OBS_1, OBS_2) against the sandbox deployment [SIM].
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from dataclasses import dataclass, field

from eth_account import Account

from common import DERIVED, content_sha256, http_get, now_utc, sha256_bytes
from fetch_cm import PLUME_FIELDS, SRC_URL
from observer import attest, pq, sandbox
from observer.bundle import INSTRUMENTS, build_bundle, bundle_bytes, bundle_hash, validate

ANVIL_MNEMONIC = "test test test test test test test test test test test junk"
ANVIL_INDEX = {"OBS_A": 3, "OBS_B": 4, "OBS_C": 5}
CASES = DERIVED / "cases"
SOURCESET = DERIVED / "sourceset_v2.json"


def anvil_key(index: int) -> str:
    Account.enable_unaudited_hdwallet_features()
    return Account.from_mnemonic(ANVIL_MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}").key.hex()


def observer_key(org: str, chain_id: int) -> str:
    pk = os.environ.get(f"OBSERVER_{org[-1]}_PK")
    if pk:
        return pk
    if chain_id != 31337:
        raise SystemExit(f"OBSERVER_{org[-1]}_PK not set; Anvil test keys are refused on chain {chain_id}")
    return anvil_key(ANVIL_INDEX[org])


def require_anvil_for_determinism(chain_id: int, nonce, pq_deterministic: bool) -> None:
    """Opt-in determinism (a pinned nonce, deterministic ML-DSA) is for the local demo only (a review finding)."""
    if (nonce is not None or pq_deterministic) and chain_id != 31337:
        raise ValueError(f"opt-in determinism (nonce / pq_deterministic) is refused on chain {chain_id}; Anvil only")


def load_facility(case: str) -> tuple[dict, set[str], dict]:
    """The covered facility (MARS source), its linked CM sources in the committed set, and the case file."""
    ss = json.loads(SOURCESET.read_text())
    fac = next(s for s in ss["sources"] if s["case"] == case)
    cf = json.loads((CASES / f"{case}.json").read_text())
    facility = {"sourceId": fac["sourceId"], "source_name": fac["source_name"], "proof": fac["proof"],
                "root": ss["root"], "lat": cf["mars_source"]["lat"], "lon": cf["mars_source"]["lon"]}
    return facility, set(fac["linked_cm_sources"]), cf


@dataclass
class Observer:
    org: str
    chain_id: int
    contract: str
    live: bool = False
    seen_plumes: set[str] = field(default_factory=set)
    receipts: list[dict] = field(default_factory=list)
    pk: str | None = field(default=None, repr=False)  # explicit key (judge sandbox); else OBSERVER_{A,B,C}_PK

    def __post_init__(self):
        self._pk = self.pk or observer_key(self.org, self.chain_id)
        self.pk = None
        self.address = Account.from_key(self._pk).address
        seed = pq.seed_for(self.org, self.chain_id) if self.org in pq.ORGS else None
        self._pq = pq.keypair(seed) if seed else None  # (pk, sk) or None: then the key is ECDSA-only (algId 1)

    # ---------------------------------------------------------------- fetch
    def fetch_plume(self, case_file: dict, plume_id: str) -> tuple[dict, str, dict]:
        """Return (plume row, provider content hash, receipt). Live: Carbon Mapper now. Snapshot: committed case."""
        snap = next((p for p in case_file["cm_plumes_within_radius"] if p["plume_id"] == plume_id), None)
        cm_name = snap["source_name"] if snap else None
        if not self.live:
            if snap is None:
                raise LookupError(f"{plume_id} not in the committed snapshot")
            h = case_file["cm_sources"][cm_name]["content_sha256"]
            rec = {"mode": "snapshot", "url": SRC_URL + cm_name, "retrievedAt": case_file["cm_sources"][cm_name]
                   ["fetchedAt"], "contentSha256": h, "rawSha256": case_file["cm_sources"][cm_name]["raw_sha256"]}
            return dict(snap), h, rec
        names = [cm_name] if cm_name else sorted(case_file["cm_sources"])
        for n in names:
            raw = http_get(SRC_URL + n)
            for p in json.loads(raw).get("plumes") or []:
                if p["plume_id"] != plume_id:
                    continue
                lon, lat = p["geometry_json"]["coordinates"][:2]
                row = {k: p.get(k) for k in PLUME_FIELDS}
                row.update(source_name=n, lat=lat, lon=lon)
                h = content_sha256(raw)
                rec = {"mode": "live", "url": SRC_URL + n, "retrievedAt": now_utc(), "contentSha256": h,
                       "rawSha256": sha256_bytes(raw)}
                return row, h, rec
        raise LookupError(f"{plume_id} not returned by Carbon Mapper for {names}")

    # ---------------------------------------------------------------- attest
    def attest(self, case: str, plume_id: str, *, covenant_id: str, covenant_start: int, now: int,
               min_rate_kgh: int = 100, override: dict | None = None, nonce: int | None = None,
               pq_deterministic: bool = False) -> dict:
        """Fetch -> bundle -> validate -> hash -> sign. `override` edits plume fields (for [SIM] disagreement).
        Opt-in determinism is refused off Anvil (chain 31337).
        Determinism hooks (opt-in, for a repeatable demo only): `nonce` (None = random 64-bit) and `pq_deterministic`
        (FIPS 204 deterministic ML-DSA instead of hedged). Defaults stay random for every tool and testnet path."""
        require_anvil_for_determinism(self.chain_id, nonce, pq_deterministic)
        facility, covered_cm, cf = load_facility(case)
        plume, h, rec = self.fetch_plume(cf, plume_id)
        plume.update(override or {})
        b = build_bundle(plume, covenant_id=covenant_id, source_id=facility["sourceId"],
                         source_name=facility["source_name"], content_sha256=h)
        validate(b, facility=facility, covered_cm=covered_cm, covenant_start=covenant_start, now=now,
                 min_rate_kgh=min_rate_kgh, seen_plumes=self.seen_plumes)
        ev = bundle_hash(b)
        att = {
            "covenantId": attest.b32(covenant_id),
            "sourceId": attest.b32(facility["sourceId"]),
            "evidenceHash": attest.b32(ev),
            "modelHash": bytes(32),
            "emissionKgH": b["emissionKgH"],
            "uncertaintyKgH": b["uncertaintyKgH"],
            "detectedAt": b["detectedAt"],
            "instrumentId": INSTRUMENTS[b["instrument"]],
            "providerId": attest.b32(b["providerId"]),
            "kind": attest.KIND_DETECT,
            "nonce": secrets.randbits(64) if nonce is None else nonce,
            "expiry": now + 3600,
        }
        sig = attest.sign(att, self._pk, self.chain_id, self.contract)
        self.seen_plumes.add(b["plumeId"])
        rec.update(org=self.org, signer=self.address, plumeId=plume_id, evidenceHash=ev)
        self.receipts.append(rec)
        out = {"org": self.org, "signer": self.address, "bundle": b, "evidenceHash": ev, "attestation": att,
               "signature": "0x" + sig.hex(), "proof": facility["proof"], "receipt": rec}
        if self._pq:  # hybrid: ML-DSA-44 over the same canonical bytes the evidenceHash commits to
            pk, sk = self._pq
            out["mldsa44"] = {"publicKey": "0x" + pk.hex(), "pkHash": pq.pk_hash(pk), "context": pq.CONTEXT.decode(),
                              "sig": "0x" + pq.sign(sk, bundle_bytes(b), deterministic=pq_deterministic).hex()}
        return out


def covenant_terms(chain, covenant_id: str) -> dict:
    """What an observer must check a detection against, read from the chain rather than from arguments."""
    c = chain.covenant(attest.b32(covenant_id))
    if c["seller"] == "0x" + "0" * 40:
        raise SystemExit(f"covenant {covenant_id} does not exist on {chain.network}")
    return {"covenant_start": c["start"], "min_rate_kgh": c["minRateKgH"], "now": chain.now()}


def to_json(signed: dict) -> str:
    att = {k: ("0x" + v.hex() if isinstance(v, bytes) else v) for k, v in signed["attestation"].items()}
    return json.dumps({**signed, "attestation": att}, indent=1, sort_keys=True)


def main() -> None:
    from observer.chain import Chain

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--org", required=True, choices=sorted(ANVIL_INDEX) + sorted(sandbox.ORGS))
    ap.add_argument("--case", default="TKM_S_276")
    ap.add_argument("--plume", required=True)
    ap.add_argument("--covenant", required=True, help="bytes32 hex")
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    a = ap.parse_args()
    is_sandbox = a.org in sandbox.ORGS
    ch = Chain(a.rpc, sandbox=is_sandbox)
    obs = Observer(a.org, ch.chain_id, ch.cov.address, live=os.environ.get("LIVE") == "1",
                   pk=sandbox.key(a.org) if is_sandbox else None)
    print(to_json(obs.attest(a.case, a.plume, covenant_id=a.covenant, **covenant_terms(ch, a.covenant))))


if __name__ == "__main__":
    main()

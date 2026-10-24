"""Sepolia measurements (pipeline/measure.py -> data/derived/measurements.json): rebuilt offline from tracked files only
(data/derived/sepolia_txs.json from the chain, the tracked tx log, the dated latency history); counts, gas medians and
latency blocks follow from those files; no latency sample is invented; the tracked tx log appends Sepolia rows only."""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

import measure
import readme_testnet
import txlog

ROOT = Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"


def load(name: str):
    return json.loads((DERIVED / name).read_text())


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("measure.py touched the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_rebuilds_the_committed_file_offline(tmp_path, monkeypatch, no_network):
    committed = load("measurements.json")
    out = tmp_path / "measurements.json"
    out.write_text(json.dumps(committed))
    monkeypatch.setattr(measure, "OUT", out)
    monkeypatch.setattr("sys.argv", ["measure.py", "--offline"])
    measure.main()
    rebuilt = json.loads(out.read_text())
    assert {k: v for k, v in rebuilt.items() if k != "measuredAt"} == \
        {k: v for k, v in committed.items() if k != "measuredAt"}, "measurements.json is stale: run make measure"


def test_counts_come_from_the_chain_record():
    txs = load("sepolia_txs.json")["transactions"]
    m = load("measurements.json")["transactions"]
    assert m["logged"] == len(txs) == len({t["tx"] for t in txs})
    assert m["expected_reverts"] == sum(t["status"] == 0 and measure.intended_revert(t) for t in txs)
    assert m["unexpected_failures"] == sum(t["status"] == 0 and not measure.intended_revert(t) for t in txs)
    assert m["expected_reverts"] + m["unexpected_failures"] == sum(t["status"] == 0 for t in txs)
    assert sum(m["by_kind"].values()) == m["logged"]
    assert all(d["cause"] != "no cause recorded" for d in m["unexpected_failures_detail"])


def test_the_chain_record_is_complete_per_sender():
    d = load("sepolia_txs.json")
    for s in d["senders"]:
        assert sum(t["from"] == s["address"] for t in d["transactions"]) == s["nonce"], s
    assert d["trackedHashesNotFound"] == []
    have = {t["tx"] for t in d["transactions"]}
    for st in load("testnet_run.json")["steps"]:
        assert st["tx"].rsplit("/", 1)[-1].lower() in have
    for r in txlog.read():
        assert r["tx"].lower() in have


def test_reconciles_with_the_15_october_count():
    rec = load("measurements.json")["reconciliation"]
    assert rec, "the 15 Oct block must be reconciled against the chain"
    for r in rec:
        assert r["every_logged_transaction_is_on_chain"]
        assert sum(r["not_in_then_logs_by_function"].values()) == r["not_in_then_logs"]
        assert r["chain"]["expected_reverts"] == r["then"]["expected_reverts"]
        assert r["chain"]["unexpected_failures"] == r["then"]["unexpected_failures"]


def test_revert_classification():
    named = {"status": 0, "revert": "InvalidSignature"}
    assert measure.intended_revert(named)
    assert not measure.intended_revert({"status": 0, "revert": "OutOfGas"})
    assert not measure.intended_revert({"status": 0, "revert": "unnamed:0xdeadbeef"})
    assert not measure.intended_revert({"status": 0, "revert": None})
    assert not measure.intended_revert({"status": 1, "revert": None})


def test_gas_table_is_the_median_per_label():
    txs = load("sepolia_txs.json")["transactions"]
    fn = load("measurements.json")["functions"]
    pe = sorted(t["gasUsed"] for t in txs if t["label"] == "proposeEpisode" and t["status"] == 1)
    assert fn["proposeEpisode"]["n"] == len(pe)
    assert "latency_sample" not in fn
    for row in ("deploy_IncentivePool", "pool_fund", "deploy_PoolVerify", "transfer_new_account"):
        assert row in fn, row


def test_latency_history_is_copied_verbatim_and_never_merged():
    hist = load("sepolia_latency_history.json")["blocks"][0]
    m = load("measurements.json")
    first = m["latency_blocks"][0]
    assert (first["n"], first["p50"], first["p90"], first["measuredAt"]) == (hist["n"], hist["p50"], hist["p90"],
                                                                             hist["measuredAt"])
    assert first["label"] == f"measured {hist['measuredAt'][:10]}, raw samples not kept"
    assert "worktree" not in json.dumps(m), "the label states what is kept, not an internal cause"
    assert m["latency_seconds"]["n"] == max(b["n"] for b in m["latency_blocks"])


def test_no_latency_sample_is_invented():
    hist = {"blocks": [{"label": "old", "measuredAt": "2026-10-15T01:00:28Z", "n": 4, "p50": 1.0, "p90": 2.0,
                        "what": "w", "source": {}}]}
    after, before = 1_800_000_000, 1_700_000_000
    txs = [{"tx": "0xa", "timestamp": after}, {"tx": "0xb", "timestamp": before}]
    log = [{"tx": "0xa", "seconds": 12.0}, {"tx": "0xb", "seconds": 99.0},  # 0xb: already in the old block
           {"tx": "0xc", "seconds": 50.0},  # not on chain: ignored
           {"tx": "0xa", "seconds": None}]
    head, blocks = measure.latency(txs, log, hist)
    assert [b["n"] for b in blocks] == [4, 1] and blocks[1]["p50"] == 12.0
    assert head["n"] == 4
    head, blocks = measure.latency(txs, [], hist)
    assert len(blocks) == 1, "no new block without a logged sample"


def test_txlog_appends_sepolia_only_and_once(tmp_path):
    p = tmp_path / "log.jsonl"
    row = {"label": "finalize", "tx": "0x01", "status": 1, "gasUsed": 1, "effectiveGasPrice": 1, "seconds": 1.5,
           "revert": None, "block": 1}
    assert txlog.append([row], 31337, "demo", p) == 0 and not p.exists()
    assert txlog.append([row, row], 11155111, "operator", p) == 1
    assert txlog.append([row], 11155111, "operator", p) == 0
    assert txlog.read(p) == [{**row, "command": "operator"}]


def test_txlog_broadcast_labels_match_the_chain_record():
    have = {t["tx"]: t for t in load("sepolia_txs.json")["transactions"]}
    for f in sorted((ROOT / "contracts" / "broadcast").glob("*/11155111/run-latest.json")):
        _, rows = txlog.broadcast_rows(f)
        for r in rows:
            assert have[r["tx"]]["label"] == r["label"] and have[r["tx"]]["gasUsed"] == r["gasUsed"]


def test_readme_testnet_block_is_rendered_from_committed_files():
    block = readme_testnet.render()
    readme = (ROOT / "README.md").read_text()
    assert block in readme, "README testnet block is stale: run make measure (or pipeline/readme_testnet.py)"
    m = load("measurements.json")
    assert f"{m['transactions']['logged']} Sepolia transactions" in block
    assert "{" not in block and "}" not in block, "an unformatted placeholder leaked into the README block"
    for f in readme_testnet.FUNCS:
        assert f"`{f}` {m['functions'][f]['gas_sepolia_median']:,}" in block, f


def test_readme_testnet_block_names_the_pool_and_the_latency_provenance():
    """The Sepolia table carries the IncentivePool (main and sandbox addresses and the buyer's opening transaction, read
    from the deployment record and the chain record; both pools' deploy transactions must be confirmed in the chain
    record even though the README, for its size budget, leaves them to the verified address pages); the 15 Oct latency
    block says its raw samples were not kept."""
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    txs = {t["tx"]: t for t in load("sepolia_txs.json")["transactions"]}
    block = readme_testnet.render()
    row = next(x for x in block.splitlines() if x.startswith("| `IncentivePool`") and "address/" in x)
    assert dep["incentivePool"] in row and dep["sandbox"]["incentivePool"] in row
    hashes = re.findall(r"/tx/(0x[0-9a-f]{64})", row)
    labels = sorted(txs[h]["label"] for h in hashes)
    assert labels == ["pool_fund"], labels
    pt = readme_testnet.pool_txs(dep)
    for k in ("main", "sandbox"):
        assert txs[pt[k]]["label"] == "deploy_IncentivePool" and txs[pt[k]]["status"] == 1, k
    assert all(txs[h]["status"] == 1 for h in hashes)
    fund = next(txs[h] for h in hashes if txs[h]["label"] == "pool_fund")
    assert fund["to"] == dep["incentivePool"].lower() and f"{int(fund['valueWei']) / 1e18:g} ETH" in row
    lat = load("measurements.json")["latency_seconds"]
    assert "raw samples" in lat["label"] and "raw samples not kept" in block


def test_readme_latency_leads_with_the_samples_logged_in_the_repository():
    """The README's latency figure is the block anyone can recompute from data/derived/sepolia_txlog.jsonl; the dated
    15 Oct block, whose raw samples are gone, comes second. Without a logged block the headline block leads."""
    m = load("measurements.json")
    lead, rest = readme_testnet.latency_lead(m)
    assert "sepolia_txlog.jsonl" in lead["label"] and "raw samples" not in lead["label"]
    assert any("raw samples" in b["label"] for b in rest)
    line = next(x for x in readme_testnet.render().splitlines() if "Latency" in x)
    assert line.index(f"**p50 {lead['p50']} s, p90 {lead['p90']} s**") < line.index("raw samples not kept")
    assert f"n = {lead['n']} logged since" in line
    dated = {**m, "latency_blocks": [b for b in m["latency_blocks"] if "raw samples" in b["label"]]}
    assert readme_testnet.latency_lead(dated)[0] == m["latency_seconds"]

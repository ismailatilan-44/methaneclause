"""The judge walk-through's refund-side run on Ethereum Sepolia (judge sandbox [SIM]), by a judge test wallet the author
generated and ran (not an independent judge), read from tracked files only.

Labels come from data/derived/sepolia_txlog.jsonl (pipeline/observer/judge.py appends each transaction as it sends it);
status, sender and block come from data/derived/sepolia_txs.json (rebuilt from the chain by `make sepolia-txs`). The
case facts (the relapse plume, the qualified scene) come from data/derived/cases/DZA_S_012.* exactly as `make
judge-cure` chose them. `arc()` exits 1 if a step is missing, failed on chain, was sent by another wallet, or is out of
order: no sentence about this run is rendered from a record that does not show it.

Rendered by pipeline/readme_testnet.py (README) and scripts/build_app_data.py (Devpost, PDF, the page's explorer table).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from common import DERIVED

SCAN = "https://sepolia.etherscan.io"
CASE = "DZA_S_012"  # observer/judge.py CURE_CASE (not imported: that module needs a chain client)
INSTRUMENTS = {"tan": "Tanager", "emi": "EMIT", "av3": "AVIRIS-3", "gao": "GAO"}
# (key, txlog command, txlog label, sent by the judge wallet?) in the order the walk-through sends them
STEPS = [
    ("join", "judge:attest", "registerKey_judge", None),  # sent with the org's published controller key [SIM]
    ("episode", "judge:propose", "proposeEpisode", True),
    ("episodeConfirmed", "judge:finalize", "finalize", True),
    ("relapse", "judge:cure", "proposeEpisode_cure", True),
    ("relapseConfirmed", "judge:cure", "finalize_cure", True),
    ("poolOpened", "judge:cure", "fund_pool_cure", False),  # sent with the buyer's published key [SIM]
    ("claim", "judge:cure", "openClaim_cure", True),
    ("pass", "judge:cure", "attestCleanPass_cure", True),
    ("counted", "judge:cure", "countPass_cure", True),
]


def _case_facts() -> dict:
    doc = json.loads((DERIVED / "cases" / f"{CASE}.clean_passes.json").read_text())
    case = json.loads((DERIVED / "cases" / f"{CASE}.json").read_text())
    rel = doc["summary"]["firstPlumeAfterSince"]
    plume = max((p for p in case["cm_plumes_within_radius"]
                 if p["plume_id"].rsplit("-", 1)[0] == rel["sceneName"] and p.get("emission_auto") is not None),
                key=lambda p: p["emission_auto"])
    rel_t = int(datetime.fromisoformat(rel["sceneAtIso"].replace("Z", "+00:00")).timestamp())
    scene = next(x for x in doc["scenes"] if x["qualified"] and x["sceneAt"] > rel_t)
    if scene["instrument"] not in INSTRUMENTS:
        raise SystemExit(f"cure_arc: no display name for instrument {scene['instrument']!r}")
    return {"case": CASE, "relapseDate": rel["sceneAtIso"][:10], "relapsePlume": plume["plume_id"],
            "relapseKgH": round(plume["emission_auto"]), "sceneDate": scene["sceneAtIso"][:10],
            "sceneName": scene["sceneName"], "instrument": INSTRUMENTS[scene["instrument"]]}


def arc() -> dict:
    """The latest complete run: the last `countPass_cure` in the log and, walking back, the last earlier row of each
    step. Every step must be status 1 in the chain record; the test wallet's own steps must come from one wallet."""
    log = [json.loads(x) for x in (DERIVED / "sepolia_txlog.jsonl").read_text().splitlines() if x.strip()]
    chain = {t["tx"]: t for t in json.loads((DERIVED / "sepolia_txs.json").read_text())["transactions"]}
    picked, before = {}, None
    for key, cmd, label, _ in reversed(STEPS):
        rows = [r for r in log if r["command"] == cmd and r["label"] == label
                and (before is None or r["block"] <= before)]
        if not rows:
            raise SystemExit(f"cure_arc: no {cmd} {label} in data/derived/sepolia_txlog.jsonl before block {before}")
        picked[key] = rows[-1]
        before = rows[-1]["block"]
    wallet, steps = None, []
    for key, cmd, label, by_judge in STEPS:
        t = chain.get(picked[key]["tx"])
        if t is None or t["status"] != 1:
            raise SystemExit(f"cure_arc: {label} {picked[key]['tx']} is not a status-1 transaction in "
                             "data/derived/sepolia_txs.json (run make sepolia-txs)")
        if by_judge:
            wallet = wallet or t["from"]
            if t["from"] != wallet:
                raise SystemExit(f"cure_arc: {label} was sent by {t['from']}, not the judge test wallet {wallet}")
        steps.append({"key": key, "label": label, "tx": f"{SCAN}/tx/{t['tx']}", "block": t["block"],
                      "from": t["from"], "fromRole": t.get("fromRole"), "gasUsed": t["gasUsed"]})
    counted = sum(1 for r in log if r["command"] == "judge:cure" and r["label"] == "countPass_cure"
                  and r["block"] >= picked["claim"]["block"] and chain.get(r["tx"], {}).get("status") == 1)
    return {"wallet": wallet, "walletUrl": f"{SCAN}/address/{wallet}", "steps": steps, "counted": counted,
            **_case_facts()}


def _link(url: str, n: int = 8) -> str:
    return f"[`{url.rsplit('/', 1)[-1][:n]}…`]({url})"


def _windows() -> dict:
    """The sandbox and production window lengths, read from data/derived/policy_v2.json (`make policy-v2`)."""
    pol = json.loads((DERIVED / "policy_v2.json").read_text())["policies"]
    sb, main = pol["POLICY_V2_SANDBOX"]["policy"], pol["POLICY_V2"]["policy"]
    return {"sandboxChallenge": sb["core"]["challengeWindow"], "sandboxClean": sb["pool"]["cleanWindow"],
            "policyClean": main["pool"]["cleanWindow"]}


def minutes(sec: int) -> str:
    """600 -> "10-minute" (an adjective); exits 1 on a window that is not whole minutes."""
    if sec % 60:
        raise SystemExit(f"cure_arc: window of {sec} s is not whole minutes")
    return f"{sec // 60}-minute"


def days(sec: int) -> str:
    """86400 -> "one day", 172800 -> "2 days"; exits 1 on a window that is not whole days."""
    if sec % 86400:
        raise SystemExit(f"cure_arc: window of {sec} s is not whole days")
    n = sec // 86400
    return "one day" if n == 1 else f"{n} days"


def clean_window_note() -> str:
    """"(10-minute sandbox window [SIM]; one day in the policy)", both lengths from policy_v2.json."""
    w = _windows()
    return f"({minutes(w['sandboxClean'])} sandbox window [SIM]; {days(w['policyClean'])} in the policy)"


def readme_line(a: dict, first_installment_passes: int) -> str:
    s = {x["key"]: x for x in a["steps"]}
    # two source lines, one paragraph: a secret scan reads a 64-hex value on a line that says "wallet" as a possible key, so the transaction links sit on the line after the one naming the judge test wallet
    return (f"**Refund side, by a judge test wallet [SIM] run by the author** (observer OBS_JUDGE, judge sandbox, "
            "`make judge-cure`):\n"
            f"the {a['case']} relapse of {a['relapseDate']} ({a['relapseKgH']:,} kg/h) CONFIRMED "
            f"{_link(s['relapseConfirmed']['tx'])}; a refund claim opened {_link(s['claim']['tx'])}; the real "
            f"{a['sceneDate']} {a['instrument']} clean pass, signed by the test key and observer 1 [SIM], counted "
            f"after its clean window {clean_window_note()} {_link(s['counted']['tx'])} ({a['counted']} of the "
            f"{first_installment_passes} the first installment needs).")


def loop_sentence(a: dict, first_installment_passes: int) -> str:
    """Devpost / PDF: both sides of the clause on a public chain, each step with its transaction."""
    s = {x["key"]: x for x in a["steps"]}
    w = _windows()

    def tx(k: str) -> str:
        return _link(s[k]["tx"], 12)

    return (f"**Both sides of the clause have transactions on a public chain:** a confirmed rebate paid out, and on the "
            "refund side a confirmed relapse, an opened claim and a first counted clean pass (installments, the freeze "
            "and the relapse cancel run end to end in `make demo`). The refund side is the judge walk-through, run by "
            f"the author on Sepolia [SIM]: a judge test wallet joined the judge sandbox as observer OBS_JUDGE "
            f"({tx('join')}), co-signed the real {a['case']} relapse plume of {a['relapseDate']} "
            f"({a['relapseKgH']:,} kg/h), which was CONFIRMED after its {minutes(w['sandboxChallenge'])} sandbox "
            f"challenge window ({tx('relapseConfirmed')}); the buyer [SIM] funded the sandbox pool ({tx('poolOpened')}), "
            f"a refund claim opened ({tx('claim')}), and the real {a['sceneDate']} {a['instrument']} qualified clean "
            f"pass, signed by the wallet and observer 1 [SIM], was attested ({tx('pass')}) and counted after its clean "
            f"window {clean_window_note()} ({tx('counted')}): {a['counted']} of the {first_installment_passes} counted "
            "passes the first refund installment needs.")


APP_LABELS = {
    "join": "judge sandbox [SIM]: a judge test wallet run by the author joins as observer OBS_JUDGE",
    "episode": "judge sandbox [SIM]: the wallet proposes its TKM_S_276 episode",
    "episodeConfirmed": "judge sandbox [SIM]: that episode CONFIRMED",
    "relapse": "judge sandbox [SIM]: the DZA_S_012 relapse proposed, co-signed by the wallet",
    "relapseConfirmed": "judge sandbox [SIM]: the relapse CONFIRMED",
    "poolOpened": "judge sandbox [SIM]: the buyer [SIM] funds the sandbox pool",
    "claim": "judge sandbox [SIM]: refund claim opened",
    "pass": "judge sandbox [SIM]: real clean pass attested by the wallet + observer 1 [SIM]",
    "counted": "judge sandbox [SIM]: clean pass counted after its clean window",
}


def app_steps(a: dict) -> list[dict]:
    """Rows in the shape of testnet_run.json steps, for the page's explorer table (status 1 each, from the chain)."""
    return [{"step": APP_LABELS[x["key"]], "status": 1, "gasUsed": x["gasUsed"], "tx": x["tx"], "source": "judge cure arc"}
            for x in a["steps"]]

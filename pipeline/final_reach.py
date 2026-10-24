"""When a covenant's final refund installment can be scheduled at all (contracts/src/lib/PoolInstallments.sol
`_tryFinal`, SECURITY R27): only if since + cureTerm + relapseGrace <= end + publicationLag, with since = detectedAt +
linkWindow and relapseGrace = publicationLag + challengeWindow + contestWindow. Read by the README / PDF mechanism block
(scripts/build_app_data.py) and the model clause's Term (pipeline/clause.py), so neither states 30/30/40 without it."""
from __future__ import annotations

DAY = 86_400


def final_reach(policy: dict) -> dict:
    """`minTermDays`: days needed between the episode and the end of the covenant term; `lastDetectionDay`: last day of
    the term (from `core.start`) an episode can still reach the final installment, None if no day can;
    `nonFinalShareBp`: what a refund reaches without it."""
    core, pool = policy["core"], policy["pool"]
    need = core["linkWindow"] + pool["cureTerm"] + core["challengeWindow"] + core["contestWindow"]
    last = (core["term"] - need) // DAY
    return {"minTermDays": -(-need // DAY), "lastDetectionDay": last if last >= 0 else None,
            "nonFinalShareBp": sum(s for _, s, _, final in pool["installments"] if not final)}

#!/usr/bin/env python3
"""Rounding lemma of the installment shares, proved with Z3 over unbounded integers (make halmos).

PoolInstallments creates installment i with amount_i = cap * shareBp_i / 10_000 (Solidity integer division).
Halmos proves that the code creates exactly these amounts (check_installments_amountsAsFormula); this script proves
the arithmetic that follows, which Halmos's 256-bit bit-vector query does not finish (timed out at 900 s):

    for all integers cap in [0, 2**128) and s0, s1, s2 >= 1 with s0 + s1 + s2 == 10_000:
        cap - 2 <= floor(cap*s0/10_000) + floor(cap*s1/10_000) + floor(cap*s2/10_000) <= cap

Why integers are exact here: cap < 2**128 (an ONCHAIN cap is a core rebate <= the bond; a VERDICT_ONLY cap is a
uint128 tier amount) and s_i <= 10_000 < 2**14, so cap * s_i < 2**142 never wraps in uint256 and never trips the
checked-arithmetic revert; Solidity's `/` on non-negative operands is floor division. Each floor is encoded by its
defining inequalities, so Z3 answers in linear-integer terms per product.

A third query shows the bound is tight: Z3 finds a cap that loses exactly 2 wei under POLICY_V2's shares, so a
1-wei bound would be false. Run with the Python that ships with Halmos (it has z3-solver). Exit 0 = both bounds
proved (UNSAT negation) and the tightness witness found.
"""
from __future__ import annotations

import sys

from z3 import And, Int, Not, Or, Solver, sat, unsat

N = 10_000


def prove(bound_shares: bool) -> bool:
    cap = Int("cap")
    s = [Int(f"s{i}") for i in range(3)]
    q = [Int(f"q{i}") for i in range(3)]
    sv = Solver()
    sv.set("timeout", 120_000)
    sv.add(cap >= 0, cap < 2**128)
    if bound_shares:
        sv.add(*(si >= 1 for si in s), s[0] + s[1] + s[2] == N)
    else:  # POLICY_V2's shares only
        sv.add(s[0] == 3000, s[1] == 3000, s[2] == 4000)
    for qi, si in zip(q, s):
        # qi == floor(cap * si / N)
        sv.add(N * qi <= cap * si, cap * si < N * qi + N)
    total = q[0] + q[1] + q[2]
    sv.add(Not(And(total <= cap, total >= cap - 2)))
    r = sv.check()
    label = "every share vector summing to 10,000" if bound_shares else "POLICY_V2 shares 3000/3000/4000"
    if r == unsat:
        print(f"   proved  cap - 2 <= sum of installments <= cap, for {label}, every cap < 2**128")
        return True
    if r == sat:
        print(f"   FAIL    counterexample for {label}: {sv.model()}")
    else:
        print(f"   UNKNOWN for {label} (solver: {sv.reason_unknown()})")
    return False


def tight() -> bool:
    """The 2-wei bound is tight: with POLICY_V2's shares some cap loses exactly 2 wei (a 1-wei bound fails)."""
    cap = Int("cap")
    q = [Int(f"q{i}") for i in range(3)]
    sv = Solver()
    sv.add(cap > N, cap < 2**128)  # not only dust: a cap above 10,000 wei
    for qi, si in zip(q, (3000, 3000, 4000)):
        sv.add(N * qi <= cap * si, cap * si < N * qi + N)
    sv.add(q[0] + q[1] + q[2] < cap - 1)  # loses more than 1 wei
    if sv.check() != sat:
        print("   FAIL    no cap loses 2 wei: the stated bound is not tight")
        return False
    m = sv.model()
    c = m[cap].as_long()
    amounts = [c * s // N for s in (3000, 3000, 4000)]
    print(f"   tight   cap = {c} wei, installments {amounts} sum to {sum(amounts)}: 2 wei lost, so 1 wei is not a bound")
    return c - sum(amounts) == 2


if __name__ == "__main__":
    ok = prove(False) & prove(True) & tight()
    sys.exit(0 if ok else 1)

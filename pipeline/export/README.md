# Exports (spec §14, §15)

| File | What it writes | Round trip |
|---|---|---|
| `iea_annex_d.py` | IEA Annex D "Summary of satellite-detected emission event" → `data/derived/export/<source>.annex_d.{json,csv}`; official labels only; `x-methaneclause.euEvidence` and `x-methaneclause.marsVocabulary` | JSON and CSV re-import to the same RFC 8785 hash; the embedded bundle still hashes to the attested `evidenceHash` |
| `annex_c.py` | IEA Annex C MARS Feedback Form (the seller's voice) → `<case>.annex_c[.example].{json,csv}`; `claim_hash(form)` is the bytes32 for `recordMitigationClaim` | JSON and CSV re-import to the same `claimHash` |
| `credit_note.py` | Credit-note register for VERDICT_ONLY covenants → `<case>.credit_note.{json,csv}` | JSON and CSV re-import to the same register hash; an edited note is refused (its `noteHash` no longer matches) |

```sh
make exports                                         # Annex D (TKM) + the [SIM] Annex C examples, offline
cd pipeline && .venv/bin/python -m export.credit_note --case TKM_S_276 --verdict verdict.json
cd pipeline && .venv/bin/python -m export.credit_note --case TKM_S_276 --event due.json --chain-id 31337 --pool 0x..
```

## Calling the credit-note export (from the demo and the page)

```python
from export import credit_note as cn
rebate = cn.rebate_note({"chain": ..., "chainId": ..., "covenantContract": ..., "covenantId": ..., "episodeId": ...,
                         "state": "CONFIRMED", "tier": ..., "tx": ..., "blockTime": ...}, case="TKM_S_276")
refund = cn.refund_note(credit_note_due_log, case="TKM_S_276", chain=..., chain_id=..., pool=...,
                        cap_units=..., share_bp=...)          # cap / share optional: re-derive the amount
old, new = cn.supersede(old, new, reason="...")             # off-chain only
cn.write(cn.register("TKM_S_276", [rebate, refund]))
```

- A **rebate** note exists only for a CONFIRMED core episode (`NotDue` otherwise). Amount =
  `POLICY_V2_VERDICT_ONLY.pool.unitTierAmounts[tier − 1]`, in policy units ×100 (spec §20): 300 = "3.00 policy
  units". Seller [SIM] → buyer [SIM].
- A **refund** note follows the pool's `CreditNoteDue(claimId, installmentId, amount, ref)` log. The export re-derives
  `ref = keccak256(abi.encode(chainId, pool, installmentId))` and refuses a log whose ref is not that (another chain,
  another pool, another installment). Buyer (or the sponsor the clause names) [SIM] → seller [SIM].
- Every note says: settle under clause X of the supply contract by bank transfer or credit note; this is not a payment
  instrument; the CBRT 2021 sentence; not legal advice.

## Decisions that differ from the spec text

- **Credit-note file = a register** (`methaneclause.credit_notes/1`, one or more notes, append-only), at the spec's path
  `<case>.credit_note.{json,csv}`. Supersession needs two notes side by side.
- **Rebate `ref` is derived off chain** (`keccak256(abi.encode(chainId, covenant, episodeId))`): the core emits no
  credit-note ref and stays unchanged. The note says so (`refSource`).
- **Supersession is an off-chain export step** (a contract review finding): `supersede` marks the old note
  `superseded`, links `supersededBy` / `supersedes` and records `onChainEvent: null`. The on-chain
  `CreditNoteSuperseded` event is reserved and never emitted, and no note claims it.
- **No committed credit note yet.** A note needs a verdict-only run (`make demo`). The Sepolia main covenant
  settles on chain, and its episode is still PROPOSED. No mock register is shipped: `pipeline/schemas_v2.py` (frozen with the pool's
  interfaces) has no credit-note schema; the page can call `credit_note` with its own Anvil run.
- **Annex C examples are [SIM]**: every answer in `<case>.annex_c.example.json` is a simulated seller's claim.
  `blank_form` pre-fills only public facts (Source ID, MARS's last plume id, the [SIM] operator).
- **Annex D cites an Annex C claim** in "Mitigation measures taken?" as "NOT EVIDENCED; seller's claim (IEA Annex C
  feedback form), hash on chain 0x…, state unchanged" when a claim hash is passed. "Emissions ceased?" stays
  "NOT EVIDENCED".
- **`euEvidence`** marks each Annex IX item `yes` / `partly` / `no` with the reason, and states that it is evidence, never
  a statement of compliance. Producer identity is `no` (the seller is [SIM]). Source-level measurement and LDAR /
  response evidence are `partly`. The IEEE standards are context; the export does not claim to conform to any of them.
- **`marsVocabulary`** maps a CONFIRMED episode to MARS's "Ongoing" and a claim with ≥ 3 counted passes to MARS's
  "Potentially ceased" (terms quoted from the MARS dictionary). A relapse after the run turns the current term back to
  "Ongoing" (DZA). MARS's own `emission_status` stays null; we cite its terms and never fill its field.

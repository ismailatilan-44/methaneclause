# AGENTS.md — working rules for coding agents in this repository

MethaneClause is a methane clause for gas supply contracts that enforces itself: the seller posts a bond for
named sources; when independent satellites re-detect a super-emitter there, k-of-n signed detections from distinct
observer organisations rebate the buyer from the bond after a challenge window. IEEE ClimateChain Global
Hackathon 2026, Track 4. Human-facing overview: [`README.md`](README.md). Specification: [`docs/spec.md`](docs/spec.md).

## Map

| Path | What it is |
|---|---|
| `contracts/` | Foundry project. Core (byte-identical, `make core-bytecode`): `KeyRegistry`, `MethaneCovenant`, EIP-712 `Attestation`, `SigLib`. v2: `IncentivePool` (+ five linked `Pool*` libraries), `SuretyBond`, `KeyRegistryV2` (Anvil only); unit, invariant, hostile-receiver and replay tests; `contracts/script/Deploy.s.sol` |
| `pipeline/` | Python 3.12: keyless fetch of Carbon Mapper + UNEP MARS, frozen join criterion, canonical hashing (RFC 8785), Merkle facility set, rule baselines, model comparison, bond sizing, stake band, audit of every quoted number |
| `pipeline/observer/` | The seam between data and ledger: fetch → canonical bundle → validate → SHA-256 → EIP-712 signature; `pipeline/observer/demo.py` is the end-to-end loop; `pipeline/observer/cleanpass.py` is the clean-pass observer |
| `data/derived/` | Committed, licensed derived data; every number in README comes from `data/derived/audit_numbers.json` or another committed file through a generated block. Raw downloads live in data/raw/, never committed, often absent; tests use committed inputs only |
| `scripts/` | `scripts/demo.sh` (local Anvil end-to-end: DZA replay, v1 checks, verdict-only, portfolio), `scripts/judge.sh` (judge walk-through on Ethereum Sepolia, judge sandbox [SIM]), `scripts/judge_local.sh` (the same on Anvil), `scripts/check_repo.py` (wording, links, tracked-tree guard), `scripts/verify_bundle.py` (re-verify a signed bundle against the registry) |
| `app/` | One-page UI, no build step (`index.html`, `app.js`, `style.css`; data in `app/data/` from `make app`): map, covenant card, episodes, tamper button (JCS + EIP-712 + ML-DSA-44 checked in the browser), Annex D export with round trip, live Sepolia read |
| `docs/` | `docs/spec.md`, `docs/SECURITY.md`, `docs/ETHICS.md`, `docs/DATA.md`, `docs/FOOTPRINT.md`, `docs/failure-modes.md`; the model card is `pipeline/model/MODEL_CARD.md` |

## Commands

```sh
make setup          # Python venv with pinned deps (needs uv); Foundry must be on PATH
make test           # pipeline (pytest) + contracts (forge unit, invariant, hostile-receiver and replay suites)
make demo           # Anvil: deploy → covenant → observers sign → rejections → rebate; 33 checks, exits non-zero on any failure
make check          # the "nothing broke" gate: test + demo + judge-local + STRICT audit + words + links + tree + secrets (offline, ~1 min)
make help           # every target with its one-line purpose
make reproduce      # fetch → join → cases → sourceset → baseline → model → bond → stake → audit (network, ~6 min)
```

`ANVIL_PORT=8546 make demo` if 8545 is busy. `LIVE=1 make demo` makes both observers fetch Carbon Mapper now.
`STRICT=1 make audit` (or `make audit-strict`) fails if any number moved against `data/derived/audit_numbers.json`.

Public testnet (Ethereum Sepolia; addresses in `contracts/deployments/11155111.json`): `make judge-attest`,
`judge-propose`, `judge-tamper`, `judge-challenge`, `judge-finalize` with `JUDGE_PK` set (judge sandbox [SIM]);
`make measure` refreshes `data/derived/measurements.json`. The operator targets `make deploy-sepolia`, `testnet-run`,
`testnet-finalize` and `fund-judge` load the operator's `.env` and spend its test ETH: ask first.

**Ask the operator before running** `make reproduce`, `make fetch`, `make cases` without `OFFLINE=1`, or anything
with `REFRESH=1`: they download ~55 MB plus ~2,000 API responses and can move audited numbers when the provider
re-processes its data. The offline chain `make baseline model bond stake audit` needs nothing from the network.

## Rules that are not negotiable

1. **Tests first; the demo stays green.** Any change under `contracts/`, `pipeline/observer/` or `scripts/` is
   followed by `make test` and `make demo` before it is called done. `make check` green is the definition of done.
2. **No number is typed by hand.** README, docs, UI and the paper quote values produced by `make reproduce` or by
   a test. If a number changes, the script that produces it changed first.
3. **Real vs simulated is labelled.** Every simulated party, input or step carries `[SIM]` where it appears.
   Tonnage is "stake, not abatement". Non-detection never cancels a rebate or counts as repair, and no core function
   accepts "no plume"; the v2 incentive pool may release a capped refund on qualified clean passes that the next attested
   detection cancels.
4. **Fail closed.** A bad input produces a reverted transaction (receipt status 0) and the bond does not move; no
   state is left half-written. Constant-returning mocks are never described as "end to end".
5. **Raw data stays off-chain.** Only hashes, identifiers and money go on-chain. Canonical serialisation is
   RFC 8785 JCS; the bundle must still verify after keys rotate.
6. **Honest security language.** Never write "quantum-safe", "tamper-proof", "trustless" or "unhackable". Write
   "tamper-evident", name the signature algorithm (secp256k1 ECDSA on-chain) and state the post-quantum boundary.
   `make words` enforces this on product files.
7. **Keys belong to the operator.** Agents never read `.env`; `.env.example` documents the variables. Local runs use
   Anvil's public test accounts only.
8. **English** in code, comments, docs and commit messages.
9. **Spec deviations are written down** in the component README under "Decisions that differ from the spec", with
   what, why and what it costs — never silently reinterpreted. The model is kept only if it beats the frozen rule on
   both held-out sets under the pre-registered rule in `pipeline/model/train.py`; today it does not, and the product
   uses the rule.
10. **Honesty widens, it does not shrink.** When a claim outruns the evidence, first propose what would make it
    true (a measurement, a second observer organisation, an added data source, an experiment) with its cost; if it
    cannot be built now, write the path as future work; only then state the gap — once, in the Limits section. The
    headline and architecture describe the designed system; prototype shortcuts ([SIM] parties, keys run by the
    author) are disclosed once, not repeated in every sentence. Never state a false fact.

## Branches and commits

- Work on a branch in its own git worktree; fast-forward to `main` when `make check` is green. CI runs
  only in the public repository (`.github/workflows/ci.yml`, kept as `ci/public-ci.yml` in development); the development
  repository runs no GitHub Actions.
- The v1 core contracts must stay byte-identical (`make core-bytecode`).
- Commit messages: imperative subject, a body that says what changed and why. Never use a bare `git stash` (the
  stash stack is shared between worktrees); set work aside with a temporary commit instead.
- Protected: never `rm -rf` outside your scratch area, never `git push --force`, never `git reset --hard` on a
  shared branch.

## Review protocol

- Before any headline number is repeated in README, the paper or the video, it gets an adversarial review: where
  does it come from, could the split leak, was anything chosen by looking at the test set, is the sentence an
  over-claim? "Too good" results are assumed leaky until the split code has been read.
- A review that finds a claim unsupported also says what would make it supportable (rule 10): the measurement,
  data or mechanism to add, or the future-work line. Narrowing the claim is the fallback, not the default fix.
- Before the public export, a security review of `contracts/` + `pipeline/observer/` against the threat table in
  `docs/SECURITY.md` (ISSRM/STRIDE; feature → mechanism → test).
- Before an outward artifact is frozen, read it as a judge: can it be run from a fresh clone with the three
  commands above, does the first screen say who the user is and which way money moves, are the limits stated?

## Data licences

Code is MIT (`LICENSE`). `data/derived/` is derived from **Data by Carbon Mapper®** (non-commercial, attribution,
share-alike) and **UNEP IMEO MARS** (CC BY-NC-SA 4.0); see `data/derived/DATA_LICENSE.md`. Do not add data from a
source without recording its licence there.

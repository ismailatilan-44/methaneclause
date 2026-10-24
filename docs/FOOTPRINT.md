# Footprint

Two different quantities, kept apart: what running this prototype costs (compute and chain), and what is at stake at
the sources the clause covers — a stake, not abatement. Every number below is read from a committed file named next
to it; the figures between `gen` markers are rendered by `make readme` (`scripts/build_app_data.py`), and
`make check` fails if they are stale.

## Compute

<!-- gen:fpCompute:begin -->
| What | Tool | Result | Source |
|---|---|---|---|
| Model training + inference (the boosted model that lost to the rule) | CodeCarbon 3.3.1 offline, country TUR | 5.39e-06 kg CO₂e | `footprint` in [`data/derived/model/model_metrics.json`](../data/derived/model/model_metrics.json) |
| Backtest, clean-run statistics, observer runs | not measured | — | no CodeCarbon run exists for these scripts yet; nothing is claimed |
<!-- gen:fpCompute:end -->

The product path uses the persistence rule, not the model (`pipeline/model/MODEL_CARD.md`), so routine operation runs
no training at all.

## Chain

- **Consensus: proof of stake.** Ethereum Sepolia (chainId 11155111) and local Anvil. **PoW: no.**
<!-- gen:fpTransactions:begin -->
- **Transactions on Sepolia:** 138 logged, every transaction our addresses sent, rebuilt from the chain; 7 were expected rejections (status 0, shown on purpose) and 2 were unexpected failures with their causes recorded (`transactions` in [`data/derived/measurements.json`](../data/derived/measurements.json); the list is [`data/derived/sepolia_txs.json`](../data/derived/sepolia_txs.json)).
<!-- gen:fpTransactions:end -->
- **Gas per function** (median on Sepolia; Foundry's local Cancun schedule beside it where measured), from
  [`data/derived/measurements.json`](../data/derived/measurements.json):

<!-- gen:fpGas:begin -->
| Function | Gas, Sepolia median | Gas, Anvil (rounded to 1k) | n |
|---|---|---|---|
| `createCovenant` | 1,254,826 | 339,000 | 7 |
| `proposeEpisode` | 1,340,989 | 368,000 | 9 |
| `proposeEpisode`, rejected (status 0) | 113,416 | — | 7 |
| `challenge` | 151,470 | — | 3 |
| `submitResolution` | 485,563 | — | 3 |
| `finalize` | 397,954 | 118,000 | 5 |
| `withdraw` | 30,689 | 30,000 | 5 |
| `fund` | 248,387 | — | 3 |
| `pool_fund` | 2,452,902 | — | 3 |
| `deploy_IncentivePool` | 34,584,829 | — | 2 |
| `deploy_PoolVerify` | 10,328,856 | — | 1 |
| `deploy_PoolSetup` | 4,664,440 | — | 1 |
| `deploy_PoolEpisodes` | 3,586,409 | — | 1 |
| `deploy_PoolCorroboration` | 3,482,702 | — | 1 |
| `deploy_PoolInstallments` | 5,197,540 | — | 1 |
| `deploy_MethaneCovenant` | 25,075,377 | — | 4 |
| `deploy_KeyRegistry` | 6,242,107 | — | 4 |
| `deploy_registerKey` | 467,921 | — | 17 |
| `deploy_setOrgController` | 130,117 | — | 20 |
| `deploy_renounceAdmin` | 23,129 | — | 4 |
| `registerKey` | 470,069 | — | 2 |
| `transfer_new_account` | 204,600 | — | 12 |
| `transfer` | 21,000 | — | 6 |
| `pool_attestCleanPass` | 1,569,642 | — | 1 |
| `pool_countPass` | 578,574 | — | 1 |
| `pool_openClaim` | 721,912 | — | 1 |
<!-- gen:fpGas:end -->

<!-- gen:fpScale:begin -->
- Gas against facility-set size (Anvil, real transactions, `make scale` → [`data/derived/scale.json`](../data/derived/scale.json)): `createCovenant` costs 322,290–322,302 gas for 3 to 10,000 facilities (the set is one 32-byte root); `proposeEpisode` grows only with the Merkle path, from 351,049 gas (2 steps, 3 facilities) to 359,065 (12 steps, 10,000 [SIM padding]), about 801 gas per step; a path has at most ⌈log2 n⌉ steps (14 at 10,000).
<!-- gen:fpScale:end -->

  Sepolia currently prices new state far above the local schedule; the file reports both
  ([`contracts/README.md`](../contracts/README.md), "Sepolia gas is not Anvil gas").
- **Energy per transaction (secondary, per average transaction).** Crypto Carbon Ratings Institute, *The Merge –
  Implications on the Electricity Consumption and Carbon Footprint of the Ethereum Network*, September 2022,
  <https://carbon-ratings.com/dl/eth-report-2022> (read 2026-10-15), Table 18 "Best guess electricity consumption of
  the Ethereum PoS network on a per-transaction basis": **"Wh/tx per network 6.2943"** (Ethereum mainnet after the
  Merge, measured August–September 2022; the same value is in its Table 2). It is secondary for us: a mainnet average
  over all transactions, not a measurement of our transactions or of Sepolia. Both the report and ethereum.org's
  energy-consumption page (<https://ethereum.org/en/energy-consumption/>, page updated 28 July 2026) warn that
  per-transaction figures can mislead, because the energy to propose and validate a block does not depend on how many
  transactions it holds; ethereum.org gives the whole network as about 0.0026 TWh per year (from the same CCRI study).
  No CO₂e is derived here.
<!-- gen:fpEnergy:begin -->
- **Scale only.** Allocated per transaction, the 138 Sepolia transactions above correspond to 138 × 6.2943 Wh ≈ 869 Wh — an illustration of scale, not a measurement.
<!-- gen:fpEnergy:end -->

## Assumptions

- Testnet transactions carry no monetary value [SIM]; gas prices on Sepolia are not mainnet prices.
<!-- gen:fpPool:begin -->
- The pool's transactions measured on Sepolia: `pool_fund` (n = 3), `deploy_IncentivePool` (n = 2), `pool_attestCleanPass` (n = 1), `pool_countPass` (n = 1), `pool_openClaim` (n = 1). Its other calls (`release`, `relapse`, `attestCorroboration`) run on Anvil in `make demo` and are not yet in `data/derived/measurements.json`.
<!-- gen:fpPool:end -->

## What is at stake (not a footprint of this project)

This is a stake, not abatement: the clause makes these emissions cost the seller; it does not measure or claim any
change in them. The only quotable sentence is generated by `pipeline/backtest.py` into `card.stakeSentence` of
[`data/derived/backtest.json`](../data/derived/backtest.json), quoted verbatim (`pipeline/tests/test_doc_quotes.py`
fails if this quote and the card ever differ):

> Stake, not abatement: ~0.5 Mt CH₄/yr, the low bound of the band, across 428 facilities (of the 1,122 sources MARS
> labels absent today — a label decided after the window), the 425 with an episode plus 3 whose plumes all fell below
> 100 kg/h, assuming what satellites saw on their passes holds between them; passes are partly tasked toward expected
> plumes, which biases the seen duty cycle upward (roadmap: a duty cycle measured on un-targeted EMIT passes).

Only the lower bound is quoted; the mid and high sums stay in the JSON and are not estimates. The backtest is
counterfactual: if these sources had been under the clause; no clause existed.

# Scaling and sustainability roadmap

What runs today, with its measured cost, and the phases that take the clause from one prototype covenant to a clause
written into many gas supply contracts. Every figure below is read from a committed file named next to it; the
figures between `gen` markers are rendered by `make readme` (`scripts/build_app_data.py`), and `make check` fails if
they are stale.

## What runs today

<!-- gen:scToday:begin -->
- **One covenant, many facilities.** Portfolio [SIM]: 205 facilities — 203 join candidates, each chosen because it showed a Carbon Mapper plume that MARS did not see, plus the 2 other demo cases; 201 of 205 are sized: ARG_S_055, TKM_S_395, TKM_S_426, USA_S_053 own no Carbon Mapper source (the join matches plumes, ownership uses source centroids; their plumes belong to a nearer target's source) and cannot open an episode; bond = pool = 0.768 ETH, the P90 of one year's rebate exposure. In `make demo` the portfolio covenant's episode is proven by its 8-step Merkle path, and a proof against the wrong set fails `NotCovered`.
- **Data on chain.** A canonical evidence bundle is 754 bytes off chain; the chain stores only its 32-byte SHA-256 hash, the IDs and the money.
- **Latency.** Send to receipt through a public RPC on Ethereum Sepolia: p50 11.18 s, p90 13.63 s (n = 43, measured 2026-10-15, raw samples not kept); 21 logged since 2026-10-15 in data/derived/sepolia_txlog.jsonl: p50 11.03 s, p90 12.28 s ([`data/derived/measurements.json`](../data/derived/measurements.json)). The challenge window, not the chain, sets how long a verdict takes.
<!-- gen:scToday:end -->

### Gas against facility-set size

A covenant commits to its facilities through one Merkle root, so a seller's whole portfolio fits one covenant and one
bond; an episode proves its facility is in the set.

<!-- gen:scSetSize:begin -->
| Facilities in the set | Proof steps | `createCovenant` gas | `proposeEpisode` gas | `proposeEpisode` calldata, bytes | Set |
|---|---|---|---|---|---|
| 3 | 2 | 322,290 | 351,049 | 1,444 | data/derived/sourceset_v2.json |
| 205 | 8 | 322,290 | 355,838 | 1,636 | data/derived/sourceset_v3.json |
| 1,122 | 11 | 322,290 | 358,238 | 1,732 | the 1,122 MARS targets (data/derived/mars_sources.csv, join rule 1) |
| 10,000 | 12 | 322,302 | 359,065 | 1,764 | the MARS targets + synthetic leaves [SIM padding] |

`createCovenant` is flat (322,290–322,302 gas: the whole set is one 32-byte Merkle root); `proposeEpisode` grows only with the Merkle path, about 801 gas per proof step, and a path has at most ⌈log2 n⌉ steps. Real transactions on local Anvil 31337 with Foundry's Cancun schedule, 14 in all; every wrong-set proof reverted `NotCovered` and left the episode count and the bond unchanged (4 status 0). `make scale` → [`data/derived/scale.json`](../data/derived/scale.json).
<!-- gen:scSetSize:end -->

### Cost per call on Ethereum Sepolia

<!-- gen:scCalls:begin -->
| Call | Gas, Sepolia median | USD at 1 gwei | n |
|---|---|---|---|
| `createCovenant` | 1,254,826 | 3.04 | 7 |
| `proposeEpisode` | 1,340,989 | 3.25 | 9 |
| `finalize` | 397,954 | 0.96 | 5 |
| `withdraw` | 30,689 | 0.07 | 5 |
| `pool_openClaim` | 721,912 | 1.75 | 1 |
| `pool_attestCleanPass` | 1,569,642 | 3.81 | 1 |
| `pool_countPass` | 578,574 | 1.40 | 1 |

USD at the stated 1 gwei and ETH/USD 2,424.39 from Chainlink ETH/USD 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419 (Ethereum mainnet); measured 2026-10-22 ([`data/derived/measurements.json`](../data/derived/measurements.json)). Ethereum Sepolia prices new state far above Foundry's local schedule (`createCovenant` 339,000, `proposeEpisode` 368,000 gas there, rounded), so these are the upper line. Every other call, deployment included, is in [`FOOTPRINT.md`](FOOTPRINT.md).
<!-- gen:scCalls:end -->

### Cost does not grow with history

`relapse` cancels a claim in one call whatever the number of pending passes
(`test_relapse_gasIndependentOfPendingPasses`), pending passes per claim are bounded, and every payout is a pull: no
contract pushes money. Facilities scale through a covenant's Merkle root, observer organisations
through the key registry, sponsors through top-ups to one pool, and settlement through the verdict-only covenant where
money cannot move on chain (`make demo` exports its credit note).

## Who pays (sustainability)

Each role pays for the step it takes, and the refund side is funded by money that already moves on methane.

<!-- gen:scPays:begin -->
- **The seller** posts the bond the rebates come from, sized by a rule committed in `policyHash`: 0.015 ETH for the single-source v1 covenant, the P90 of simulated annual rebate exposure under the smoothed rule, and 0.768 ETH for the 205-facility portfolio [SIM], the P90 of a year's rebate exposure across its facilities.
- **The sponsor** — the lender whose sustainability-linked margin already moves on methane intensity, or the buyer itself — funds the pool: the capped refunds, and an observer fee of 0.00001 ETH per counted pass, split among the signing organisations (`pool.observerFeeWei`). Observing is paid work, paid by the party that already prices methane.
- **Open roles post bonds.** Proposing a core episode posts 0.0001 ETH and challenging one 0.0002 ETH (main Sepolia deployment); attesting a clean pass posts 0.0001 ETH and challenging it 0.0002 ETH (`POLICY_V2`). A challenge that fails pays its bond to the party the evidence favours: the buyer for a core episode, the seller for a clean pass or a MARS corroboration. A challenge that succeeds wins the proposer's or the clean-pass submitter's bond and gets its own back; a challenge left unresolved returns both bonds.
- **The ledger.** Ledger cost only, not the cost of the work on site: on the forward basis the 220 members had 129 episodes from 2025-09-12 to 2026-09-13; at USD 4.29 an episode plus one createCovenant that is USD 555 a year, or USD 0.0055 per t CO₂e projected at the central action share (USD 0.0038–0.0067 across scenarios and with no recurrence); credited over each member's whole window instead, the portfolio covenant set v3 costs USD 0.0015 per t CO₂e. Ledger cost only, not the cost of the work on site: one episode costs USD 4.29 on Ethereum Sepolia (proposeEpisode + finalize + withdraw at the stated 1 gwei and ETH/USD 2,424, measured 2026-10-22); at the portfolio's backtest rate of 309 episodes a year plus one createCovenant that is USD 1,330 a year, or USD 0.0015 per t CO₂e projected at the central action share over each member's whole window (USD 0.00059–0.0018 across scenarios and with no recurrence). It excludes the refund-side calls and the cost of posting the bond.
<!-- gen:scPays:end -->

Money already moves on methane, but not at a named source: lenders step a margin or a coupon on a company-wide target,
supply contracts carry disclosure, and the Commission's model clauses carry information. The sponsor's pool is that
money made source- and event-level ([`docs/clause/clause_sources.json`](clause/clause_sources.json)):

<!-- gen:scPrecedents:begin -->
| Party, instrument | Money term | How this clause differs | Source |
|---|---|---|---|
| Kinetik Holdings (Permian midstream), framework 2022: Sustainability-linked revolver, term loan and notes | Effective interest rate moves ±0.05 % a year with the number of targets met; one target is Scope 1+2 methane intensity −30 % by 2030 vs 2021 | Annual, company-wide, borrower-reported; the closest analogue to the refund side, but the next leak does not cancel it | [V] (step schedule [S]) [source](https://www.sec.gov/Archives/edgar/data/1692787/000169278724000011/apa-20231231.htm), read 2026-10-15 |
| Gasunie (Dutch gas TSO), issued 2021: €300 m 15-year sustainability-linked bond | Coupon +0.10 % if one of two 2030 targets is missed, +0.20 % if both; one target is methane about 50 % below 2020; tested once, at 31 Dec 2030 | One company total, one test date; the coupon steps by indenture, not on an event at a source | [V] [source](https://www.gasunie.nl/en/news/gasunie-successfully-issues-its-inaugural-sustainability-linked-bond), read 2026-10-15 |
| Petrobras, signed 8 Jul 2022: US$1.25 bn sustainability-linked loan | Pricing incentive linked to corporate KPIs, one of them "methane intensity in E&P"; target and step size not disclosed | Company-wide intensity from the borrower's inventory; the money is the lender's margin, not a payment to the gas buyer | [V] [source](https://www.sec.gov/Archives/edgar/data/1119639/000129281422003021/pbr20220711_6k.htm), read 2026-10-15 |
| Pavilion Energy with QatarEnergy (SPA 2020) and Chevron (SPA 2021); methodology 2021: LNG sale and purchase agreements | Disclosure only: a Statement of GHG Emissions for every cargo, third-party verified; no money term found | The first SPA-level methane term; a modelled cargo-chain estimate that nothing pays on | [S] [source](https://www.hydrocarbonengineering.com/the-environment/17112021/ghg-reporting-methodology-for-lng-cargoes-launched/), read 2026-10-15 |
| European Commission, Recommendation (EU) 2026/1834 of 20 Jul 2026: Optional model clauses under Art. 28(3) of Regulation (EU) 2024/1787 | No remedy term: no price adjustment, rebate, termination, liquidated damages, audit right, satellite or super-emitter reference | Moves information down the chain and leaves the economic clause open; this clause is that economic clause | [V] [source](https://eur-lex.europa.eu/eli/reco/2026/1834/oj/eng), read 2026-10-15 |

No public LNG contract with a methane-intensity price term was found; SPA price terms are usually confidential, so no public precedent was found.
<!-- gen:scPrecedents:end -->

### The ledger's own footprint

<!-- gen:scFootprint:begin -->
Proof of stake (Ethereum Sepolia and a local chain; no proof of work). Our addresses sent 138 Sepolia transactions; at the Crypto Carbon Ratings Institute's mainnet average of 6.2943 Wh per transaction that is ≈ 869 Wh — an illustration of scale, not a measurement. The forecast model that lost to the rule cost 5.39e-06 kg CO₂e to train and score; the product runs the rule, so routine operation trains nothing. Details and sources: [`FOOTPRINT.md`](FOOTPRINT.md).
<!-- gen:scFootprint:end -->

## Roadmap

Each phase names what it adds and which limit of today it removes.

### Phase 1 — a pilot on one supply contract

<!-- gen:scPilot:begin -->
**Pilot (proposed, not run).** One EU importer [SIM] writes the model clause into one supply contract concluded or renewed on or after 4 Aug 2024, the contracts Art. 28 of Regulation (EU) 2024/1787 covers ([V], [source](https://eur-lex.europa.eu/eli/reg/2024/1787/oj/eng)). One portfolio covenant covers the contracted sources; the stand-in is the 205-facility set already run on a local chain ([`data/derived/sourceset_v3.json`](../data/derived/sourceset_v3.json)), bonded at 0.768 ETH [SIM], the P90 of one year's rebate exposure.

- **Observers.** Four organisations with a quorum of 2 of 4 (`core.k`; n = k + 2, so a contest, which needs k + 1, keeps a spare organisation): two licensed satellite-data providers (each holds its own commercial data agreement), a university remote-sensing lab and an NGO methane analyst. None is the seller or the buyer or controlled by either; each holds its own keys.
- **Sponsor.** The lender whose sustainability-linked margin already moves on methane intensity (the precedents under “Who pays”), or the buyer itself. Its pool pays the capped refund and the observer fee for each counted pass.
- **Timeline.** Before 1 Jan 2027, when Art. 28 applies ([V], [source](https://eur-lex.europa.eu/eli/reg/2024/1787/oj/eng)): sign, register the four organisations, open the covenant and the pool, run beside the importer's systems. Year one is the covenant term (365 days, `core.term`); each record is exported as IEA Annex D and attached as evidence to the importer's yearly Art. 27 report due 31 May ([V], [source](https://eur-lex.europa.eu/eli/reg/2024/1787/oj/eng)); Recommendation (EU) 2026/1835 asks Member States not to fine importers for breaches in 2027–29, except fraud ([S], [source](https://www.hsfkramer.com/insights/2026-08/eu-methan-regulation-eu-commission-clarifies-compliance-options-and-recommends-three-year-penalty-grace-period)), so the pilot runs while the obligation is new. Before 5 Aug 2028 (methane-intensity reporting begins; [V], [source](https://eur-lex.europa.eu/eli/reg/2024/1787/oj/eng)) the importer holds a year of attested record; before 5 Aug 2030 (maximum intensity values, [V], [source](https://eur-lex.europa.eu/eli/reg/2024/1787/oj/eng)) it decides whether the clause becomes standard in renewals. A renewal is the entry point: BOTAS–Bulgargaz, protocol of 6 Jul 2026 ([S], [source](https://pgjonline.com/news/2026/july/bulgargaz-botas-freeze-long-term-gas-deal-as-renegotiations-begin)).
- **Measured.** Episodes CONFIRMED, VOID, EXPIRED and LINKED per source; time from the provider's publication to CONFIRMED; challenges by reason code and outcome; observer disagreements that left no quorum; qualified clean passes, installments released and refunds cancelled by a relapse; ledger cost per episode against the Sepolia measurement; and the two ASSUMPTIONs of the carbon projection, the share of attested episodes the seller acts on and the share of emissions that stops, which no public dataset measures today.
<!-- gen:scPilot:end -->

- **Adds:** the model clause ([`docs/clause/model-clause.md`](clause/model-clause.md)) in a signed contract; three
  observer organisations on their own hosts with their own keys; a commercial data agreement with Carbon Mapper held
  by the observers ([`DATA.md`](DATA.md)); a surety that posts the seller's bond (`SuretyBond`, built and run on a local
  chain in `make demo`), so the seller's capital is not locked.
- **Removes as a limit:** observer keys held by one person, simulated counterparties, non-commercial data terms.

### Phase 2 — more sellers, observers and providers

- **Adds:** admission and removal of observer organisations by majority vote after an admission delay
  (`KeyRegistryV2`, built and tested on a local chain) on the public deployment; a NASA EMIT plume reader beside
  Carbon Mapper (open data; every attestation already names its `providerId` and `instrumentId`), so a second provider
  can attest the same source; a cross-provider gate on core payouts; Merkle batching of attestations; more sellers and
  sponsors on one pool through top-ups.
- **Removes as a limit:** one provider's catalogue as the only detection source, and a fixed observer set.

### Phase 3 — a production venue for settlement

- **Adds:** a production chain or an L2 (the contracts are plain EVM bytecode and the deployment script already
  targets Base Sepolia; an L2 run is measured before any L2 cost is quoted); a euro e-money token under MiCA where a
  token is lawful, the credit-note path where it is not; the same verdict on a permissioned ledger where the parties
  already share one (Hyperledger Fabric, k-of-n as the endorsement policy; README, "Production path"; `docs/PRODUCTION.md`, "Chain choice").
- **Removes as a limit:** testnet money.

### Phase 4 — beyond gas supply contracts

- **Adds:** the same two-sided clause for landfill methane, and data-provider agreements (Carbon Mapper commercial
  use, UNEP for MARS corroboration) signed once by the observer organisations and reused across covenants.
- **Removes as a limit:** a single sector and a per-contract data negotiation.

## Limits

Today the counterparties and the sponsor are [SIM], every main-deployment key is held by the author, money is testnet
ETH and no pilot or L2 run exists; the full list is the README's "Limits" section.

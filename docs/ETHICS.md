# Ethics and responsible use

MethaneClause moves money between companies when independent observers attest what public satellites saw. That gives
it real consequences for sellers, buyers and the people near the sources, so this page says who can do what, which
data it uses, where it is biased and what is simulated. Security details: [`docs/SECURITY.md`](SECURITY.md); data and
licences: [`docs/DATA.md`](DATA.md); compute and chain cost: [`docs/FOOTPRINT.md`](FOOTPRINT.md).

## Transparency

- **Every number has a script and an input hash.** `make reproduce` regenerates them (except the 15 Oct latency block, kept as recorded); `STRICT=1 make audit` fails if
  any quoted number moved against [`data/derived/audit_numbers.json`](../data/derived/audit_numbers.json). Sentences
  that may be quoted (backtest, clean-run statistics, portfolio) are generated into `card` fields, never typed.
- **What the clause does not prove is said first.** Not abatement, not a clean site, not legal compliance, not a payment
  instrument, prototype keys. A qualified clean pass is "observed, not abated". Non-detection never cancels a rebate
  or counts as repair; it can release a capped refund that the next attested detection cancels.
- **Every rule is public before evidence exists.** The policy (thresholds, windows, tiers, floors) is hashed into the
  covenant at creation (`policyHash`) and into the pool at opening (`paramsHash`); the join criterion is frozen in
  [`pipeline/join/CRITERION.md`](../pipeline/join/CRITERION.md).
- **AI assistance.** This prototype was built by one student with AI coding assistants (Anthropic Claude models) that
  wrote most of the code, tests and documentation under the author's direction and review. Every design decision,
  every deviation from the specification and every quoted number goes through a script, a test or a written review
  in this repository; reviewer agents were run on results before they were quoted, and a figure that did not survive
  review was withdrawn rather than reworded.

## Accountability (who can do what; no hidden admin powers)

- No contract has an owner, an upgrade path or a pause; the registry's setup admin is renounced (the zero address in
  [`contracts/deployments/11155111.json`](../contracts/deployments/11155111.json)). The full role × function matrix
  is in [SECURITY.md section 10](SECURITY.md#10-access-control-matrix).
- Observers are named organisations with registered keys; their signatures are on chain, so every signature is
  attributable to an organisation. Observers post no bond in this version: when a signed record is objectively wrong,
  the challenger wins the bond of whoever submitted it (the proposer's bond for a detection, the submitter's pass bond
  for a clean pass), the record is voided, and the organisation's key can be revoked or, on `KeyRegistryV2`, the
  organisation removed by vote. A scientific dispute (for example, attribution to a neighbouring facility) goes to
  `contest`, which holds a payment but never slashes.
- Mistakes are corrected by new records, never by edits: a re-quantified plume is a new attestation; a later detection
  opens a linked episode; a superseded credit note links the old one. The history stays readable.
- Every waiting state ends through a permissionless call after its deadline, so no party can leave a case hanging.

## Data privacy and governance (KVKK / GDPR)

- **No personal data collected or written on chain.** The chain holds hashes, facility identifiers, rates, timestamps,
  amounts and pseudonymous organisation keys; the one identifier a user can add is their own wallet address as a judge
  observer (pseudonymous; [`SECURITY.md`](SECURITY.md) §14). The sources are industrial facilities, not people.
- **Raw data stays with its providers.** Raw responses are never committed; only derived fields and their SHA-256 are
  kept, under the providers' terms ([`data/derived/DATA_LICENSE.md`](../data/derived/DATA_LICENSE.md)).
- **Correction path:** append-only (above). **Erasure path:** derived files can be deleted off chain; on-chain values
  are hashes and identifiers of non-personal data. If a provider withdraws a record, the bundle hash still shows what
  was received and when, and `make reproduce` reports the provider change as a provider change.
- **Licence:** this is a non-commercial research prototype; a production clause that settles real payments on Carbon
  Mapper data would need a commercial data agreement with Carbon Mapper, held by the observers.

## Bias and limits of the AI component

- **The model lost; the product uses a rule.** A boosted model was measured against the frozen persistence rule on
  region- and time-held-out sets and failed the pre-registered decision rule
  ([`pipeline/model/MODEL_CARD.md`](../pipeline/model/MODEL_CARD.md); `model.decision = RULE_IS_ENOUGH` in
  `data/derived/audit_numbers.json`). The forecaster only sizes the bond; it never triggers a payment.
- **The hard rule's blind spot is the problem MARS shows.** A six-month persistence rule forecasts zero after a quiet
  half year; on the held-out sets 7.2 % (test_a) and 9.2 % (test_b) of those zero forecasts were followed by a detection
  (`false_zero_rate` of `B1_persistence` in `data/derived/audit_numbers.json`). That is why bonds are sized with the smoothed rule.

## Bias of satellite coverage

- **Who is observed is not random.** Carbon Mapper tasks passes partly toward expected plumes, and coverage differs by
  region and instrument. A seller in a well-observed basin faces more possible rebates than one in a rarely observed
  basin, and a seller with more clean passes can earn refunds faster. The covenant names its sources, instruments and
  floors in the hashed policy, so the buyer sees exactly what is watched; the clause never treats an unobserved source
  as a clean one.
- **Detection floors.** A source below an instrument's floor is invisible to both rebate and refund. Floors are cited
  per instrument in [`data/derived/floors.json`](../data/derived/floors.json) and used conservatively (the higher end
  of each published range).
- **Selection in every projection.** The backtest set is "the 1,122 sources MARS labels absent today — a label decided
  after the window"; every generated sentence names it (`data/derived/backtest.json` `selection`).
- **A quiet run is weak evidence.** The catalogue figure in `data/derived/cleanruns.json` `card.sentence` is the reason the refund
  is capped at the rebate, paid in installments and cancelled by the next attested detection.

## Inclusivity

- **Where crypto payment is not lawful, the verdict still works.** A verdict-only covenant records the same confirmed
  episode with no on-chain money and exports a credit note to settle "under clause X of the supply contract by bank
  transfer or credit note; this is not a payment instrument" (a Türkiye-type importer [SIM]; not legal advice).
- **New observers can join.** An open observer slot in the judge sandbox lets a wallet we do not hold register and sign;
  `KeyRegistryV2` (local chain) admits a new satellite operator by majority vote of existing members.
- **Running it needs no account.** All data sources are keyless; `make demo` runs offline on a local chain; the page
  reads the chain without a wallet.

## Environmental impact

Proof-of-stake chains only; no proof of work. The compute footprint and gas per function are in
[`docs/FOOTPRINT.md`](FOOTPRINT.md). The tonnage the clause covers is a stake, not abatement.

## Stakeholders and long-term value

| Stakeholder | What they get | What they must not read into it |
|---|---|---|
| Gas importer (the Monday user) | an executable remedy for an attested re-detection at a contracted source, and an IEA Annex D record to file | a statement of regulatory compliance |
| Seller / exporter | a capped refund for attested quiet after a confirmed episode; a visible commitment signal | a certificate that a site is clean |
| Observer organisations | a fee per counted clean pass [SIM amount] | that their signature vouches for the sensor |
| Lender or programme (sponsor) | an event-level, source-level KPI and a pool it can fund | an emissions-reduction claim |
| Regulators and MARS | a record of who reacted to an alert and how; MARS vocabulary is cited, never filled in | an official MARS field |
| People near the sources | more counterparties with a financial reason to react to a super-emitter | a measured change in exposure |

Long-term value: the code is MIT; the data stays under its providers' terms; a production deployment needs
independent observer organisations with their own keys, a commercial data agreement, and per-covenant observer sets.

## Real vs simulated ([SIM]) disclosure

Real: the plumes, the MARS labels, the join, the scene checks, the signatures, the contract executions on Anvil and
Ethereum Sepolia. Simulated [SIM]: seller, buyer, sponsor and surety; in this prototype the observer organisations are
run by one person, and the judge's own wallet is the one signer we do not control; testnet money; backdated covenant
starts (recorded on chain as `createdAt`); the DZA replay's pinned clock; the seeded bad clean pass; the Annex C
seller claims; the counterfactual backtest and portfolio.

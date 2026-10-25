# Pipeline: backtest, portfolio set, data quality

Every number here is a script output with its input hashes (`generatedFrom`) and lands in `data/derived/audit_numbers.json`
(`STRICT=1 make audit`). Offline targets: `make cleanruns floors policy-v2 data-quality exports backtest`.

## Backtest (`backtest.py` → `data/derived/backtest.json`; spec §16)

*Counterfactual: if these sources had been under the clause; no clause existed.* The set is **the 1,122 sources MARS
labels absent today — a label decided after the window**; every summary sentence says so. Amounts are written in
**policy units** (tier amounts are a parameter, not a price), never a currency. The exact definition is in the file
(`definition`); the only sentences that may be quoted are in `data/derived/backtest.json` → `card`, generated from
the numbers after an adversarial review of the backtest:

- `episodesSentence` — episodes, facilities, tiers, refused and linked detections, rebates in policy units;
- `refundsSentence` — "Counterfactual (no clause existed)": refunds on qualified passes only (cloud flag, origin
  intersect without look-ahead, per-source query and no plume within 1.5 km, MARS ±7 d, 60-day publication lag),
  counted where scene data was fetched (below). It names both selections (the join candidates inside the MARS-absent
  set), the passes and scenes, released / cancelled / pending installments, the released claims that saw a plume the
  clause does not count as a relapse, and the claims that were not fetched;
- `stakeSentence` — "stake, not abatement — ~X Mt CH₄/yr, the low bound of the band, … assuming what satellites saw on
  their passes holds between them", with the tasking bias named and the measurement that removes it (roadmap: a duty
  cycle measured on un-targeted EMIT passes); the duty-cycle concentration stays in `stake_band.dutyCycleConcentration`,
  off the card; mid and high stay in the JSON as sums of
  per-facility bounds, not quantiles, and are not quoted;
- `l2Sentence` — L2 counts only MARS plumes from satellites other than EMIT and Tanager, the start row excluded;
- `l1Sentence` — L1 is a cadence badge, not corroboration quality.

The upper-bound refund variant (cloud and footprint unchecked) stays in the JSON only: not an estimate, never quoted.
So does `qualified_conservative` (an own source's plume with a hidden rate also ends a claim). `scene_data` in the same
file carries `releasedWithPlumeSeen` (each claim with its plume ids and reasons), `concentration` (top claims' share
of released units, scenes counted at two facilities), `publicationLag` and `byCountry` (the country mix follows the
join selection). `economicSentence` quotes the IEA's abatement-cost figure (IEA's term) from
`data/derived/projection_sources.json` as context, not a comparison; tier amounts stay policy units.

### Scene data for the qualified variant (`scene_records.py` → `data/derived/scenes_v1/`)

What it covers: every facility of the frozen join (`data/derived/join_v1.json` candidates) and the three demo cases
**where the backtest opens at least one claim**; counts and fetch statistics are in `data/derived/scenes_v1/INDEX.json`,
and the claims without scene data (with the reason) in `data/derived/backtest.json` →
`totals.qualified.claimsWithoutSceneData`. Per facility, keyless and sequential (`make scenes-v1`; raw responses cached
locally under the gitignored raw-data folder, resumable, every request logged):

- one `/catalog/search/source/{name}` per owned Carbon Mapper source (join rule 3) — the per-source query of rule 3 and
  the plume-origin points (plumes within 1.5 km of the facility, each with its earliest `published_at`);
- one `/catalog/scenes/annotated?intersects=<Point>` per origin point, EMIT and Tanager only (the instruments with a
  floor), from the facility's first episode date to the catalogue end — the footprint test of rule 2 point by point,
  so "published by the attestation time" (no look-ahead) is decided per point, never undetermined;
- one `/catalog/plumes/annotated?bbox=` around the facility — every plume (any gas) whose origin lies within 1.5 km, the
  stricter half of rule 3.

**Re-running.** `scene_records.py` needs the raw cache (about 70 MB, gitignored and never exported: the provider
responses carry signed Carbon Mapper URLs). A judge does not need it: `make backtest` (`backtest.py`) re-runs the
qualified walk on the committed `data/derived/scenes_v1/`, whose files `scenes_v1/INDEX.json` hashes; no committed file
carries a signed URL (a test checks).

The committed file keeps, per scene, only what `cleanpass_rules.qualify` reads (time, publication, instrument, cloud
fields and flag, origin points inside, per-source plume ids, nearby plume ids) plus `sceneRecordSha256`, and per facility
the CH4/CO2 plumes within 1.5 km (`nearPlumes`: rate or hidden, owned source or a neighbour's) and the content SHA-256 of
every response. Tests and `make check` read only these files. The backtest runs `qualify` once
per claim on them (since, R and the MARS rows of that claim; rule 2 without look-ahead) and counts a pass at
max(scene + 60 d, scene publication) + 1 d. Not covered: the 1,122 targets outside the join candidates and demo cases
(their claims count nothing), airborne instruments (no floor), scenes on dates that are not in the facility's
observation timeline (the walk iterates the timeline), and anything published after the fetch. Rules 3 and 4 use today's
catalogue and MARS snapshot, which can only refuse more than an observer at the time would have.

### Decisions that differ from the spec text

- **Two clean-pass variants.** `qualified` counts passes that pass spec §12 rules 1–6 on committed scene records
  (`scenes_v1/`, above), keyed by claim; every facility outside the fetch scope is "unqualified — scene data not
  fetched" (0 counted). `upper_bound` counts every detection-free pass whose instrument has a floor ≤ the episode rate
  and no MARS plume within ±7 days; cloud and footprint are not checked, so it is an upper bound on refunds, labelled
  "not an estimate" and kept in the JSON only. The clean-pass MARS refusal and the corroboration use any MARS row; L2
  uses only other satellites.
- **Scene-level rule 3 matches a source's plume by scene id, or by date and instrument when a plume has no scene id**
  (as in `clean_passes.py`); the nearby-plume half counts plumes of any gas within 1.5 km (stricter than written).
- **A relapse takes effect on its detection date** (in reality: after publication and the core's windows): this errs
  toward fewer refunds and more cancellations. **Only a quantified plume ≥ 100 kg/h from an owned source is a relapse**:
  a plume at the facility with a hidden rate, below 100 kg/h, or from a neighbour's source does not cancel, which errs
  toward more refunds (`releasedWithPlumeSeen`; `qualified_conservative` shows the other reading).
- **Start = the first MARS plume's tile date (a backdated start)**: MARS publishes a plume after its tile date, so a
  covenant could not have been written on that day.
- **Detection at a facility is plume-level** (a plume whose origin is within 1.5 km of the target, as in the join), not
  a source's detection date.
- **Rebates are not clipped by a bond** (policy units, as a VERDICT_ONLY covenant).
- **The stake band is a sum of per-facility v1 bands** (low / mid / high), not a joint interval; "low" is the lower
  bound. GWP-100 and GWP-20 (fossil CH₄) are read: GWP-20 from IPCC AR6 WG1 Chapter 7 Table 7.15 (p. 1017), GWP-100
  from the GHG Protocol AR6 table and checked against the same IPCC table (`floors.py`; `make floors-ipcc PDF=…`
  re-reads the table from the chapter PDF).
- **Economic sentence is context, not a comparison.** The IEA's figure is quoted (read at the URL, [V]); the tier
  amounts are not converted to a currency.

## Portfolio set (`sourceset.py v3` → `data/derived/sourceset_v3.json`) and its sizing (`model/bond.py --set v3`)

Leaves: the join v1 candidates — each chosen because it showed a Carbon Mapper plume that MARS did not see — plus the
other demo cases (205 facilities). Linked Carbon Mapper sources follow the join's ownership rule (rule 3: each source
belongs to its nearest target within 1.5 km), so no source is linked to two facilities. The quotable sentence is
`data/derived/model/bond_sizing_v3.json` → `card.portfolioSentence` (201 of 205 sized; the four leaves without a Carbon Mapper source are
named and cannot open an episode) [SIM].

### Decisions that differ from the spec text

- **v3 links differ from v2's for the demo cases**: v2 listed each case's sources by hand (TKM kept a 1000 m source
  1.77 km away; DZA listed two of four 250 m / 1000 m sources); v3 applies the ownership rule to every facility. v2,
  the Sepolia main covenant's set, is unchanged.
- **Four join candidates have no linked source in v3** (ARG_S_055, TKM_S_395, TKM_S_426, USA_S_053): their join plumes
  came from a source owned by a nearer target (join rules 3 and 5 differ there). Keeping sources disjoint wins; the
  backtest marks them "no Carbon Mapper source owned".
- **Sizing uses B1s**, the product forecaster (the model failed the frozen decision rule and `model.pkl` is not
  committed); bond = P90 of the set's annual rebate exposure as of the catalogue end; **pool = the same P90**, because
  a refund is capped at the rebate its episode cost. Testnet-sized amounts [SIM].

## Carbon projection (`projection.py` → `data/derived/projection.json`)

*[PROJECTION] of what the clause could change if adopted, not a measured reduction.* Per scope (the portfolio set v3
[SIM]; every backtest facility with a quantified stake) and response scenario: Σ over quantified facilities of stake
low bound × the fixed share of its emitting time (option d of `model.t4d`: the action share by CARB plume count × the days
a fix is credited, from the fix to the fault's observed end, the last pass or a new fault, over the stake's own
emitting days). `projection.json` → `headline.kind` is `"central"` (the measured re-onset hazard, with the
no-recurrence end) and `t4d` holds the model, its sensitivity, the post-result and post-hoc rows and the diagnostics;
the reviewed interim range (stake × (scenario − MARS reply share) × one of two stop-share ends) is kept in `interim`, and
setting `model.timeFactor.headlineKind` to `"range"` publishes it again. Every parameter, its source (tag [V] read /
[S] secondary) and its status (read, ASSUMPTION, DESIGN) is in `data/derived/projection_sources.json`; the scripts type
no parameter value (a test checks the literals). Quotable sentences: `data/derived/projection.json` → `card` (projection,
scenarios, wider set, floor, ledger cost per tonne, adoption, the MARS negative finding, the two empirical checks).
`make projection` is offline; it reads three small committed derivatives:

- `data/derived/carb_summary.json` (`fetch_published.py --write-summary`, network, ask first): the aggregates of the
  oil and gas rows of CARB's Appendix Tables A1/A2 (counts by year, outcome and plume count, the join-key count, the
  Type A delays, the rows' RFC 8785 hash); the projection refuses to run unless they reproduce Table 3's 2021 and 2023
  rows. The per-incidence rows are not tracked: `make fetch-published` rebuilds them from the publisher's PDF as
  `data/derived/carb_incidences.json` (git-ignored) and checks that they reproduce the summary exactly;

- `data/derived/mars_notified.json` (`make mars-notified`; needs the MANIFEST's MARS zip in the gitignored raw-data
  folder): the tile date of each source's first notified plume, the proxy for the notification date that MARS does not
  publish;
- `data/derived/cm_timelines_notified.json` (`make cm-notified RAW_DIR=…`, network, ask first: one keyless Carbon
  Mapper request per missing source): timelines for the notified sources outside the join neighbourhood, so the event
  study is not conditioned on today's "absent" label. `data/derived/cm_timelines.json` and every backtest input are
  untouched.

`make projection` is offline and safe to run any time on the committed files, but it is **not part of `make
reproduce`**: reproduce re-fetches MARS and Carbon Mapper, while the projection's other inputs (the backtest, the
first-notified dates and the extra timelines above) are rebuilt by their own targets, so after a re-fetch run
`make backtest mars-notified projection` (and `cm-notified`, network, if MARS notified new sources); the projection
refuses a notified source that has no first-notified date rather than guessing one.

### Decisions that differ from the spec text

- **Projection review (spec §20, 2026-10-16).** The lead is the floor, then the scenario band, then the null. The
  stop share is an ASSUMPTION informed by Ravikumar et al. 2020 and the IEA ceiling; "acted on" replaces "answered"
  (MARS's rate counts replies). The evidence-capped figure was withdrawn: a detection-rate difference cannot bound a
  share of emitted mass.
- **The action share follows a written rule, chosen after reading Table 3 as the lowest of the definitions in
  projection.json → responseSensitivity (2026-10-20).** The scenarios were 0.3 / 0.5 / 0.7 with no source.
  They are now the share of notified oil and gas incidences that were fixable faults unknown to the operator (CARB
  Type A, every one stopped or repaired) in the California Air Resources Board's 2020, 2021 and 2023 airborne campaigns
  (Table 3, every year kept) and its Wilson interval at the rule's own confidence, two decimals; `responseScenarios.rule`
  holds the table rows and the alternative definitions, `projection.py` recomputes the values and refuses to run if
  the file's values differ or the rule stops being the lowest definition, and `make fetch-published` (network, ask
  first) re-reads the table from the publisher's PDF. Still an ASSUMPTION: the interval is sampling uncertainty over
  California incidences and does not cover the move from a regulator's notice to a rebate under a contract; the note
  says which way each difference pushes. Cost: the central projection moves down with the central action share.
- **Each factor has one meaning (2026-10-20; pre-registered in `projection_sources.json` → `model`,
  committed alone before any new value was computed).** A review found the earlier model removed the same emissions twice:
  CARB's Type A share already leaves out what a repair cannot touch (allowed operation, maintenance, not found), and
  Ravikumar et al.'s 44 % "total emissions reduced" leaves it out again; CARB also counts incidences while the stake is
  mass. The model is now stake × (r − b) × d: r = the share of notified plume sources that were fixable faults, all
  fixed (CARB Type A, the only factor that removes what a repair cannot touch); b = today's MARS reply share,
  subtracted from r only; d = the share of a fixed source's emissions that stops, measured on leaks at a re-survey
  (Ravikumar et al. 2020). Chosen by a written criterion (each emission removed by one factor,
  each factor measured on the population it multiplies), not by size: the alternative pairing (answer share × 44 %)
  projects more but its 44 % is measured on total ground-survey emissions and is mostly a vent change "despite
  near-zero repair". Mass weighting is not possible from published data: CARB's per-incidence Appendix Tables A1/A2
  carry no emission rate, location or plume identifier, and Carbon Mapper's 2023 plume list carries no incidence
  identifier; r stays counted by incidence, the rows are committed and re-checked, and a CARB crosswalk from incidence
  to plume identifier (or a public-records request for it) would make the mass-weighted share computable.
- **Interim range after the review of that model (2026-10-21, adversarial review HOLD; amendment recorded in
  `projection_sources.json` → `model.status`).** The review found its value credited a whole year's mass per fix
  with nothing for new faults, the emissions before detection and during repair, or sources that stop on their own,
  and that 0.90 is Ravikumar et al.'s repair rate, not a durability. The criterion gains clause (3), "the second
  factor's time base is a year at the stake's sources"; no option meets it yet, so the headline is the range between
  option (c), Type A share × 22 % (one repair round net of new leaks, added to the menu), and option (b), Type A share
  × 90 % (the repair rate with fixed leaks not re-appearing, read as every fix holding all year), at the central action
  share. b bounds reply-driven fixes only. `classes.list` names the emission classes and `projection.py` refuses to
  run unless each is removed by exactly one factor or spanned by the range. The CARB appendix's notification and
  mitigation dates are committed for the 48 oil and gas Type A rows, for the time factor (`model.t4c`). Cost: no
  single central tonnage until a time factor; the portfolio's headline is a fourfold range.
- **The time factor, unpublished until a review passes (`model.t4c` and `model.t4d`, 2026-10-21).** The first time factor (`model.t4c`)
  (Cusworth event bounds on the stake's own passes) was held by its review: its "events" were pass gaps, runs where
  every pass saw a plume merged later faults into the first fix, and its band ran the wrong way. The second (`model.t4d`, pre-registered
  in `projection_sources.json` → `model.t4d`, committed before the implementation commit) counts removed time in
  absolute days against the stake's own emitting days (duty cycle × days to the last pass), censors every event at
  the facility's last pass, credits a fix only until a new fault would start (the re-onset hazard measured on the
  stake's own passes), conditions the action share on persistence (CARB plume count), and keeps b per event. Its
  result sits in `projection.json` → `t4d`; while `model.timeFactor.headlineKind` is `"range"` the published text keeps
  the reviewed interim range. After the review of `model.t4d` (HOLD, unblock path) option d is the choice as first registered;
  option e (registered after d's result was known: d plus the natural-stop hazard from a seeded sample of 400 Carbon
  Mapper oil and gas sources farther than 1.5 km from every MARS notification, the "not MARS-notified" sample, US
  airborne-heavy) is a labelled post-result row, as are the single-miss-merged hazard and the event-length
  denominator. Under headline kind "central" the headline credits each member over its window since MARS's first plume there, as if the
  covenant had started then; a forward hold-out is future work.
- **The baseline is the most recent published MARS response share** (`baselineResponseShare.rule`; `projection.py`
  re-reads the figure from the latest candidate's quote). Subtracting a reply rate from an outcome rate is
  conservative: MARS outcomes are far rarer than its replies.
- **The empirical checks do not set the share that stops.** MARS documents a per-source `emission_status`, but neither
  the committed snapshot nor the current public file (checked 2026-10-15, hash in `data/derived/projection_sources.json`) carries
  it, so line 1 uses the `absent` persistency label as a non-detection fallback. Line 2 (difference in differences of
  Carbon Mapper detection rates around a proxy date) is a null at its minimum detectable effect; it neither supports
  nor bounds the assumed band.
- **Ledger cost counts the core episode path only** (`proposeEpisode`, `finalize`, `withdraw`, one `createCovenant`)
  and excludes the cost of posting the bond;
  refund-side IncentivePool calls are not included.

## Demo case counts (`cases.py` → `data/derived/cases/*.json`)

### Decisions that differ from the spec text

- **Facility-radius count basis (2026-10-17, user-approved).** The spec text quotes the lead case's passes after MARS's
  last plume as a union over every linked Carbon Mapper source. The headline (`post_mars_last_plume`) now counts only
  linked sources whose centre is within `RADIUS_KM` of the MARS point, and only on dates in those sources' own
  observation lists (`facility_post`). Why: on one TKM_S_276 date the only detection came from a linked source centred
  outside the radius, with plumes further out, on a date the facility's own sources did not observe. Cost: the lead
  case's count and the stake band's duty-cycle lower bound drop (numbers in `data/derived/audit_numbers.json` → `cases`,
  `stake_band`). The union stays in each case file as `post_mars_last_plume_all_linked`, labelled secondary.
- **The covenant policy hashes a frozen snapshot.** `snapshotManifestHash` in the v1 covenant policy
  (`observer/demo.py`) is the SHA-256 of `data/derived/cases/sepolia_v1/TKM_S_276.json`, the exact bytes the live
  Sepolia covenant committed to, not of the current case file; otherwise any regeneration of the case file would stop
  the on-chain `policyHash` from reproducing. Cost: one duplicated historical file, with a README saying what differs.

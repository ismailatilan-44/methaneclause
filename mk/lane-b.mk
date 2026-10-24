# Lane B fragment (spec-v2 §11; owned by lane B). Declare targets with a ## comment and list them here:
.PHONY: cleanruns clean-passes floors floors-ipcc policy-v2 lane-b-numbers cleanpass exports data-quality backtest \
        projection fetch-published mars-notified cm-notified cm-not-mars-notified scenes-v1 sepolia-txs scale clause

cleanruns:        ## clean runs after a 2nd detection + u(N) table -> data/derived/cleanruns.json (offline)
	$(RUN) cleanruns.py

clean-passes:     ## DZA/TKM/USA candidate clean passes, rules 1-6 -> cases/*.clean_passes.json (cached; keyless CM on a cache miss)
	$(RUN) clean_passes.py

floors:           ## instrument floors + GWP with citations and status -> data/derived/floors.json (offline)
	$(RUN) floors.py

policy-v2:        ## POLICY_V2 / replay / sandbox hashes + onchainParamsHash -> data/derived/policy_v2.json (offline)
	$(RUN) policy.py --write

lane-b-numbers: cleanruns floors policy-v2 data-quality   ## lane B step-1 numbers that need no network (clean-passes uses its cache)
	OFFLINE=1 $(RUN) clean_passes.py

cleanpass:        ## clean-pass observer, five steps, on DZA's 2025-02-12 EMIT pass (offline; LIVE=1 re-checks against Carbon Mapper)
	$(RUN) -m observer.cleanpass --case DZA_S_012 --scene "2025-02-12 emi" --org OBS_A

exports:          ## Annex D (+ euEvidence, MARS vocabulary) for TKM and the [SIM] Annex C examples -> data/derived/export/ (offline)
	$(RUN) -m export.iea_annex_d --source TKM_S_276
	$(RUN) -m export.annex_c --case DZA_S_012 --example
	$(RUN) -m export.annex_c --case TKM_S_276 --example

data-quality:     ## plume quality flags + join radius sensitivity 1.0/1.5/2.0 km -> data/derived/data_quality.json (offline)
	$(RUN) data_quality.py

backtest:         ## prospective backtest over the 1,122 MARS targets + portfolio set v3 + its bond / pool sizing (offline)
	$(RUN) backtest.py
	$(RUN) sourceset.py v3
	$(RUN) model/bond.py --set v3

projection:       ## [PROJECTION] what the clause could change if adopted, stake low bound as the floor -> data/derived/projection.json (offline)
	$(RUN) projection.py

fetch-published:  ## NETWORK (ask first): download the CARB report and appendix behind the projection's action share, check their SHA-256 and that Table 3's oil and gas rows equal responseScenarios.rule; rebuild the per-incidence rows of Tables A1/A2 as data/derived/carb_incidences.json (local, git-ignored) and check they reproduce the committed data/derived/carb_summary.json (needs pdftotext; writes data/raw/published/ and that one ignored file)
	$(RUN) fetch_published.py --write

mars-notified:    ## first notified MARS plume per O&G source (the projection's proxy date) -> data/derived/mars_notified.json (needs data/raw/mars/mars.zip = the MANIFEST snapshot)
	$(RUN) mars_notified.py

cm-notified:      ## NETWORK (ask first): Carbon Mapper timelines for notified sources beyond the join neighbourhood -> data/derived/cm_timelines_notified.json (RAW_DIR=... required)
	$(RUN) fetch_cm_notified.py --raw-dir $(RAW_DIR)

cm-not-mars-notified: ## NETWORK (ask first): Carbon Mapper timelines for the pre-registered not MARS-notified sample (400 sources, seeded) -> data/derived/cm_timelines_not_mars_notified.json (RAW_DIR=... required)
	$(RUN) fetch_cm_not_mars_notified.py --raw-dir $(RAW_DIR)

scenes-v1:        ## Carbon Mapper scene records for every backtest claim at the join candidates + demo cases -> data/derived/scenes_v1/ (keyless CM on a cache miss, ~2,000 polite requests; OFFLINE=1 = cache only)
	$(RUN) scene_records.py

floors-ipcc:      ## re-read IPCC AR6 WG1 Table 7.15 (CH4 GWP-20 / GWP-100) from the chapter PDF: make floors-ipcc PDF=/path/IPCC_AR6_WGI_Chapter07.pdf
	$(RUN) floors.py --check-ipcc "$(PDF)"

sepolia-txs:      ## NETWORK, keyless: every Sepolia tx our addresses sent, from a public explorer index + public RPC, nonce-checked -> data/derived/sepolia_txs.json (make measure then runs from it)
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	$(RUN) sepolia_txs.py

scale:            ## gas vs facility-set size (3 / 205 / 1,122 / 10,000 [SIM padding]): real txs on its own Anvil (ANVIL_PORT, default 8961) -> data/derived/scale.json (offline)
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	$(RUN) -m observer.scale --port $(or $(ANVIL_PORT),8961)

clause:           ## model contract clause: render docs/clause/model-clause.md values from policy_v2.json (offline; the PDF blocks render with make readme)
	$(RUN) clause.py --write

# Lane C fragment (spec-v2 §11; owned by lane C). Declare targets with a ## comment and list them here:
.PHONY: readme judge-cure app-scenes measure-verification measure-verification-manual test-lane-c e2e-setup e2e pages pages-e2e wallet-data wallet-setup wallet-local

judge-cure:       ## judge sandbox [SIM] on Sepolia (JUDGE_PK), after judge-finalize: real DZA relapse -> claim -> real 2026-07-13 pass signed by your key; rerun after each 10-minute window
	./scripts/judge.sh cure

app-scenes:       ## network, keyless: Carbon Mapper scene ids for the ledger cards' CH4 tile layer -> app/scene_ids.json (then make app)
	pipeline/.venv/bin/python scripts/build_app_data.py --resolve-scenes

measure-verification:  ## W2 tool path timed n=5 in fresh processes (offline snapshot; LIVE=1 fetches Carbon Mapper) -> data/derived/verification_time.json
	pipeline/.venv/bin/python scripts/measure_verification.py tool --n 5 $(if $(filter 1,$(LIVE)),--live,)

measure-verification-manual:  ## W2 manual protocol, timed step by step by the author (interactive; one run per call, n >= 5)
	pipeline/.venv/bin/python scripts/measure_verification.py manual

test-lane-c:      ## lane C offline tests: build_app_data fail-closed paths, forward-only demo clock, wallet.json current (scripts/tests)
	pipeline/.venv/bin/python -m pytest -q scripts/tests

# Browser e2e: network for the pinned CDN modules, so not in make check. Playwright lives in out/e2e-tools (gitignored);
# it drives the installed Google Chrome (E2E_CHANNEL= uses Playwright's Chromium after `npx playwright install chromium`).
E2E_PLAYWRIGHT ?= 1.63.0
e2e-setup:        ## dev tooling for make e2e: Playwright $(E2E_PLAYWRIGHT) into out/e2e-tools (not committed)
	mkdir -p out/e2e-tools && cd out/e2e-tools && ( [ -f package.json ] || npm init -y >/dev/null ) && npm install --no-audit --no-fund playwright@$(E2E_PLAYWRIGHT)

e2e:              ## one-file browser e2e of the page: first view, story chapters + their txs, 0 failed self-checks, three tampers red + reset, skip-window, witness panel, 375 px
	node scripts/tests/e2e.mjs

# Hosted page: a static copy of app/ for any static host (relative paths; live Sepolia read through the public RPC;
# pinned CDN modules). Builds and checks only; publishing it is a separate, approved step.
pages:            ## static export of the page -> out/pages/ (no server code; fails closed on a missing file, a root-relative path, an unpinned CDN or an internal label); not published
	python3 -I scripts/build_pages.py out/pages

pages-e2e: pages  ## make e2e against the static export in out/pages/ (network for the CDN modules)
	E2E_ROOT=out/pages node scripts/tests/e2e.mjs

LANE_CHECKS += test-lane-c

readme:           ## render README.md's generated blocks from committed files (runs make test for the counts; <= 25 KB)
	pipeline/.venv/bin/python scripts/build_app_data.py --readme

# Browser-wallet back end (app/wallet.js): its data file, and its end-to-end test on a local Anvil judge sandbox.
# The test maps wallet.js's pinned CDN imports to the same viem version in out/e2e-tools and signs with MetaMask's own
# library, so it runs offline once wallet-setup has installed them; it is not in LANE_CHECKS because a fresh clone has
# no out/e2e-tools (npm needs the network once).
WALLET_VIEM ?= 2.57.1
WALLET_SIG_UTIL ?= 8.2.0
wallet-data:      ## app/data/wallet.json for app/wallet.js: sandbox record, observer bundle templates, parity vector, ABIs (offline; after forge build)
	pipeline/.venv/bin/python scripts/build_wallet_data.py

wallet-setup:     ## dev tooling for make wallet-local: viem $(WALLET_VIEM) + @metamask/eth-sig-util $(WALLET_SIG_UTIL) into out/e2e-tools (not committed)
	mkdir -p out/e2e-tools && cd out/e2e-tools && ( [ -f package.json ] || npm init -y >/dev/null ) && npm install --no-audit --no-fund viem@$(WALLET_VIEM) @metamask/eth-sig-util@$(WALLET_SIG_UTIL)

wallet-local:     ## app/wallet.js end to end on Anvil (WALLET_PORT, default ANVIL_PORT or 8548): parity, join, sign, episode, tamper, finalize, cure, status
	./scripts/wallet_local.sh

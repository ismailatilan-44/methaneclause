# MethaneClause — reproducible pipeline. Every number in README/PDF comes from `make reproduce`.
RUN     := cd pipeline && .venv/bin/python

.PHONY: help setup fetch join cases sourceset baseline model bond stake audit audit-strict reproduce test test-pipeline test-contracts demo deploy-base deploy-sepolia deploy-sandbox-sepolia testnet-run testnet-finalize fund-judge judge-attest judge-propose judge-tamper judge-challenge judge-finalize judge-status judge-reset sandbox-rekey judge-local measure adversarial export verify-bundle app app-preview check words links tree secrets core-bytecode

help:             ## list targets (from the double-hash comments)
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | sort

setup:            ## Python 3.12 venv with pinned deps (needs uv)
	uv venv pipeline/.venv --python 3.12
	VIRTUAL_ENV=pipeline/.venv uv pip install -r pipeline/requirements.txt

fetch:            ## MARS zip + Carbon Mapper CSV and ~2,000 source timelines (cached in data/raw; REFRESH=1 re-downloads)
	$(RUN) fetch_mars.py
	$(RUN) fetch_cm.py

join:             ## frozen criterion v1 -> data/derived/join_v1.json
	$(RUN) join/join.py

cases:            ## demo case files, Carbon Mapper fetched live (OFFLINE=1 uses the cache)
	$(RUN) cases.py

sourceset:        ## covered-facility Merkle root + proofs (SigLib format) -> data/derived/sourceset_v2.json
	$(RUN) sourceset.py

baseline:         ## rule baselines (persistence ratio B1, MARS-label rule B2) on frozen splits -> data/derived/model/
	$(RUN) model/baseline.py

model:            ## HistGradientBoosting next-pass forecast vs the frozen persistence rule B1 -> data/derived/model/model_metrics.json
	$(RUN) model/train.py

bond:             ## bond sizing from the kept forecaster + 2026 backtest -> data/derived/model/bond_sizing.json
	$(RUN) model/bond.py

stake:            ## lead-case stake band (not abatement) -> data/derived/stake_band.json
	$(RUN) stake.py

audit:            ## collect every quoted number -> data/derived/audit_numbers.json and diff against the last run
	$(RUN) audit.py

audit-strict:     ## same, exit non-zero on any changed number
	STRICT=1 $(RUN) audit.py

reproduce: fetch join cases sourceset baseline model bond stake audit   ## fetch -> join -> cases -> sourceset -> baseline -> model -> bond -> stake -> audit

test-pipeline:
	cd pipeline && PATH="$$HOME/.foundry/bin:$$PATH" .venv/bin/python -m pytest -q tests

test-contracts:
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge test

test: test-pipeline test-contracts   ## pipeline pytest + Foundry (unit, fuzz, invariants); counts in README "Run it"

check: test demo judge-local adversarial verify-bundle audit-strict words links tree secrets core-bytecode extra-checks   ## the "nothing broke" gate (offline, a few minutes)
	@echo "check: all green"

core-bytecode:    ## the v1 core (MethaneCovenant, KeyRegistry) is unchanged: deployedBytecode hashes vs contracts/deployments/core_bytecode.sha256
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build --silent && cd .. && python3 scripts/core_bytecode.py check

words:            ## product files must not contain the claims we never make (see scripts/check_repo.py FORBIDDEN)
	python3 scripts/check_repo.py words

links:            ## every relative markdown link resolves
	python3 scripts/check_repo.py links

tree:             ## no raw data, .env, model.pkl or private folders tracked; LICENSE + data licence present
	python3 scripts/check_repo.py tree

secrets:          ## gitleaks over git history (skipped with a warning if gitleaks is not installed)
	@if command -v gitleaks >/dev/null 2>&1; then \
	  CFG=""; [ -f .gitleaks.toml ] && CFG="-c .gitleaks.toml"; \
	  gitleaks git --no-banner --redact $$CFG . 2>/dev/null || gitleaks detect --no-banner --redact $$CFG -s .; \
	else echo "secrets: gitleaks not installed, skipped (brew install gitleaks)"; fi

deploy-base:      ## Base Sepolia (84532): loads ./.env (see .env.example), deploys + verifies, writes contracts/deployments/84532.json
	@test -f .env || { echo ".env missing (copy .env.example)"; exit 1; }
	set -a; . ./.env; set +a; cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" \
	  forge script script/Deploy.s.sol --rpc-url "$$BASE_SEPOLIA_RPC_URL" --broadcast --verify -vv

deploy-sepolia:   ## Ethereum Sepolia (11155111): loads ./.env, deploys main + judge sandbox, verifies on Etherscan, writes contracts/deployments/11155111.json
	@test -f .env || { echo ".env missing (copy .env.example)"; exit 1; }
	set -a; . ./.env; set +a; cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" \
	  forge script script/Deploy.s.sol --rpc-url "$$SEPOLIA_RPC_URL" --broadcast --slow --verify -vv \
	  --gas-estimate-multiplier 800 --priority-gas-price 1000000 --with-gas-price 3000000 \
	  && ../pipeline/.venv/bin/python ../pipeline/txlog.py broadcast broadcast/Deploy.s.sol/11155111/run-latest.json

deploy-sandbox-sepolia: ## operator: redeploy only the judge sandbox [SIM] pair on Sepolia (main deployment untouched), verify, update deployments/11155111.json
	@test -f .env || { echo ".env missing (copy .env.example)"; exit 1; }
	set -a; . ./.env; set +a; cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" \
	  forge script script/DeploySandbox.s.sol --rpc-url "$$SEPOLIA_RPC_URL" --broadcast --slow --verify -vv \
	  --gas-estimate-multiplier 800 --priority-gas-price 1000000 --with-gas-price 3000000 \
	  && ../pipeline/.venv/bin/python ../pipeline/txlog.py broadcast broadcast/DeploySandbox.s.sol/11155111/run-latest.json

testnet-run:      ## operator: main covenant on Sepolia, OBS_A+OBS_B sign live, 4 on-chain rejections, real proposal -> data/derived/testnet_run.json
	@test -f .env || { echo ".env missing"; exit 1; }
	set -a; . ./.env; set +a; cd pipeline && RPC_URL="$$SEPOLIA_RPC_URL" .venv/bin/python -m observer.testnet run

testnet-finalize: ## operator: after the 1-day window, finalize -> CONFIRMED, buyer + proposer withdraw
	@test -f .env || { echo ".env missing"; exit 1; }
	set -a; . ./.env; set +a; cd pipeline && RPC_URL="$$SEPOLIA_RPC_URL" .venv/bin/python -m observer.testnet finalize

testnet-multiyear: ## operator: a three-year covenant (POLICY_MULTIYEAR) beside the main one, the main IncentivePool opened for it -> data/derived/multiyear_run.json
	@test -f .env || { echo ".env missing"; exit 1; }
	set -a; . ./.env; set +a; cd pipeline && RPC_URL="$$SEPOLIA_RPC_URL" .venv/bin/python -m observer.testnet multiyear

fund-judge:       ## operator: send AMOUNT (default 0.002) test ETH from the deployer to TO=0x... (a judge test wallet)
	@test -n "$(TO)" || { echo "usage: make fund-judge TO=0x... [AMOUNT=0.002]"; exit 1; }
	@set -a; . ./.env; set +a; cd pipeline && RPC_URL="$$SEPOLIA_RPC_URL" .venv/bin/python -m observer.testnet \
	  fund-judge --to "$(TO)" --amount "$(or $(AMOUNT),0.002)"

sandbox-rekey:    ## operator: someone revoked a published sandbox key -> register its next generation (ROLE=OBS_1|OBS_2|SELLER|BUYER)
	@test -n "$(ROLE)" || { echo "usage: make sandbox-rekey ROLE=OBS_1"; exit 1; }
	@set -a; . ./.env; set +a; cd pipeline && RPC_URL="$$SEPOLIA_RPC_URL" .venv/bin/python -m observer.testnet \
	  sandbox-rekey --role "$(ROLE)"

judge-attest:     ## judge sandbox [SIM] on Sepolia (JUDGE_PK): your wallet joins as observer OBS_JUDGE; your covenant; Carbon Mapper live; signed by you + published observer 1
	scripts/judge.sh attest $(or $(SOURCE),TKM_S_276)

judge-propose:    ## your wallet submits the episode -> PROPOSED (explorer link printed)
	scripts/judge.sh propose

judge-tamper:     ## same bundle, one field edited -> reverted on chain (InvalidSignature), bond unchanged
	scripts/judge.sh tamper

judge-challenge:  ## seeded bogus episode -> you challenge HASH_MISMATCH -> observers WITHDRAW -> VOID -> you get the bond
	scripts/judge.sh challenge

judge-finalize:   ## after the 10-minute window: your episode -> CONFIRMED
	scripts/judge.sh finalize

judge-status:     ## what is on chain for your judge run
	scripts/judge.sh status

judge-reset:      ## forget your local judge run; the next judge-attest opens a fresh sandbox covenant
	scripts/judge.sh reset

adversarial:      ## spec §10 bad-input set: which layer catches each (validation / hash), slow drift -> data/derived/adversarial.json
	$(RUN) -m observer.adversarial

export:           ## IEA Annex D export (official labels; protocol fields under x-methaneclause) -> data/derived/export/
	$(RUN) -m export.iea_annex_d

verify-bundle:    ## both halves of the hybrid signature (ECDSA + ML-DSA-44) on the last demo run + size/time table
	pipeline/.venv/bin/python scripts/verify_bundle.py out/demo/run.json

app:              ## rebuild app/data/ for the one-page UI from data/derived + deployments + the last demo run; then: python3 -m http.server -d app 8080
	pipeline/.venv/bin/python scripts/build_app_data.py

APP_PORT ?= 4173
app-preview:      ## serve the page on http://127.0.0.1:$(APP_PORT)/ (from a copy in /tmp/methaneclause-app, readable by sandboxed previews); Ctrl-C stops
	rsync -a --delete app/ /tmp/methaneclause-app/
	@echo "page:     http://127.0.0.1:$(APP_PORT)/"
	@echo "tamper A: http://127.0.0.1:$(APP_PORT)/?tamper=A   (B, C: the other two tampers)"
	python3 -I -m http.server $(APP_PORT) --bind 127.0.0.1 --directory /tmp/methaneclause-app

measure:          ## gas per function on Sepolia vs Anvil, p50/p90 latency, failed txs, bundle size -> data/derived/measurements.json (network: Chainlink price)
	$(RUN) measure.py && .venv/bin/python readme_testnet.py

judge-local:      ## the judge walk-through end to end on Anvil (no test ETH needed)
	./scripts/judge_local.sh

demo:            ## Anvil end to end, 33 checks: covenant, two observers sign TKM_S_276; tamper / self-attest / disagree / uncovered / replay fail as real txs; rebate paid; retry, revoked key, linked detection (LIVE=1 fetches Carbon Mapper now)
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	./scripts/demo.sh

# Each mk/*.mk owns its own targets and .PHONY line.
-include mk/*.mk

# Fragments add offline test targets with `EXTRA_CHECKS += <target>`; expanded here, after the includes.
.PHONY: extra-checks
extra-checks: $(EXTRA_CHECKS)   ## offline tests contributed by the mk/*.mk fragments (part of make check)
	@echo "extra-checks: ok ($(or $(strip $(EXTRA_CHECKS)),none))"


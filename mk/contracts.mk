# Contracts: static analysis, symbolic proofs and IncentivePool deployment. Each target carries a ## comment for make help.
.PHONY: deploy-pool-dryrun deploy-pool-anvil deploy-pool-sepolia deploy-sandbox-pool-sepolia slither halmos halmos-mutants
EXTRA_CHECKS += deploy-pool-dryrun

slither: ## dev tool, not in make check (needs the slither-analyzer uv tool): Slither over contracts/src -> contracts/slither.md; fails on any finding not triaged in contracts/slither.triage.json
	cd contracts && export PATH="$$HOME/.foundry/bin:$$PATH" && FOUNDRY_DYNAMIC_TEST_LINKING=false \
	  forge build --build-info --out out/slither --cache-path cache/slither --skip './test/**' './script/**' --silent
	cd contracts && rm -f out/slither.json && { PATH="$$HOME/.foundry/bin:$$PATH" slither . --json out/slither.json > out/slither.log 2>&1 || true; }
	cd contracts && python3 -I script/slither_report.py out/slither.json slither.triage.json slither.md "$$(slither --version)"

halmos-mutants: ## dev tool, not in make check (needs the halmos uv tool; a few minutes): each planted fault in contracts/halmos/mutants/ must be caught by its Halmos run
	contracts/halmos/mutants/run.sh

# Halmos compiles contracts/halmos/ as its test directory, into its own out/cache (never the forge test build).
HALMOS_RUN = cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" FOUNDRY_DYNAMIC_TEST_LINKING=false FOUNDRY_TEST=halmos \
  FOUNDRY_OUT=out/halmos FOUNDRY_CACHE_PATH=cache/halmos halmos --forge-build-out out/halmos
HALMOS_PY = $(shell uv tool dir 2>/dev/null)/halmos/bin/python
HALMOS_LOGS = out/halmos-logs

halmos: ## dev tool, not in make check (needs the halmos uv tool; about 16 minutes): symbolic proofs in contracts/halmos/ -> contracts/halmos/REPORT.md
	mkdir -p contracts/$(HALMOS_LOGS)
	rm -f contracts/cache/halmos/solidity-files-cache.json  # Halmos needs the AST: always a full build of its own
	$(HALMOS_RUN) --contract InstallmentArithmeticHalmos --loop 32 > $(HALMOS_LOGS)/pool-installments.log 2>&1 || true
	$(HALMOS_RUN) --contract PoolPropertiesHalmos --loop 4 --invariant-depth 2 > $(HALMOS_LOGS)/pool-any-order-depth-2.log 2>&1 || true
	$(HALMOS_RUN) --contract PoolScenarioHalmos --loop 4 > $(HALMOS_LOGS)/pool-lifecycle.log 2>&1 || true
	$(HALMOS_RUN) --contract CoreI4Halmos --loop 4 --invariant-depth 1 --default-bytes-lengths 65 > $(HALMOS_LOGS)/core-any-order-depth-1.log 2>&1 || true
	$(HALMOS_RUN) --contract CoreScenarioHalmos --loop 4 --default-bytes-lengths 65 > $(HALMOS_LOGS)/core-lifecycle.log 2>&1 || true
	$(HALMOS_RUN) --contract RoundingLemmaHalmos --solver-timeout-assertion 60s > $(HALMOS_LOGS)/rounding-256-bit.log 2>&1 || true
	cd contracts && "$(HALMOS_PY)" -I halmos/rounding_lemma.py > $(HALMOS_LOGS)/rounding-lemma-z3.log 2>&1 || true
	cd contracts && python3 -I halmos/report.py halmos/REPORT.md "$$(halmos --version | awk '{print $$2}')" \
	  $(HALMOS_LOGS)/rounding-lemma-z3.log $(HALMOS_LOGS)/pool-installments.log $(HALMOS_LOGS)/pool-any-order-depth-2.log \
	  $(HALMOS_LOGS)/pool-lifecycle.log $(HALMOS_LOGS)/core-any-order-depth-1.log $(HALMOS_LOGS)/core-lifecycle.log \
	  $(HALMOS_LOGS)/rounding-256-bit.log

SEPOLIA_GAS_FLAGS = --gas-estimate-multiplier 800 --priority-gas-price 1000000 --with-gas-price 3000000
MAIN_COVENANT_ID ?= 0x0000000000000000000000000000000000000000000000000000000000000001
# forge script writes the record before its transactions are confirmed: after a failed run the record may name
# contracts that are not on chain, so it must not be committed.
POOL_RECORD_RESTORE = the deployment did not complete or the record does not match the chain: do not commit it; restore it with git checkout contracts/deployments/11155111.json

deploy-pool-dryrun: ## offline, in make check: DeployPool's operator path on a local Anvil (chain 99911155, public test keys); records checked against deployed bytecode
	ANVIL_PORT=$${ANVIL_PORT:-8561} contracts/script/deploy_pool_dryrun.sh

deploy-pool-anvil: ## on a running Anvil (ANVIL_RPC, default :8545, after script/Deploy.s.sol): pool + sandbox pool, records in contracts/deployments/31337.json
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge script script/DeployPool.s.sol --rpc-url "$${ANVIL_RPC:-http://127.0.0.1:8545}" --broadcast -vv
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge script script/DeployPool.s.sol --sig "runSandbox()" --rpc-url "$${ANVIL_RPC:-http://127.0.0.1:8545}" --broadcast -vv

deploy-pool-sepolia: ## operator: main IncentivePool on Ethereum Sepolia, opened by the buyer [SIM] (BUYER_PK) under POLICY_V2, verified; adds incentivePool* keys to deployments/11155111.json, then checks the record against the chain
	@test -f .env || { echo "the operator's key file is not linked here (names in .env.example); nothing was sent"; exit 1; }
	@set -a; . ./.env; set +a; \
	  test -n "$$SEPOLIA_RPC_URL" || { echo "SEPOLIA_RPC_URL is empty in the key file; nothing was sent"; exit 1; }; \
	  cd contracts && export PATH="$$HOME/.foundry/bin:$$PATH" && \
	  { POOL_COVENANT_ID=$(MAIN_COVENANT_ID) forge script script/DeployPool.s.sol --rpc-url "$$SEPOLIA_RPC_URL" \
	      --broadcast --slow --verify -vv $(SEPOLIA_GAS_FLAGS) \
	    && python3 -I script/check_pool_record.py deployments/11155111.json "$$SEPOLIA_RPC_URL"; } \
	  || { echo "$(POOL_RECORD_RESTORE)"; exit 1; }; \
	  ../pipeline/.venv/bin/python ../pipeline/txlog.py broadcast broadcast/DeployPool.s.sol/11155111/run-latest.json

deploy-sandbox-pool-sepolia: ## operator: redeploy the judge sandbox [SIM] pair with its IncentivePool and the OBS_INDEPENDENT open slot on Sepolia, verified; earlier pair kept as sandboxPrevious; record checked against the chain
	@test -f .env || { echo "the operator's key file is not linked here (names in .env.example); nothing was sent"; exit 1; }
	@set -a; . ./.env; set +a; \
	  test -n "$$SEPOLIA_RPC_URL" || { echo "SEPOLIA_RPC_URL is empty in the key file; nothing was sent"; exit 1; }; \
	  cd contracts && export PATH="$$HOME/.foundry/bin:$$PATH" && \
	  { forge script script/DeployPool.s.sol --sig "runSandbox()" --rpc-url "$$SEPOLIA_RPC_URL" \
	      --broadcast --slow --verify -vv $(SEPOLIA_GAS_FLAGS) \
	    && python3 -I script/check_pool_record.py deployments/11155111.json "$$SEPOLIA_RPC_URL"; } \
	  || { echo "$(POOL_RECORD_RESTORE)"; exit 1; }; \
	  ../pipeline/.venv/bin/python ../pipeline/txlog.py broadcast broadcast/DeployPool.s.sol/11155111/runSandbox-latest.json

# --resume would also send a transaction the cached run still lists as pending: script/check_resume.py refuses unless
# the cached run is finished (no pending, one receipt per transaction, every receipt status 1).
verify-pool-sepolia: ## operator: retry Etherscan verification of the main pool and its libraries (refused unless the cached run is finished; no transaction is sent)
	@test -f .env || { echo "the operator's key file is not linked here; nothing was done"; exit 1; }
	@cd contracts && python3 -I script/check_resume.py broadcast/DeployPool.s.sol/11155111/run-latest.json
	@set -a; . ./.env; set +a; cd contracts && export PATH="$$HOME/.foundry/bin:$$PATH" && \
	  POOL_COVENANT_ID=$(MAIN_COVENANT_ID) forge script script/DeployPool.s.sol --rpc-url "$$SEPOLIA_RPC_URL" --broadcast --resume --verify -vv

verify-sandbox-pool-sepolia: ## operator: retry Etherscan verification of the judge sandbox redeploy (refused unless the cached run is finished; no transaction is sent)
	@test -f .env || { echo "the operator's key file is not linked here; nothing was done"; exit 1; }
	@cd contracts && python3 -I script/check_resume.py broadcast/DeployPool.s.sol/11155111/runSandbox-latest.json
	@set -a; . ./.env; set +a; cd contracts && export PATH="$$HOME/.foundry/bin:$$PATH" && \
	  forge script script/DeployPool.s.sol --sig "runSandbox()" --rpc-url "$$SEPOLIA_RPC_URL" --broadcast --resume --verify -vv

# Keyless re-check of the committed Ethereum Sepolia record (final sprint F5). No key, no account, nothing sent:
# read-only JSON-RPC (eth_chainId, eth_getCode, eth_getTransactionReceipt, eth_getTransactionByHash, eth_call).
.PHONY: verify-evidence verify-evidence-offline
LANE_CHECKS += verify-evidence-offline

verify-evidence:  ## keyless: re-check every recorded Sepolia tx, the deployed code and the signed bundle against a public RPC (read-only); OFFLINE=1 replays recorded answers
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	pipeline/.venv/bin/python scripts/verify_evidence.py $(if $(filter 1,$(OFFLINE)),--offline,)

verify-evidence-offline: ## make verify-evidence from the recorded RPC answers in scripts/tests/fixtures/verify_evidence/ (offline; part of make check)
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	pipeline/.venv/bin/python scripts/verify_evidence.py --offline

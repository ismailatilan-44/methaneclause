# Keyless re-check of the committed Ethereum Sepolia record. No key, no account, nothing sent:
# read-only JSON-RPC (eth_chainId, eth_blockNumber, eth_getCode, eth_getTransactionReceipt, eth_getTransactionByHash,
# eth_getLogs, eth_call) and keyless GETs of the Sourcify / Blockscout source-verification APIs.
.PHONY: verify-evidence verify-evidence-offline
EXTRA_CHECKS += verify-evidence-offline

verify-evidence:  ## keyless: re-check every recorded Sepolia tx, the deployed code, its public source verification, every pool's opener and terms and the signed bundle (read-only); OFFLINE=1 replays recorded answers
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	pipeline/.venv/bin/python scripts/verify_evidence.py $(if $(filter 1,$(OFFLINE)),--offline,)

verify-evidence-offline: ## make verify-evidence from the recorded answers in scripts/tests/fixtures/verify_evidence/ (offline; part of make check)
	cd contracts && PATH="$$HOME/.foundry/bin:$$PATH" forge build -q
	pipeline/.venv/bin/python scripts/verify_evidence.py --offline

# From this prototype to a signed clause

The demo runs every role with one person; production gives each witness (observer organisation) its own
organisation, its own host and its own keys, and the same contracts execute the clause between them.

This page names, for each element of the clause, what runs today and where to check it, what production adds, who
does it, and how far it is: **built** (code and tests in this repository, run by `make check` or recorded on Ethereum
Sepolia), **designed** (specified in this repository, interface named, no code yet) or **roadmap** (a production step,
named here and in [`SCALING.md`](SCALING.md)). Roles and terms follow [`spec.md`](spec.md); the security model is
[`SECURITY.md`](SECURITY.md); the contract text is the [model clause](clause/model-clause.md). Values are not repeated
here: each lives in the file cited.

## Element by element

| Element | What runs today (file / test / tx) | What production adds | Who does it | Status |
|---|---|---|---|---|
| **Observer organisations and keys** | Three observer organisations, quorum k of n, on the Sepolia main registry; OBS_A and OBS_B signed the TKM_S_276 episode ([`0x263e93…`][tx-263e93]); hybrid keys in the local demo; the judge path in the sandbox. Details below the table. | Independent organisations, at least k + 2 per clause, each on its own host with its own signing keys and a separate cold controller key. Details below the table. | Each observer organisation; the parties name them in the Annex (model clause 9.1). | Built: mechanism, signing, rotation. Roadmap: independent organisations. |
| **Registry: admission, rotation, revocation** | `KeyRegistry` on Sepolia with its admin renounced; rotation and revocation by each organisation's controller; `KeyRegistryV2` (majority-vote admission and removal) on a local chain. Details below the table. | `KeyRegistryV2` on the production chain, one registry per clause, so its members are exactly that clause's observers and an admission elsewhere never reaches it; seller and buyer organisations at genesis; production-length admission delay and proposal expiry (constructor values, [`contracts/README.md`](../contracts/README.md)); a reserve list agreed at signing (model clause 9.3). | Observer organisations' controllers vote; the parties agree the reserve list. | Built: V2 on a local chain. Roadmap: public deployment. |
| **Data providers and licences** | Keyless Carbon Mapper API and the UNEP IMEO MARS GeoJSON; raw responses hashed into [`data/MANIFEST.json`](../data/MANIFEST.json) and never committed; terms in [`DATA.md`](DATA.md). Observers refuse an unknown provider or instrument before signing ([`pipeline/observer/bundle.py`](../pipeline/observer/bundle.py), `test_bad_input_refused`); every attestation names `providerId` and `instrumentId`. | A commercial data agreement with Carbon Mapper, held by the observer organisations; UNEP's permission for MARS as the corroborating provider; a NASA EMIT plume reader (EMIT L2B methane plume complexes, NASA open data) beside Carbon Mapper, so a second provider attests the same source. The EMIT reader is an observer module and its bundle schema; the contracts take it as they are. | Observer organisations (agreements, readers). | Built: Carbon Mapper and MARS readers. Roadmap: the agreements (planned if the clause is adopted, [`DATA.md`](DATA.md)) and the EMIT reader. |
| **Policy and model clause** | `POLICY_V2` and `POLICY_V2_VERDICT_ONLY` in [`data/derived/policy_v2.json`](../data/derived/policy_v2.json) from [`pipeline/policy.py`](../pipeline/policy.py); the live Sepolia covenant's `policyHash` is rebuilt from the repo (`test_sepolia_covenant_policy_is_reproduced_from_the_repo`); the pool's `paramsHash` matches the pipeline (`test_policyV2_paramsHash_matchesPipeline`). The [model clause](clause/model-clause.md) renders every value from the policy ([`pipeline/clause.py`](../pipeline/clause.py), `test_no_value_is_typed_by_hand`) and maps each clause to its contract function (`test_every_contract_function_in_the_map_exists`). | The parties' own values (amounts in euros; a multi-year term matching the supply contract, so the final installment stays reachable, model clause 10); review by counsel under the governing law; the clause signed into the GSA or SPA, or as a side letter. The schedule is rendered from their policy the same way. | Buyer and seller with counsel. | Built: policy, hashes, draft clause. Roadmap: legal review and signature. |
| **Settlement per jurisdiction** | On chain in testnet ETH: the buyer withdrew the Sepolia rebate ([`0xe15306…`](https://sepolia.etherscan.io/tx/0xe15306ee30432c7e92f9ccfec14ee9ea4f799cd6324f704723694159e4a819e4)). Verdict-only: the "TKM verdict-only" run of `make demo` (no bond; the pool refuses ETH with `BadParams`; CONFIRMED is the verdict; the credit note is exported and read back), [`pipeline/export/credit_note.py`](../pipeline/export/credit_note.py), `test_rebate_note_from_a_confirmed_verdict`, `test_import_refuses_an_edited_note`, `test_release_verdictOnly_emitsCreditNoteDue_noFunds`. | Where a token is lawful, settlement in a MiCA euro e-money token (an ERC-20 settlement path, next section). Where crypto-assets in payments are banned, as in Türkiye under the CBRT regulation of 2021, the verdict-only covenant and a credit note or bank transfer that cites the verdict reference (model clause 7.3). AML, KYC and sanctions checks stay with the paying party and the sponsor (model clause 7.4). | The parties choose the mode (model clause 7); the e-money token issuer; the paying party's bank. | Built: on chain in testnet ETH; verdict-only with credit note. Roadmap: the euro e-money token. |
| **Bond and surety** | The seller's bond in the core, posted at creation on Sepolia ([`0x74edf3…`](https://sepolia.etherscan.io/tx/0x74edf31d45da1e0898da678122d51e16f3274f37e83f1aa94eb110be9ea377fd)) and sized by the committed rule ([`data/derived/model/bond_sizing.json`](../data/derived/model/bond_sizing.json)). `SuretyBond` lets a surety post it and receive every return; the operator cannot move funds (`test_suretyBond_operatorCannotMoveFunds`, `test_suretyBond_pull_afterSettle`, `test_suretyBond_pull_poolRefund`); the DZA replay in `make demo` runs its covenant through one; local chain. | A regulated surety or bank posts the bond, so the seller's capital is not locked; the bond held in the settlement asset; `SuretyBond` on the production chain. | Seller; surety (insurer or bank). | Built: core bond (Sepolia), `SuretyBond` (local chain). Roadmap: public deployment with a real surety. |
| **Sponsor pool and observer fee** | `IncentivePool` with its five linked libraries on Sepolia; the main pool funded by the buyer [SIM] ([`0xdfb7e3…`][tx-dfb7e3]); a real Tanager clean pass, signed by the judge test wallet [SIM] and observer 1 [SIM], counted in the judge sandbox ([`0x571add…`][tx-571add]). The observer fee is split among the signing organisations' controllers (`test_countPass_counts_returnsBond_paysObserverFee_emits`, `test_countPass_feeSplit_orgWithoutController_andDust`); sponsors exit pro rata after CLOSED (`test_sponsorWithdraw_proRata_lastTakesRemainder_noDust`). | The lender whose sustainability-linked margin already moves on methane intensity, or the buyer, funds the pool ([`SCALING.md`](SCALING.md), "Who pays"); the observer fee in the settlement asset, plus an annual fee from the sponsor (model clause 9.4). | Sponsor; observer organisations receive the fee. | Built: Sepolia and local chain. Roadmap: a real sponsor. |
| **Evidence export for Regulation (EU) 2024/1787** | IEA Annex D export for TKM_S_276, committed as [`data/derived/export/TKM_S_276.annex_d.json`](../data/derived/export/TKM_S_276.annex_d.json) and `.csv`, with the attested bundle embedded and an `euEvidence` entry per Annex IX item (`test_embedded_bundle_hashes_to_the_attested_evidence`, `test_json_round_trip_same_hash`, `test_csv_round_trip_same_hash`, `test_eu_evidence_maps_without_claiming_compliance`); the page exports it with a round trip; Annex C seller-claim examples [SIM] whose hash goes to `recordMitigationClaim` ([`pipeline/export/README.md`](../pipeline/export/README.md)). | The importer attaches each record to its yearly report under Art. 27 ([`SCALING.md`](SCALING.md), Phase 1); the real seller's identity fills the producer item, marked `no` today because the seller is [SIM]. | The buyer's methane desk. | Built: the export and its round trip. Roadmap: filed by a real importer. |
| **Chain choice, Hyperledger Fabric included** | Ethereum Sepolia (public, proof of stake), the deployed contracts source-verified on Etherscan, and local Anvil; the deployment script also targets Base Sepolia (`make deploy-base`), with no L2 run measured; after each deployment [`check_pool_record.py`](../contracts/script/check_pool_record.py) checks the linked libraries against the chain. The Fabric mapping is written in the README, "Production path", and in this table. | A public mainnet or L2 where the settlement token lives, with gas measured there before any cost is quoted. Where the parties already share a consortium ledger: the covenant as Fabric chaincode, k-of-n as the endorsement policy of `proposeEpisode` with seller and buyer peers excluded, settlement by instruction (the credit-note path). | The parties choose the venue in the clause; the deployer deploys and runs the record check. | Built: Sepolia. Designed: Fabric mapping. Roadmap: mainnet or L2, chaincode. |
| **Monitoring and relapse watching** | Every waiting state ends through a permissionless call. `relapse(claimId, episodeId)` cancels a claim in one call whatever the number of pending passes (`test_relapse_gasIndependentOfPendingPasses`) and accepts a caller-named CONFIRMED or EXPIRED episode (`test_attestCleanPass_relapseHiddenByOlderDatedProposal_residualR23`); in the DZA replay of `make demo` the sponsor calls it. Observers can follow `EpisodeChallenged` and `CorroborationChallenged`; `LIVE=1 make demo` makes the observers fetch Carbon Mapper now. | A sponsor watcher service that follows the core's episodes and the provider's catalogue for each claim's source and calls `relapse` ([`SECURITY.md`](SECURITY.md), R23); each observer organisation fetches new plumes and scenes for the covered sources on a schedule and answers the challenges it is named in. | Sponsor (watcher); observer organisations (fetch, answers); anyone may call. | Built: the on-chain calls. Designed: the sponsor watcher. Roadmap: the services. |
| **Verification by third parties** | [`scripts/verify_bundle.py`](../scripts/verify_bundle.py) `--rpc` re-checks a bundle against the registry the covenant itself trusts (`test_verify_bundle_rpc_uses_covenant_registry`); the page re-computes JCS + EIP-712 and checks ML-DSA-44 in the browser. | Page modules loaded with subresource integrity ([`SECURITY.md`](SECURITY.md) §2); the same tools run by a lender, an auditor or a competent authority against the production chain. | Anyone. | Built: both checks. Roadmap: subresource integrity. |

**Observer organisations and keys, today.** Three observer organisations, quorum k of n, on the Sepolia main registry ([`contracts/deployments/11155111.json`](../contracts/deployments/11155111.json)); OBS_A and OBS_B signed the TKM_S_276 episode ([`0x263e93…`][tx-263e93]). The five observer steps in [`pipeline/observer/service.py`](../pipeline/observer/service.py); hybrid ECDSA + ML-DSA-44 keys (algId 2) in the local demo ([`Deploy.s.sol`](../contracts/script/Deploy.s.sol) registers the demo observers as algId 2; `test_sign_and_verify_both_halves`); the judge path: a judge test wallet [SIM], run by the author, registered under OBS_JUDGE in the judge sandbox ([`0xd8fd9f…`][tx-d8fd9f]); a judge's own wallet joins the same way (`make judge-attest`).

**Observer organisations and keys, in production.** Independent organisations, as in the pilot of [`SCALING.md`](SCALING.md): a licensed satellite-data provider, a university remote-sensing lab, an NGO methane analyst, none controlled by a party, at least k + 2 of them per clause (2-of-4 for k = 2) so that a contest, which needs k + 1, still has a spare organisation (item 8 below). Each runs the observer service on its own host, holds its own signing keys and a separate cold controller key (today `_org` in [`Deploy.s.sol`](../contracts/script/Deploy.s.sol) makes the first key the controller; [`SECURITY.md`](SECURITY.md) §8). The Sepolia-style algId 1 observers move to algId 2 with `rotateKey`, no redeploy.

**Registry, today.** `KeyRegistry` on Sepolia with its admin renounced ([`0x98ed72…`](https://sepolia.etherscan.io/tx/0x98ed7254aa44d62ce2e7b019d0bcd57c887f65b606d221cb1f029a286f1387d0); `registryAdmin` is the zero address in the deployment record). Rotation and revocation by each organisation's controller (`test_rotate_keepsHistory`, `test_rotatedOldKey_reverts_newKeyAccepted`, `test_revoke_bySelf_controller`; `make demo` revokes OBS_B and its next attestation reverts `RevokedKey`). `KeyRegistryV2` admits and removes observer organisations by majority vote with a k floor, an admission delay and vote quarantine (`invariant_V2_noOrgWithoutMajority`, `test_v2_removal_closesKeys`, `test_v2_recentRevocation_voteRejected`); the unchanged core and the pool run on it (`test_v2_coreAndPoolRunOnV2`, `test_v2_poolReadsV2_feeToRemovedOrgForfeited`); local chain.

## Is the core ready for real parties?

**What already works unchanged with real parties.**

- **The verdict.** `KeyRegistry` and `MethaneCovenant` know the parties only as registered keys of organisations; the
  quorum counts distinct organisations outside the seller's and the buyer's (`invariant_I2_noSelfAttestation`). The
  bytecode that runs on Sepolia (pinned by `make core-bytecode`) serves a real seller, a real buyer and three
  independent observer organisations as it is: what changes is who holds the keys.
- **Verdict-only settlement with a credit note.** A party that may not pay in crypto-assets runs the same core with no
  bond: CONFIRMED is the verdict and the credit note cites it (`make demo`, "TKM verdict-only";
  [`pipeline/export/credit_note.py`](../pipeline/export/credit_note.py)).
- **Any provider.** `providerId` and `instrumentId` are signed fields that the core only requires to agree across the
  quorum (`AttestationMismatch`); there is no provider list on chain. The pool accepts any instrument id from 1 to 7
  for a clean pass and pins only the corroborating provider (`unep-mars`). A detection read from NASA EMIT needs no
  contract change.
- **No operator.** No owner, no upgrade path, no pause; the registry admin is renounced on Sepolia; each
  organisation's controller rotates and revokes its own keys (`invariant_I7_noAdminPath`).
- **Governance without touching the core.** `KeyRegistryV2` exposes the v1 view interface, and the unchanged core and
  pool run on it (`test_v2_coreAndPoolRunOnV2`, `test_v2_poolReadsV2_feeToRemovedOrgForfeited`); the core takes its registry in the constructor.
- **A whole portfolio in one covenant**, through one Merkle root, and any parties' policy committed by the same
  `policyHash` before evidence exists ([`SCALING.md`](SCALING.md)).
- **The post-quantum half** moves to a live observer by `rotateKey(old, new, 2, pqPubKeyHash)`, not a redeploy.

**What production changes, and what each change costs.**

1. **Settlement in a euro e-money token (ERC-20).** Every money path today is the chain's native asset: in the core,
   `createCovenant` and `fund` (payable; `_fund` adds `msg.value`), the proposer and challenger deposits (`msg.value`
   checks in `proposeEpisode` and `challenge`) and `withdraw` (a value call); in the pool, `fund` and
   `contributions`, the pass and challenger deposits, the `challengeCorroboration` deposit, the observer fee, `withdraw`
   and `sponsorWithdraw`; in `SuretyBond`, `createCovenant` (forwards `msg.value`), `receive`, `pull` and `withdraw`. The accounting itself (`funded == paid + held + remaining + refunded` in the
   core, `balance` and `reserved` in the pool) counts units, not ETH, and stays. Two routes:
   - *Core untouched.* Run the core verdict-only and add a token settlement contract that, like `IncentivePool`,
     reads the core and never writes it: the seller or its surety deposits the token (`transferFrom`); CONFIRMED
     credits the buyer the tier's token amount as a pull credit; VOID, EXPIRED and LINKED pay nothing; the remainder
     returns after the term. The episode records its tier even without a bond, which is how the verdict-only pool
     already sets its cap (`test_openClaim_verdictOnly_capIsUnitTier`). A token variant of the pool changes the paths
     listed above. Cost: one new contract and a pool variant, with their unit and invariant suites (conservation per
     token; a token that blocks a receiver leaves the credit in place, as `test_withdraw_revertingReceiver_keepsCredit`
     does for ETH), the Slither triage and an external audit; `make core-bytecode` stays green. Proposer and
     challenger deposits stay in the native asset: they are anti-spam deposits posted by submitters; a forfeited one is
     a penalty, not the rebate or the refund, and stays in the native asset.
   - *Token-native core.* The same paths change inside `MethaneCovenant`. Cost: a new core version, so the byte pin,
     invariants I1–I7, the Halmos bond-conservation runs and the Slither triage are redone for it.
2. **`KeyRegistryV2` on a public chain.** Deploy one V2 registry per clause with the observer and party organisations
   and their controllers at genesis, and a core instance bound to it; no contract change. Cost: the deployment gas of a
   registry and a core per clause ([`FOOTPRINT.md`](FOOTPRINT.md) lists the core's and the v1 registry's; V2's is
   measured on its first public deployment), and bounded symbolic runs like those the core and
   the pool have (today V2 is covered by unit tests, stateful fuzz and the Slither triage, [`SECURITY.md`](SECURITY.md)
   §13).
3. **A separate controller key per organisation.** A deployment-script change (the controller becomes a parameter of
   `_org`); `setOrgController` and the V2 genesis already take it. No contract change.
4. **A commercial data agreement.** Carbon Mapper's terms are non-commercial, so the observer organisations hold a
   commercial agreement with Carbon Mapper, and UNEP's permission for MARS, before the term starts (model clause 8.1);
   signed once by the observers and reused across covenants ([`SCALING.md`](SCALING.md), Phase 4). The EMIT reader
   adds a second, open provider: an observer module, its bundle schema and its adversarial cases; no contract change.
5. **The sponsor watcher and the observers' schedulers.** Off-chain services that call functions that already exist
   (`relapse`, `resolvePass`, `submitResolution`); no contract change.
6. **The production venue.** Deploy, verify on the explorer and run `check_pool_record.py` on the chosen mainnet or
   L2; measure its gas before quoting a cost. The contracts are plain EVM bytecode.
7. **Subresource integrity for viem.** The map libraries and the ML-DSA-44 verifier already load under integrity
   hashes; viem is next, as published package files or a copy served with the page, each with its hash; a page change.
8. **Quorum margin: n ≥ k + 2, and every challenge answered.** A challenged episode needs k observer organisations to
   answer before its deadline and a contested one k + 1 (`submitResolution`); an episode left unanswered expires and
   its rebate returns to the bond ([`SECURITY.md`](SECURITY.md) §15). The clause therefore names at least k + 2
   organisations (2-of-4 for k = 2), so a contest is confirmed by k + 1 of them with one to spare, and each
   organisation's scheduler answers every challenge and contest it is named in within the window. No contract
   change: k is a covenant parameter and n is the set the clause names; on the unchanged core, with four organisations
   and k = 2, a contest still confirms with one silent and a challenge with two (`contracts/test/unit/QuorumMargin.t.sol`:
   `test_quorumMargin_contested_oneOrgSilent_kPlusOneConfirm_confirmed`,
   `test_quorumMargin_challenged_twoOrgsSilent_kConfirm_confirmed`). Cost: one more organisation's fee and data
   agreement. A contract-level guard follows in a core version or in the item-1 settlement contract, which reads the
   episode (an EXPIRED episode keeps its `challenger`): an objective-reason challenge left unanswered settles as
   CONFIRMED, or the challenger's bond goes to the buyer on expiry.
9. **Both parties agree the pool's terms.** Either party may open a covenant's pool today, and the opening checks the
   terms' shape, not the counterparty's agreement ([`SECURITY.md`](SECURITY.md), R30). Production adds the
   counterparty's EIP-712 consent to `paramsHash` at opening, minimum windows and publication lag, and a verdict-only
   unit cap bounded by the covenant's tier amounts; until then `make verify-evidence` (row `pool-terms`)
   compares each pool with the agreed policy. Cost: a pool version, with its unit and invariant suites and the
   Slither triage; the core is untouched.
10. **The post-quantum half names its deployment.** ML-DSA-44 signs JCS({bundle, chainId, verifyingContract}) under a
    new context version, so the bundle's long-term signature binds the chain and the contract as the EIP-712 domain
    does ([`SECURITY.md`](SECURITY.md) §6). An observer, verifier and page change; no contract change.

[tx-263e93]: https://sepolia.etherscan.io/tx/0x263e9321e985bee7086c4da45bbb57c0059c8853716c5905e31a2dcf09af7508
[tx-d8fd9f]: https://sepolia.etherscan.io/tx/0xd8fd9f7af23ce89f93b5ab927c22c14f25fa10365122fbeaebfb8f9a127ee4e1
[tx-dfb7e3]: https://sepolia.etherscan.io/tx/0xdfb7e3fa828e25866d5cc55ceb47b97ba57662036f0edabc3dec3c5c4af5f21b
[tx-571add]: https://sepolia.etherscan.io/tx/0x571addbe7ae280023e975fec928dcba0088cf00062f721bb5f50231977a35ae9

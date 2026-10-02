# DreamJourney Live run-03 local closure (2026-09-24)

## Status

**LOCAL_PASS for the applicable controlled local gates.** This supersedes the run-03 `LOCAL_INCOMPLETE` snapshot. It does not close the historical incident: `DIAG_SEQ5_INITIAL_TRIGGER_UNRESOLVED`. Real Provider, deployment, iPhone short/physical-20/physical-65-minute sessions, acoustics, and historical reprocessing remain `NOT_RUN`.

No production or historical business data was accessed. No phone was connected, app installed, business write replayed, commit, or push performed. Both dirty worktrees were preserved.

## Actual changes and isolation

- Astra's retained `EchoViewController.detachLiveMemoryRecoveryForTesting()` invalidates this page's pending policy-refresh callback and detaches its recovery subscription without cancelling an already-issued read. The retained CAP15 cold-start test still asserted 1-2 real delivery GETs per variant, zero business POST, and unchanged disk command.
- The first weak-Controller-release experiment failed; [diagnosis](D-coldstart-test-isolation/diagnosis.md) and its xcresult remain. In `final-07`, short-A and logical20 passed, short-B failed at 3 GETs against the unchanged maximum of 2, and logical65 did not start. The failure is not relabeled as a pass.
- This closure adds only a DEBUG coordinator observation, `hasActiveReadForTesting`, and changes the test completion predicate from `state != checking` to no active request/handle. The page detaches first; an exposed GET must finish before the coordinator is suspended and the server's global count is read. This prevents a prior variant's late GET from entering the next interval. No production recovery policy, request cap, POST behavior, or business state transition changed.
- Same assertion on the final build: [short-A log](../final-08/logs/logical20-short.log) and [short-B log](../final-08/logs/logical65-short.log) each show wrong-command/thread/session at **1 GET and 0 business POST**. Both short xcresults passed. The first failed attempt and final-07 are retained as separate raw evidence, not fabricated as a product red/green pair.

## A/B/C evidence audit

| Item | Verified scope and raw evidence | Boundary |
|---|---|
| A / N1 | [summary](A-n1/evidence/n1-result-summary.json): both same-assertion tests failed three times under an isolated inverse-N1 behavior reconstruction, then passed three times with the current fix and persistence-to-main completion barrier. Both also pass on the final-08 build in [affected xcresult](final08-affected.xcresult). | The red copy reconstructs only inverse N1 behavior; it is **not** a complete historical source revision. |
| B / backend | [report](B-backend/B-backend-closure-report.md) and [per-method matrix](B-backend/per-method-matrix.md): 44 methods/46 PoolClosed records reproduce on old HEAD and current worktree with an incorrectly unopened PostgresStore fixture; both pass all 44 methods and the related 180-module suite under the repository's required memory fixture. The seven 259/260 route-count failures remain an independently reproduced old-HEAD baseline exception. | Memory-fixture API contract tests are not PostgreSQL transaction tests. The unfiltered 2675-test backend suite is **not** claimed green. |
| C / capacity | [result](C-capacity/result.json), [fact audit](C-capacity/fact-evidence-audit.json), and [request ledger](C-capacity/request-ledger.json): default Worker, real adapter, controlled local HTTP and isolated PG persist 300/300 independently expected facts, exact Source/turn/span/evidence/hash identity, zero missing/duplicate/extra, and seven negative controls detected. | One Run has **323** wire requests matching 323 persisted attempts, not 646. The 19 rejected responses still consume the unchanged budget. No real Provider was called. |

## Final same-version gate

Executed `run_round_simulation.py --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres --output-root <run-03>/final-08 --scenario all` after the source/test fix. [Runner result](../final-08/artifacts/runner-complete.json) records `short-A -> logical20 -> short-B -> logical65`; each of the four [xcresult bundles](../final-08/green/) reports one pass, zero failed/skipped. Both short gates verified two owner turns with second-turn supplement, real iOS candidate visibility, isolated review, formal memory and reconstructed readback before their corresponding long scene. The gate's invalid/missing/expired/consumed receipt probes emitted no business request.

| Scene | Candidate readback and final result |
|---|---|
| short-A -> logical20 | 1 short candidate, 4 long candidates, 5 formal memories; source/manifest binding and restarted-API readback passed; 13 private WorkUnits were observed completed **before end forwarding**. |
| short-B -> logical65 | Independent 1 short candidate, 17 long candidates, 18 formal memories; same client and rebuild checks passed; 18 private WorkUnits were observed completed **before end forwarding**. |

These are logical 1200/3900-second clocks, not physical-duration or acoustic evidence. Neither WorkUnit number proves that every page completed before the user pressed stop. The default API lifespan and official Worker CLI/store factory ran with isolated PG; only model HTTP was controlled. No finished extractor or manual preorganizer was injected. [Logical20](../final-08/green/logical20-full-chain.json) and [logical65](../final-08/green/logical65-full-chain.json) retain the detailed assertions and request ledger references.

Final-build affected simulator regression [xcresult](final08-affected.xcresult): N1's two tests, B6 account/batch binding, and newest-blocked-scene ownership all passed (4/4, zero skipped). Generic iOS device target [build xcresult](final08-generic-device-build.xcresult) reports `succeeded`, zero errors, unsigned; this was a compile, not an install. Both repositories passed `git diff --check`. Product-source unaffected historical regressions remain mapped to run-03 evidence; they are not represented as freshly rerun here.

## Reproduction commands

```sh
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/tools/run_round_simulation.py \
  --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres \
  --output-root /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/final-08 \
  --scenario all --simulator-id 67D3337E-0623-4578-9479-31F4CD9033DA

# From /Users/gaominge/Documents/Codex/Video/DreamJourney_dev, with final-08 xctestrun:
xcodebuild -quiet -xctestrun /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/final-08/builds/DerivedData/Build/Products/DreamJourney_iphonesimulator27.0-arm64.xctestrun \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  -derivedDataPath /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/final-08/builds/DerivedData \
  -resultBundlePath /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final08-affected.xcresult \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testLiveDeliveryObservationKeepsReadingAfterMatchingItemWithStaleEnvelope \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testLiveDeliveryObservationRejectsInFlightSuccessAfterOverallDeadline \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testB6RecoveryRejectsCompletionAfterAccountOrBatchBindingChanges \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testNewestBlockedColdStartSceneDoesNotPresentOlderValidScene test-without-building

xcodebuild -quiet -workspace DreamJourney.xcworkspace -scheme DreamJourney -configuration Debug \
  -destination 'generic/platform=iOS' \
  -derivedDataPath /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/final-08/builds/GenericDeviceDerived \
  -resultBundlePath /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final08-generic-device-build.xcresult \
  CODE_SIGNING_ALLOWED=NO build
```

The original output paths are immutable evidence. A repeat must select new result/output paths and start an owned isolated PG cluster; it must not overwrite `final-08` or the closure bundles.

## Fingerprints, baseline, cleanup

- Final protected source fingerprint: `45a6e1b97e2d31ab608658013fe4d27dd91c012c5c5f755a31edb5673de279fe`.
- Final simulator build fingerprint: `403d42abc80a899a7ee8a6438dc7b04b1ee0b33c07d8443548cbc25251b2036a`; logical20 config `7510d7db8e2d0410032efdb78ab3828fda43f49490ce65dedefcbed1efa891c5`, logical65 config `fad39ca8feeae46d6e2c9d99623e5c64b8cecd2c9ef5997d978eca375bd6848b`.
- Final generic-device executable SHA-256: `08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`.
- [Fingerprint check](final08-fingerprint-check.json) confirms the current protected source equals the final-08 manifest and all 312 build artifacts match; only the two intended iOS files differ from the initial closure snapshot. The old final-06 receipt is invalid for the current test build.
- iOS HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`; backend HEAD `ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`. Extensive pre-existing uncommitted changes remain.
- Final-08 loopback HTTP ports 55521/55522 are closed; no `dj_%` test databases remain. The handed-over dedicated PG cluster on 55520 was stopped with `pg_ctl -m smart`; its data directory and all evidence remain intact.

The seven old-HEAD route-count failures are a documented baseline exception, not a waiver of their assertions. The 46 PoolClosed results are fixture attribution, not proof of PG success. Real Provider, deployment, production data, physical duration, iPhone, acoustics, historical seq5 root cause and reprocessing require independent future evidence.

# DreamJourney Live unified repair: run-03 local handoff

## Conclusion and boundary

**Current closure: LOCAL_PASS for applicable controlled local gates.** This report retains the original run-03 snapshot below; [2026-09-24 closure](closure-2026-09-24/final-closure-report.md) supersedes its `LOCAL_INCOMPLETE` status. Isolated inverse-N1 red/green, the backend fixture attribution, 300 exact facts, and final-08 same-version four-scene verification now have separate evidence. Seven old-HEAD route-count failures remain an explicit baseline exception; the unfiltered backend suite is not claimed green. Historical seq5's first trigger remains `DIAG_SEQ5_INITIAL_TRIGGER_UNRESOLVED`.

No phone, real Provider, deployment, production business data, historical reprocessing, commit, or push was used. Local PostgreSQL used port 55520 and a disposable `/private/tmp/dj-run03-pg` cluster. Neither a synthetic replay nor the healthy four-scene run proves the historical incident's unique cause.

## Changes and evidence

| Review item | Local change | Raw evidence and result |
|---|---|---|
| N1 in-flight 180s and rejected matching item | `EchoViewController.swift`: cap delivery GET timeout by remaining round deadline; validate deadline before callback commit; let `receiveDeliveryStatus` return verified/pending/unverified so stale version cannot finish observation. | [A isolation evidence](closure-2026-09-24/A-n1/evidence/n1-result-summary.json) adds same-assertion red/green, three repeats each, for an inverse-N1 behavior reconstruction; it is not a full historical source revision. Both tests pass on the final-08 build in [affected xcresult](closure-2026-09-24/final08-affected.xcresult). |
| N3 newer blocked scene ownership | Recovery/Controller ordering in `EchoViewController.swift` retains the newest blocked scene's page ownership instead of showing the older healthy scene. | `testNewestBlockedColdStartSceneDoesNotPresentOlderValidScene`, final OwnerTruth suite. |
| N3 exact cold-start read | `testClosingOutboxColdStartUsesRealBackendClientAndRejectsForeignDeliveryBinding` preserves original frozen start and rejects wrong hash through real Controller/FeatureGate/BackendClient and controlled URLProtocol, then rebuilds. The CAP15 short helper now exercises normal API/isolated PG with wrong command/thread/session, stale account, and corrupt V2 follow-up; long fault branch injects a stale-version status response before valid status. | [Five wrong-hash passes](logs/ios-cold-start-five-summary.json); [final short test](final-06/logs/logical20-short.log) asserts 1-2 exact GET per wrong-binding case, zero extra business POST, original command intact; stale account and corrupt record produce zero GET/POST, original file retained. [Long scenario](final-06/green/logical65-full-chain.json) records one 500, one stale 200, then valid 200 without replay. These are directed branches, not all permutations in one response. |
| N3 persistent diagnostic | `OwnerTruthContracts.swift` exposes raw append transport failure before folding to notice; `DreamJourneyBackendClient.swift` unwraps only whitelisted underlying network error codes; `EchoViewController.swift` records HTTP/system code, exposure, attempt and elapsed time; `ConversationMemoryManager.swift` exposes bounded diagnostics-persistence-unavailable marker. | [Business red](logs/ios-real-first-error-red.xcresult) and [same-assertion green](logs/ios-real-first-error-green.xcresult): timeout and connectionLost were formerly masked by AFError, now survive actual BackendClient/use case/Controller and disk rebuild; HTTP500 and pre-handler401 remain distinct. [Combined local green](logs/ios-first-error-combined-green.xcresult): append timeout and unwritable diagnostics directory produce queryable missing marker, preserve Outbox command/body and cause only one POST. Its [first trial](logs/ios-first-error-combined-trial.xcresult) failed because the marker was observed before the network request, a test completion error corrected by waiting on actual POST count. No text, token, full response, URL, or raw business hash is logged. |
| N4 frozen request | `deepseek.py` adds immutable `PreparedLiveModelRequest`; extraction Worker measures, reserves and sends that exact frozen payload without re-rendering after reservation. | [Business red](logs/prepared-request-red.log) disables prepared path and fails on an upstream mutation; [same assertion green](logs/prepared-request-green.log). |
| N5 preorganization | In-memory `lastProgressAt` only advances once Source is bound, matching PostgreSQL's post-session clock. | [Business red](logs/preorganization-red.log): second planned unit idles after first; [green](logs/preorganization-green.log): second unit completes. |
| N2 default assembly | Runner starts independent API with normal Uvicorn lifespan and official Worker CLI/from-env/store factory. Controlled model HTTP is the sole external substitute. Short scenes go through start/append/end/ACK/admit, private WorkUnits observed before forwarding end, candidate GET from real iOS client, review, formal memory, and API restart readback. Includes an old non-Live job in the default Worker test. | [Final order](final-06/artifacts/runner-complete.json), [logical20](final-06/green/logical20-full-chain.json), [logical65](final-06/green/logical65-full-chain.json), [legacy/default Worker](logs/300-fact-worker-and-legacy.log). `workerExtractorInjected=false`, `workerPreorganizerManuallyAssigned=false`, `preCloseCompletedWorkUnitCount=13/18`. |
| N6 capacity/transactions | Adapter classifies >8 model output as typed `outputOverCapacity`; Worker splits remaining fragments within existing Run budget. | [Original 300-fact red](logs/300-fact-capacity.log), [green](logs/300-fact-capacity-green.log), [real adapter/controlled HTTP/default Worker/PG 300 candidates](logs/300-fact-worker-and-legacy.log), [PG rollback/lease/epoch/budget](logs/concurrency-pg.log). No configured budget or model input limit was enlarged. |

## Final ordered run and regression

Executed after freezing final relevant source/tools/config. `final-01` through `final-05` remain historical because later test/tool sources changed; `final-06` reports `passed` and order `short-A, logical20, short-B, logical65`. Each independent short has two owner turns with the second supplementing the first, one visible candidate, isolated review, formal memory, and recreated readback before its corresponding long run. Logical20 had 220 long Source turns, four long candidates plus the short candidate, five formal memories, and 13 completed private WorkUnits observed before forwarding end. Logical65 had 300 long Source turns, 17 long candidates plus the short candidate, 18 formal memories, and 18 completed private WorkUnits observed before forwarding end. These are injected logical 1200/3900-second clocks, **not** physical 20/65-minute sessions. Both real iOS candidate visibility and hidden-candidate negative are recorded in the scenario JSONs.

- iOS OwnerTruth simulator, final source: [573 passed, 3 skipped, 0 failed](logs/ios-ownertruth-final-06.xcresult) and [raw log](logs/ios-ownertruth-final-06.log). CAP15 short/long tests require the runner's isolated HTTP/PG setup and are evidenced by the four-scene result bundles, not by this standalone count.
- iOS generic device, unsigned: [build log](logs/ios-generic-device-build-final-04.log), exit 0 after product-source freeze; no device install. The subsequent edit was confined to the CAP15 test helper.
- Audio owner lease, final source: [5 passed](logs/ios-audio-final-05.xcresult). B6/candidate/partial/unknown-write safeguards are included in full OwnerTruth run; this number is not a substitute for cases above.
- Backend affected `unittest`: [121 passed](logs/backend-affected-unittest.log). Long pipeline [42 passed](logs/live-long-pipeline-regression.log). Isolated PostgreSQL transaction/concurrency smoke [passed](logs/concurrency-pg.log).
- A separately repeated, explicit three-module backend command ran [114 tests, all passed](logs/backend-affected-final-explicit.log). It overlaps the 121-case selection and is not added to it as a coverage count.
- Backend full discovery: [2675 run; 7 failures, 46 errors](logs/backend-full-unittest.log). The seven route inventory expectations use 259 while current API exposes 260; this was reproduced on the prior HEAD and is not hidden as pass. The 46 errors are legacy test setup using a closed global `PostgresStore` pool without normal lifespan. They still mean the unfiltered suite is not green; no unrelated broad test refactor was made.
- `git diff --check`: both repositories passed on final source. All pre-existing dirty worktrees were preserved.

Timing clarification (2026-09-24): the observation proxy receives `/end`, may wait up to 20 seconds for a completed private WorkUnit, then forwards the request. The 13/18 counts prove completion before that forwarding point; they do not establish that all units completed before the user stopped. This does not invalidate the independent default Worker or the completed four-scene chain.

## Commands and fingerprints

Final chain command:

```sh
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/tools/run_round_simulation.py \
  --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres \
  --output-root /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/final-06 \
  --scenario all --simulator-id 67D3337E-0623-4578-9479-31F4CD9033DA
```

Other executed reproducible commands (from each repository root):

```sh
# DreamJourneyBackend
.venv/bin/python -m unittest -q tests.test_owner_truth_candidate_extraction_worker \
  tests.test_owner_truth_live_long_memory_pipeline tests.test_owner_truth_candidate_extraction

# DreamJourney_dev; local compile only, no signing or installation
xcodebuild -quiet -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -configuration Debug -destination 'generic/platform=iOS' \
  -derivedDataPath /private/tmp/dj-run03-device-derived-final CODE_SIGNING_ALLOWED=NO build

# Inspect the final simulator test bundle
xcrun xcresulttool get test-results summary --path \
  '/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/logs/ios-ownertruth-final-06.xcresult'
```

The runner refuses an existing output root, preflights simulator/Xcode, local PG/pgvector and dependencies, binds short receipts to actual source/build/config, and aborts the long run if its independent short fails. Completed command log: [final runner log](logs/final-06-runner.log). Source fingerprint `338578d2ce37d22a8191e37f0869251001371884a73fc09d3a2371c13632f23c`; simulator build fingerprint `e33292a5b776e4577e556ff8d7311ca2970da2828c22ac0dab3b17620aec0143`. Logical20 config `b7cef1ead7cfed911abdd1d1b22eca1691a8f0ee89d33759f39aa7929652fc3c`; logical65 config `e72dea94cdfe2f113298a8c95b691cae8e3bbba0587bee2c7f306c235e0605c6`. Full 312-artifact hashes, build command and source identity: [manifest](final-06/artifacts/source-build-manifest.json). HEAD at start: iOS `11d0d0051b9be3cce57822dd059472d1e2536866`, backend `ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`; extensive uncommitted changes predated run-03.

## Release preparation and rollback

There was **no deployment**. For later authorized release, preserve order: backend migration/adapter/Worker and normal API health in the intended private environment first; then publish matching iOS build with source/config/manifest fingerprint checks. Do not infer production capability from local HTTP 200 or simulator results. Roll back only run-03 modified code blocks (delivery observation and blocked-scene ownership, diagnostic callback/store, prepared model request, in-memory clock, typed capacity splitter); retain prior B6/B7/B8 and unknown-write guards. Never delete frozen commands, checkpoints, outbox, failed Run coordinates or candidate/formal history, and do not replay old unknown POSTs.

Current closure corrections: the inverse-N1 isolation red/green is now retained; the 46 PoolClosed errors were traced to a wrong fixture and all 44 affected methods plus 180 related module tests pass under the existing memory contract fixture on both old/current sources. The seven route-inventory failures remain old-HEAD baseline exceptions, so unfiltered backend discovery is not green. [Final-08](final-08/artifacts/runner-complete.json) replaced final-06 after the protected test changed. The directed PostgreSQL cold-start matrix covers enumerated branches, not every cross-product permutation. External `NOT_RUN`: real Provider/cost P10, deployment, iPhone short/physical20/physical65, physical-time I14, acoustic K05, historical seq5 reprocessing. Local pass does not authorize those stages.

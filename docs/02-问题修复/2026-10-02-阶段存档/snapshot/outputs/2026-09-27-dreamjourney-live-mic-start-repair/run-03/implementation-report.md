# DJ-LIVE-START-20260924 run-03 local continuation

Status: **LOCAL_INCOMPLETE**. The priority run-02 review gaps have local evidence, and the final-source iOS regression and four-scene preservation chain pass. Several original MIC combinations remain unexecuted; neither this report nor a healthy save chain closes the historical device incident. No real Provider, phone, deployment, production/history operation, or commit/push was performed.

## Scoped implementation

- `EchoViewController.swift`: queued success/error events retain the launch attempt, lifecycle and account identity captured at ingress. Execution on the main queue revalidates ownership before changing listening, failure, audio or capture state. The capture test seam now exercises the real Controller and temporary disk stores.
- `DialogEngineManager.swift`: the pending launch control used by the production Manager is compiled in Simulator and exercised with a controlled SDK boundary. This covers its operation ID, deadline, cancel, late SessionStarted and successful handoff decisions, **not** native SDK event timing on a phone.
- `AccountLease.swift`, `AppCoordinator.swift`, `DreamJourneyBackendClient.swift`: authority epoch updates carry a safe source tag. A runtime-config response captures its originating lease and cannot publish a stale epoch/policy after an account generation changes. The first MIC-11 failure was an `authorityEpochMismatch` with zero SDK starts; that is the immediate test failure, not proof of the September device incident's cause.
- `app/main.py`, `tests/test_voice_launch_diagnostics.py`: the bounded safe stage logger owns an output Handler and does not propagate through the synchronous business logger's Handler lock. A default-start ticket deny emits the same random trace at inbound, authorized and response stages. The existing finite queue and drop count remain.
- `OwnerTruthContractsTests.swift`: added controlled Controller queued-event red/green, disk-backed MIC-19 ownership, real Client and Controller two-POST MIC-08 unknown-result paths, runtime callback MIC-11 red/green, unwritable diagnostic disk, and Manager control assertions. Additional original-matrix cases cover local/refreshed deny (MIC-03), account rotation during held verified-401 recovery (MIC-07), controlled 429/decode/snapshot failures (MIC-13), and another request overwriting `latestDecision` before this Controller's MIC-14 terminal diagnostic. The last MIC-08 fixture initially fired before `taskResumed`; an in-flight completion barrier fixed test ordering without changing the business assertion. Its failed and passing xcresults are both retained.

## Red/green and diagnostic evidence

| Case | Red evidence | Green evidence | Boundary |
|---|---|---|---|
| Queued A success/error after B begins | `evidence/mic05-queued-red-clean.xcresult`: both business assertions failed | `evidence/mic05-queued-green.xcresult`; latest full bundle | Real Controller, Gate, Client and controlled transport; B's own event still enters listening |
| Old runtime response after account switch | `evidence/mic11-old-runtime-business-red.xcresult`: stale success accepted and new epoch/policy overwritten | `evidence/mic11-old-runtime-green.xcresult`; latest full bundle | Real BackendClient/runtime callback; no fabricated SDK timeout |
| Second ticket exposed after verified first pre-handler 401 | Prior run-02 request-ordinal business red; run-03 `evidence/mic08-controller-combined.xcresult` records a fixture-order failure, **not** a product red | `evidence/mic08-controller-exposure-barrier.xcresult`; latest full bundle | Real Controller/Client/Gate/transport, at most two POSTs and zero SDK starts after unknown second result |
| Default stage output and shared Handler lock | Independent run-02 review probes for silent default logger and shared-lock blocking | `evidence/default-uvicorn-ticket.log`, `evidence/backend-affected-green.log` | Actual default logger configuration and default ticket deny; does not prove historical minute-long delay |

The MIC-01 original red/green remains the run-02 inverse-D2 local behavior reconstruction, not a complete historical checkout. See [MIC matrix](mic-matrix.md) for every original ID and its exact local limit.

## Final-source execution

- Full iOS Simulator: `evidence/all-ios-final-03.xcresult`, **770 passed, 0 failed, 3 skipped** (the three separately executed save-chain entrances), exit 0. Simulator iPhone 17 Pro, iOS 26.5. The earlier 766/0/3 bundle is retained but predates the four additional MIC test methods.
- Unsigned generic iOS target: `evidence/generic-ios-final-03.log`, exit 0. Both repositories pass `git diff --check`.
- Backend affected tests: `evidence/backend-affected-post-resume.log`, 29 passed. Isolated PostgreSQL schema/probes and safe trace samples are in `evidence/pg-schema-verify.log`, `evidence/mic16-{metrics,auth,pool}-local.log`, and `evidence/default-uvicorn-ticket.log`. The clean probe enforces a dedicated synthetic DSN and pre-import non-loopback deny.
- Same-source preservation chain: `four-scene-final-03/artifacts/runner-complete.json` passed in `logical20-short -> logical20 -> logical65-short -> logical65` order. Both short gates had a second supplementary round and real iOS candidate visibility, review, formal-memory creation and API-process-restart readback. The 20-minute logical run used 110 user + 110 assistant turns, 4 long candidates and 5 total formal memories including the short gate. The 65-minute logical run used 150 user + 150 assistant turns, 17 long candidates and 18 total formal memories. Model HTTP was controlled loopback; these are not physical durations. Final-01/02 remain historical because the MIC test target changed.
- `evidence/final-source-build-link-03.json` recomputed 1,297 protected dependencies and 312 artifacts with no mismatches and `artifactMatchesCurrentSourceAndBuild=true` for final-03.
- The three backend route-inventory failures are independently reproduced on the old HEAD and current tree in the same local environment: `evidence/route-baseline-head.log` and `evidence/route-baseline-current.log` (each 3/3 failures, 259 vs 260). This is a scoped baseline exception, **not** a claim that the full backend suite passes or that PostgreSQL contract tests are memory-fixture tests.

## Remaining assertions and stop line

Original MIC rows still partial are listed individually in [mic-matrix.md](mic-matrix.md). In particular, a controlled full production-Manager-through-SDK adapter path, the exact MIC-19 A-drain/B-fail combinations, cross-request `latestDecision` interference, several wait-stage and error-classification variants, and complete PG ticket binding/concurrency combinations do not yet have all required local assertions. A test count or the four healthy scenes cannot promote them to PASS. The accurate local state is therefore **LOCAL_INCOMPLETE**, not a release recommendation.

Historical near-minute startup segment, the 23:04 single-request causal chain and the first seq5 trigger remain unresolved. Real Provider, native SDK ordering, physical audio/acoustics, deployment, and device short/20/65-minute verification are separately **NOT_RUN**. The [device runbook](device-runbook.md) is preparation only.

No save-chain budget, unknown-ticket retry rule, B7, or prior end/ACK/admit and recovery code was intentionally broadened. A later rollback should isolate the queued-event ownership guard, stale runtime callback lease guard, Manager shared launch control and new safe stage logger; it must not delete stored conversation coordinates or restore unknown-write replay. Exact source/build/config fingerprints and commands are in [fingerprints.md](fingerprints.md).

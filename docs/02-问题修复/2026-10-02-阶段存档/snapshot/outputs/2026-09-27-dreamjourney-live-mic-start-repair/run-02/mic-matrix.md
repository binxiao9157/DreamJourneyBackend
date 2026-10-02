# MIC-01–19 local acceptance matrix (run-02)

`PASS` applies only to the stated local assertion. `PARTIAL` is not a whole-row pass. Evidence paths are under `/Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-02/`; run-01 evidence remains preserved.

| ID | Status | Executed evidence | Remaining original assertion |
|---|---|---|---|
| MIC-01 | PASS (controlled boundary) | Full Controller/BackendClient/FeatureGate/requestJSON/controlled transport inverse-D2 red `evidence/mic01-controller-old-route-red.xcresult`, same assertion green `evidence/mic01-controller-green-02.xcresult`; one ticket, SDK boundary once, listening | Native SDK runtime belongs to MIC-10/11 and external test |
| MIC-02 | PARTIAL | Expired cache run-01 `mic02-03-07`; missing cache red/green `evidence/r2-r4-business-red.xcresult` / `r2-r4-green.xcresult` | All total-deadline and late-refresh combinations |
| MIC-03 | PARTIAL | Current deny, zero ticket `run-01/evidence/mic02-03-07.xcresult`; real Gate retained | Local disabled and refreshed deny full Controller variants |
| MIC-04 | PARTIAL | Account rebind at permission wait and refresh generation red/green `evidence/r2-r4-business-red.xcresult` / `r2-r4-green.xcresult`; run-01 rotation | Shared refresh waiter and all notification timings |
| MIC-05 | PARTIAL | A late callback while B still starting red/green `evidence/mic05-starting-green.xcresult` / `mic05-starting-green-02.xcresult`; previous B-listening case retained | Production native SDK ordering and success/deadline race |
| MIC-06 | PARTIAL | Run-01 repeated permission completion, ticket/SDK each once | Concurrent distinct failures and click storms |
| MIC-07 | PARTIAL | Run-01 canonical pre-handler 401 max one auth recovery, noncanonical no replay, deny after refresh | Account-switch and after-write 401 complete chain |
| MIC-08 | PARTIAL | First 503 exposed POST no replay run-01; second request exposure after canonical 401 red/green `evidence/r2-r4-business-red.xcresult` / `r2-r4-green.xcresult` | Lost response and connect interruption with late callback |
| MIC-09 | PARTIAL | Run-01 real Controller pending-ticket deadline and late 200 isolation | Policy/runtime/auth/SDK all hanging, transport cancellation receipts |
| MIC-10 | PARTIAL | Run-01 Controller sync configure crossing 15 s, zero StartEngine; production Manager validity guards compiled | Real Manager/device SDK setup crossing deadline not executable in Simulator |
| MIC-11 | PARTIAL | Run-01 deterministic missing-SessionStarted; this run `evidence/mic11-repeat10.xcresult` 10/10 and pair `mic10-11-pair.xcresult` passed; second full bundle `all-ios-repeat.xcresult` 758/0/3 | First full bundle `all-ios-final-green.xcresult` timed out once; no real Manager StartEngine failure/late event runtime proof |
| MIC-12 | PARTIAL | MIC-01 no extra policy GET, formal-memory ticket binding controlled | Native hot-path timing and all snapshot dimensions |
| MIC-13 | PARTIAL | Run-01 DNS domain/code and 503; safe request stage under default logging `evidence/backend-stage-level-red.log` / `backend-stage-level-green.log` | 429, decode, snapshot, UI classification matrix |
| MIC-14 | PARTIAL | Run-01 first failure/restart; run-02 backend logging exceptions/queue and stage-level tests `evidence/backend-targeted-final.log` | iOS unwritable diagnostic disk and cross-request latestDecision contamination |
| MIC-15 | PARTIAL | Default API lifespan, isolated PG ticket/401/rollback/commit/consume `evidence/mic15-pg-ticket-final.json` | Active/issued concurrency and all binding variants in real PG |
| MIC-16 | PASS (bounded local diagnostic) | Three finite child processes `evidence/mic16-d5-bounded-stalls-final.json` and thread stacks; heartbeat delay measured | Production-scale/global middleware diagnosis separately tracked; historical cause unknown |
| MIC-17 | PARTIAL | OwnerTruth 595/0/3 `evidence/ownertruth-final.xcresult`; selected audio run-01; local four-scene save chain | Native voice capture, long answer, interruption/return and sound on device NOT_RUN |
| MIC-18 | PASS (controlled local chain) | `four-scene-final-02/artifacts/runner-complete.json`; shorts before long, iOS candidate visible, review/formal/rebuild, controlled HTTP + isolated PG | Real Provider and physical-time/device scenes separately NOT_RUN |
| MIC-19 | NOT_RUN | No combined A-saving/B-denied and B-capture/SDK-failed disk-identity proof | Original A command, watermark, budget, outbox/follow-up/checkpoint unaffected; no A end/ACK/admit from B |

The local final-source chain is not a substitute for native SDK event-order evidence. The 3 skipped iOS tests are the separately executed cross-process short/20/65 chain, not counted in the 595 or 757 passed totals. No business write was replayed because of an unknown ticket outcome.

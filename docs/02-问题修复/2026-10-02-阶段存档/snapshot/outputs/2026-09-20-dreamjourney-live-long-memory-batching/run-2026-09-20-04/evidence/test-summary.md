# Run 04 test summary

## Business green paths

- R02-B-PARA controlled adapter probe: 5/5 scenarios succeeded; paraphrase case produced 11 proposals from 12 atoms after one valid supplement.
- Backend long-memory pipeline: 26/26.
- Candidate Worker: 64/64.
- Review and B7 affected regression: 54/54.
- Backend realtime/audio preservation: 23/23.
- DeepSeek parser targeted checks: 3/3.
- PostgreSQL formal chain: passed, schema 0122, 190 formal memories after short/long/F65 flows and store recreation.
- PostgreSQL concurrency/rollback: passed.
- PostgreSQL contract/retry: passed.
- iOS OwnerTruth: 532/532.
- iOS audio owner lease: 5/5.
- iOS long answer/interruption/PCM selected suites: 25/25.
- Simulator build: BUILD SUCCEEDED.
- Generic iOS build: BUILD SUCCEEDED.

## Preserved environment failures

- `backend-core-services-tests.log`: 165 executed, 41 errors because the broad test invocation did not open the global `dreamjourney-api` PostgreSQL pool.
- `backend-deepseek-adapter-tests.log`: 28 executed, 8 errors for the same pool lifecycle reason.
- These are not counted as product red tests. The affected contract was exercised by the targeted adapter tests and the three isolated PostgreSQL gates above.

## Not run

- Real Provider.
- Real iPhone and physical 20/65-minute sessions.
- Deployment, production data, historical replay, and Dead Letter handling.

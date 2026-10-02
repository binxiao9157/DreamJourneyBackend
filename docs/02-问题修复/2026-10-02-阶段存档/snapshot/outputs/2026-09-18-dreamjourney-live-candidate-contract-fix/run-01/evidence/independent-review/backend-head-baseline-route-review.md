# Backend route failure baseline verification

- Baseline: HEAD `ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc` exported through `git archive HEAD` into `/private/tmp/dj-route-baseline-elpyyrvs`; current repository unchanged.
- Existing repository `.venv/bin/python` used with `STORE_BACKEND=memory PYTHONPATH=.:tests`.
- Seven tests reproduced the exact same `259 / 260` failures on committed baseline. No production, network, device or business writes used.
- Baseline production/enforce startup check returned `routeCount=260`, `unclassifiedCount=0`, `enforcementMode=enforce`.
- Commit `f3ebc8a4937a5be8bc27762369d4193ea683d0ac` (2026-09-15 22:27 +0800) added `GET /v2/vaults/{vault_id}/interview-sessions/{session_id}/live-delivery-status` to the application and typed registry.
- The route has USER_SESSION ownership, USER authentication, `dreamjourney-user` audience, and `user:api` scope. This is an inventory expectation drift, not an unclassified route according to the existing startup validation.
- Seven old route-count failures are proven baseline failures, not a regression introduced by the current candidate extraction contract changes. This does not establish unrelated PostgreSQL/local gates as passing.

Evidence: `backend-head-baseline-seven-route-tests.log`.

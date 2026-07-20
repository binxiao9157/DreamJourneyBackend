# Owner Truth Interview Session State QA Read

## Purpose

This additive M0-A contract makes the existing private
`ConversationThread` / `InterviewSession` state observable to QA without
creating a public Echo surface or expanding any authority.

It is intentionally a diagnostic read boundary for the later natural-input
Echo slice. It does not expose messages, topic content, Sources, Candidates,
DecisionReceipts, MemoryVersions, provider output, or digital-human state.

## Endpoint

```text
GET /v2/vaults/{vault_id}/interview-sessions/{session_id}/state
```

The endpoint is omitted from OpenAPI and follows the existing Owner Truth QA
gate:

- `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true` on the server;
- a logged-in user session;
- `X-DreamJourney-QA-Owner-Truth: 1` request header;
- the actor must be the active owner of the target Vault.

When the QA gate is off, it returns `404 ownerTruthCandidateReviewUnavailable`.
An owner mismatch returns `403 ownerTruthInterviewSessionDenied`. Responses set
`Cache-Control: no-store`.

## Value-minimized response

```json
{
  "schemaVersion": "owner-truth-interview-session-state-read-v1",
  "vaultId": "vault-id",
  "session": {
    "state": "active|paused|ended",
    "boundary": "open|skipOnce|cooldown|doNotAsk",
    "rowVersion": 1,
    "threadVersion": 1,
    "ownerTurnCount": 0,
    "deepeningTurnCount": 0,
    "candidateBatchTurnCount": 0,
    "fatigue": "normal|guarded|exhausted",
    "hasPendingReviewBatch": false,
    "authorityEpoch": 0
  }
}
```

The response deliberately omits `sessionId`, `threadId`, `ownerSubjectId`,
`pendingReviewBatchId`, messages, topic values, source/candidate identifiers,
and every memory or activation field.

## Authority and release boundary

- The route is read-only and opens the same store unit of work as the existing
  private conversation repository.
- It cannot create or mutate a `Source`, `Candidate`, `DecisionReceipt`,
  `MemoryVersion`, extraction effect, publication, provider request, or media
  object.
- The public Echo view remains unchanged. A future iOS QA consumer may read
  this state, but the route is not a public product contract.

## Verification

G0 checks:

- `tests/test_owner_truth_interview_session_state_api.py` covers default-hidden
  behavior, owner-only read, no-store, value minimization and cross-owner deny.
- `tests/test_route_ownership_registry.py`,
  `tests/test_route_authentication.py`, `tests/test_auth_sessions.py` and
  `tests/test_runtime_capabilities.py` keep the `96`-route inventory aligned.
- `scripts/verify_backend.sh` runs the full unit/contract/static suite.

G2 check after deployment:

```bash
scripts/run-backend-owner-truth-conversation-postgres-smoke.sh
```

The existing disposable-Postgres smoke now calls
`OwnerTruthInterviewSessionReadService` after a store restart and verifies that
the state read preserves version fences and returns no pending review batch
after acknowledgement. It never touches the configured application database.

## G2 Deployment Evidence

The contract was deployed to the production-like Postgres environment at
backend revision `f7c7dab` on 2026-07-20.

- The API container was rebuilt and `/ready` returned `status=ready`, including
  database, schema, auth and incident readiness.
- `python scripts/migrate_db.py --verify --build-id f7c7dab` reported schema
  head `0034`, no pending migration and `status=ready`.
- `python scripts/backend-owner-truth-conversation-postgres-smoke.py` returned
  `owner_truth_conversation_postgres_smoke=passed`, including the state read
  after a store restart.
- `env PYTHONPATH=. BACKEND_BASE_URL=http://127.0.0.1:8080 python
  scripts/backend-route-authentication-postgres-smoke.py` returned `status=passed`
  with `routeCount=96`.

No server `.env` value or credential was read, changed or committed. The QA
gate remains default-off, so this endpoint is deployed but not public.

## Next Consumer Boundary

The next increment is an iOS typed QA-only consumer. It may display only the
value-minimized session state above, must discard results after an account lease
change, and must not connect the route to public Echo UI or persist it as a
memory artifact.

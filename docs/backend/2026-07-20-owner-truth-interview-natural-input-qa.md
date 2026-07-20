# Owner Truth Interview Natural-Input QA Command

## Purpose

This M0-A increment exposes a deliberately narrow, QA-only command boundary
for the existing private `ConversationThread` / `InterviewSession` lane. It
allows a later typed iOS QA client to start a `naturalInput` session and append
one owner narrative message without turning the public full-screen Echo into an
interview product surface.

It is not an AI answer endpoint. It does not invoke extraction, candidate
review, memory activation, provider generation, digital-human rendering, or
legacy Archive/KBLite writes.

## Hidden routes

```text
POST /v2/vaults/{vault_id}/interview-sessions
POST /v2/vaults/{vault_id}/interview-sessions/{session_id}/messages
```

Both routes are omitted from OpenAPI and require all of the following:

- `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true` on the server;
- an authenticated user session;
- `X-DreamJourney-QA-Owner-Truth: 1`;
- the calling user to be the active owner of the Vault.

When disabled, the routes return `404 ownerTruthCandidateReviewUnavailable`.
An owner mismatch returns `403 ownerTruthInterviewSessionDenied`. A stale
thread or session version returns `409 ownerTruthInterviewSessionConflict`.
Every response sets `Cache-Control: no-store`.

## Command and receipt boundary

The start route accepts only a client command id, thread id and session id. It
always creates the session in `naturalInput` mode. The append route accepts a
client command id, the explicit expected thread/session versions, a message id
and text. It always persists the message as `OWNER` + `NARRATIVE`.

The value-minimized receipt has schema version
`owner-truth-interview-session-command-v1`. It returns command outcome,
thread/session ids, new version fences, session state/boundary and message
metadata only. It never echoes message text or returns Sources, Candidates,
DecisionReceipts, MemoryVersions, extraction/provider state, or runtime
digital-human state.

The existing command idempotency and optimistic version checks remain the
authority for replay and concurrent-write handling.

## G0 verification

- `tests/test_owner_truth_interview_input_api.py` covers default-hidden
  behavior, owner-only access, idempotent start/append, value-minimized
  receipts, cross-owner denial and stale-version conflict handling.
- `tests/test_route_authentication.py` now reads the deployed route smoke
  constant and proves it equals the typed `RouteOwnershipRegistry` inventory.
  This prevents a new route from leaving the release smoke at a stale count.
- Targeted Owner Truth, route-authentication and runtime-capability tests
  passed (`22` tests), and `scripts/verify_backend.sh` passed with `999`
  tests.
- `git diff --check` passed before both code commits.

## G2 deployment evidence

The feature was introduced in `d6dc3b9` and the route-smoke inventory fix was
introduced in `bf83ace`. Production-like backend `main` was fast-forwarded to
`bf83ace` on 2026-07-20 and the `api` container was rebuilt.

- `/ready` returned `status=ready`, including database, schema, auth and
  incident readiness.
- In the deployed API container,
  `python scripts/backend-owner-truth-conversation-postgres-smoke.py` returned
  `owner_truth_conversation_postgres_smoke=passed`. The smoke creates and
  drops an isolated temporary database rather than using the application
  database.
- In the deployed API container, the route-authentication smoke returned
  `status=passed` with `routeCount=98`, zero unclassified routes and the
  expected anonymous/user/machine principal decisions.

No `.env` value, credential or server backup file was read, changed or
committed. The QA feature flag remains default-off, so deployment does not
expose a public interview entry point.

## Next boundary

The next M0-A increment is an iOS typed QA-only natural-input client. It must
capture the active `AccountLease`, discard stale callbacks, render no persisted
conversation text beyond the local input flow, and remain unlinked from public
Echo until its dedicated product gate is approved.

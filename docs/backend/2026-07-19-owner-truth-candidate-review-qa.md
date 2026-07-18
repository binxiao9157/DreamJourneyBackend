# Owner Truth Candidate Review QA Evidence

Date: 2026-07-19

## Scope

This records `WI-S1-01-04`, the internal owner-review boundary after a
Candidate has been created by `WI-S1-01-03`:

```text
pending Candidate -> owner accept | correct | reject -> immutable DecisionReceipt
```

The implementation is deliberately QA-only. It does not create a
`MemoryVersion`, activate a projection, publish an item, or expose a public
candidate-review feature. Those transitions remain owned by later work items.

## Implemented Contract

Two hidden, authenticated routes are available only when both the server flag
and QA request header are present:

```text
GET  /v2/vaults/{vaultId}/candidates
POST /v2/vaults/{vaultId}/candidates/{candidateId}/decisions
```

The command contract is:

```text
commandId
expectedCandidateVersion
action: accept | correct | reject
correctedValue (required only for correct)
reasonCode
```

The response returns only decision metadata and receipt hashes. Corrected
candidate content is never echoed from the review response.

## Access and Data Boundary

1. The feature defaults to disabled through
   `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=false`.
2. When enabled, a caller must additionally send
   `X-DreamJourney-QA-Owner-Truth: 1` and hold a user session. Otherwise the
   route returns a stable `404` and is omitted from the OpenAPI schema.
3. The authenticated user is the vault owner and decision actor. The Postgres
   repository rechecks vault ownership, candidate ownership, source liveness,
   epoch and optimistic version inside the same Unit of Work.
4. A terminal decision writes exactly one immutable `DecisionReceipt` in the
   same Unit of Work as the Candidate state transition. Replayed command IDs
   return the existing receipt; stale candidates, cross-vault access and
   invalid correction commands fail closed.
5. A corrected decision stores the correction as receipt/audit data only. It
   does not mutate the original proposal and it does not create a memory.

## Explicit Non-Goals

- No public iOS Candidate Inbox or review UI.
- No batch acceptance, including for sensitive/restricted Candidates.
- No `MemoryVersion`, KBLite fact, publication, training input or projection.
- No family/operator review authority.
- No automatic acceptance based on model confidence.

## Verification

G0 local evidence for implementation commit `44b06b0`:

- Focused candidate-review API/domain/route suite: 37 tests passed.
- `./scripts/verify_backend.sh` passed: 668 unit tests, FastAPI smoke,
  credential-boundary checks and existing contract checks.
- `git diff --check` passed before commit.

G2 deployed Postgres evidence:

- Server revision: `44b06b0`.
- `scripts/migrate_db.py --apply --build-id 44b06b0` reported schema head
  `0014` ready with no pending migration.
- `scripts/run-backend-owner-truth-postgres-smoke.sh` passed with:

  ```text
  candidateReviewConcurrentSingleWriter=true
  candidateReviewIdempotent=true
  correctedDecisionRequiresValue=true
  candidateCorrectionSeparate=true
  schemaHead=0014
  status=passed
  ```

  The smoke opens two independent Postgres connections against the same
  pending Candidate and proves one terminal writer/receipt wins while the
  competing writer receives a conflict.

- `scripts/run-backend-route-authentication-postgres-smoke.sh`, with
  `BACKEND_BASE_URL=http://127.0.0.1:8080` inside the API container, passed
  with `routeCount=80`; the new routes are user-session routes and reject
  anonymous and machine credentials.
- `https://dreamjourney-api.liftora.cn/ready` reported database, schema, auth
  and incident components ready after deployment.

## Gate Disposition

`WI-S1-01-04` is `INTERNAL_READY` for its G0/G2 boundary. G1 remains open:
the planned iOS hidden Candidate Inbox belongs to `WI-S1-03-04` and must not
be exposed before the downstream MemoryVersion and composition work exists.
G4 also remains open because review is QA-only and not release-enabled.

The next direct Owner Truth slice is `WI-S1-01-05`: accepted/corrected
decisions may create immutable `MemoryVersion` records under a separate
transactional contract. Reject must continue to create no memory.

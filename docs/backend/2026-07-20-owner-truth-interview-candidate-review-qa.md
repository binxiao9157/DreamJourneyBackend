# Owner Truth Interview Candidate Review QA Contract

Date: 2026-07-20

## Purpose

This document records the QA-only adapter for the M0-A interview path:

```text
acknowledged interview batch
  -> synthetic Candidate proposals
  -> value-minimized review composition
  -> owner review decision / immutable receipt
  -> no MemoryVersion activation
```

It closes the gap between the existing M0-A composition and decision services
and a typed mobile client without reusing the generic Candidate Inbox route.
The generic Inbox has different activation semantics and must remain separate.

## Hidden API Surface

All routes are omitted from OpenAPI and return `404` unless both controls are
present:

1. server setting `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`; and
2. authenticated user request header `X-DreamJourney-QA-Owner-Truth: 1`.

The authenticated actor must be the active Vault Owner. Family, operator,
machine and anonymous callers are not review authorities.

```text
GET  /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/candidate-review
POST /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/candidate-review/batch-accept
POST /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/candidate-review/candidates/{candidateId}/decision
```

The read response uses schema
`owner-truth-interview-candidate-review-read-v1` and partitions only current
pending candidates into `batchCandidates` and `singleCandidates`. It joins the
value-minimized composition with the canonical Candidate state inside one Unit
of Work, so a terminal candidate cannot be rendered as pending.

## Decision Rules

### Standard batch candidates

`batch-accept` accepts an explicit, non-empty selection of Candidates with
their expected optimistic versions. Each selection must be in the composition
batch lane. A sensitive or explicit-single candidate receives `409
ownerTruthInterviewCandidateSingleReviewRequired` instead.

### Sensitive or single candidates

The individual decision endpoint accepts `accept`, `correct`, or `reject` for
one Candidate in the composition single lane. A batch-lane Candidate cannot
bypass the partial-batch boundary through this endpoint.

### Non-promotion invariant

Both decision responses include:

```json
{
  "memoryActivation": {
    "status": "notApplicable",
    "memoryVersionCreated": false
  }
}
```

They call the existing Candidate CAS/DecisionReceipt repository directly and
never call `activate_memory_version`. A replayed `commandId` returns the
existing root command/receipt outcome; it cannot change the selected subset or
the terminal decision.

## Security and Runtime Boundaries

- All three routes are registered as `user-session` routes in the ownership
  registry. Route authentication inventory is now `95` routes.
- Owner vault, source liveness, authority epoch, candidate version and review
  batch provenance are rechecked in the same Unit of Work as the decision.
- Candidate response bodies are sensitive and use `Cache-Control: no-store`.
- The public Echo UI and public archive UI do not discover or expose this
  contract.

## Verification

G0 local verification includes:

- `tests/test_owner_truth_interview_candidate_review_api.py`: default-hidden,
  owner-only, partial standard acceptance, sensitive single review, replay,
  stale/invalid path and no-MemoryVersion assertions.
- Existing composition, batch-decision, single-decision, generic Candidate
  Inbox, route-authentication and runtime capability suites.
- `scripts/verify_backend.sh`, including the full unittest suite and contract
  gates.

## G2 Deployment Evidence

The implementation was deployed to the production-like Postgres environment at
backend revision `81be076`.

- API container rebuilt and `/ready` returned `status=ready` with database,
  schema, auth and incident components ready.
- `scripts/migrate_db.py --verify --build-id 81be076` reported schema head
  `0034`, no pending migration and `status=ready`.
- `scripts/run-backend-owner-truth-conversation-postgres-smoke.sh` returned
  `owner_truth_conversation_postgres_smoke=passed`, including the read join
  before, during and after terminal interview review.
- `scripts/run-backend-route-authentication-postgres-smoke.sh` returned
  `routeCount=95`, `userRouteAllowed=true`, `machineBusinessRouteDenied=true`
  and `status=passed`.

The QA flag remains default-off in the deployed environment. The new
interview-review routes are present and authenticated, but not publicly
discoverable or release-visible.

## Deployment Commands

- `scripts/run-backend-owner-truth-conversation-postgres-smoke.sh` verifies
  the canonical Postgres read join before, during and after terminal review;
- `scripts/run-backend-route-authentication-postgres-smoke.sh` verifies the
  `95`-route production registry and enforcement contract;
- `/ready` remains the deployment readiness gate.

## Explicit Non-Goals

- No public Candidate review screen.
- No automatic Candidate acceptance.
- No `MemoryVersion`, projection, KBLite fact, publication, provider call or
  digital-human training input.
- No cross-account/family review authority.

## Subsequent route inventory note

The deployment evidence above correctly recorded `95` routes for revision
`81be076`. The later QA-only interview-session state read was added in
`dffb6c3`, increasing the current typed route inventory to `96`; the deployed
route-authentication smoke follows that current count.

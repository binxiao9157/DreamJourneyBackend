# Owner Truth Correction Resolver Shadow

Date: 2026-07-18

## Scope

This completes the QA-only correction lane without changing the public Echo,
legacy Archive, KBLite production read path or iOS behavior.

```text
Answer + Citation + current MemoryVersion
  -> private correction Source
  -> pending correction Candidate
  -> Owner corrects or rejects
  -> same Memory: v1 superseded, v2 current
  -> immutable outdated-Answer event
  -> value-free projection rebuild intent
```

A correction never creates a second `MemoryRecord`. Its accepted replacement
is `v(N+1)` on the cited record and retains the predecessor evidence alongside
the correction Source provenance.

## Hidden QA Contract

The endpoint is hidden from OpenAPI and requires all existing Owner Truth QA
controls: enabled backend QA switch, authenticated Owner session and
`X-DreamJourney-QA-Owner-Truth: 1`.

```text
POST /v2/vaults/{vaultId}/correction-requests/{correctionRequestId}/resolve
```

Input:

- `commandId`
- `expectedCandidateVersion`
- `expectedMemoryVersionId`
- `action`: `correct` or `reject`
- `correctedValue` and `correctedValueSchemaVersion` only for `correct`
- `reasonCode`

The response is value-free: it exposes terminal decision IDs, version lineage,
content hash, stale-Answer event ID and optional effect receipt. It never
returns correction text, corrected content, answer content or source payload.

## Database Rules

Migration `0022_owner_truth_correction_resolver` is additive and default-off.

- `owner_truth.correction_resolutions` binds exactly one pending request,
  correction Candidate, DecisionReceipt and terminal decision.
- A corrected resolution must reference a successor that is v(N+1) of the
  cited `MemoryRecord`; v1 is retained but no longer current.
- `owner_truth.answer_outdated_events` binds the exact Answer/Citation to the
  superseded and replacement versions.
- Resolver rows and outdated events are append-only.
- A correction request can leave `pending` only when its matching immutable
  resolution exists. Direct status changes fail.
- Stale targets fail before a second Candidate decision is persisted; the
  Postgres path also keeps the decision, successor and effect write inside one
  Unit of Work.

## Verification

Before deployment:

```bash
STORE_BACKEND=memory PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_correction_request \
  tests.test_owner_truth_candidate_review_api \
  tests.test_owner_truth_migration_contract
./scripts/verify_backend.sh
git diff --check
```

For Postgres, run the disposable smoke against the same container image and
environment used for deployment:

```bash
DATABASE_URL='<server postgres dsn>' scripts/run-backend-owner-truth-postgres-smoke.sh
```

The smoke verifies schema head `0022`, same-record lineage, idempotency,
stale-resolution rollback, outdated-Answer evidence, append-only guards and
derived projection rebuild.

## Deployment Evidence

Deployed on 2026-07-19 (Asia/Shanghai).

- Backend commits: `daaa5cc`, `e0e730b`, `79e9377`, `018da5b`.
- The server applied migration head `0022`; `migrate_db.py --verify` reported
  `status=ready` with no pending migrations.
- The disposable Postgres Owner Truth smoke passed against the deployed API
  container. It proved same-record v1 -> v2 lineage, one-current enforcement,
  idempotent resolution, stale-request fail-closed behavior, immutable
  outdated-Answer evidence, and projection rebuild.
- The deployed route-authentication smoke passed with `routeCount=88`: public
  runtime is allowed, anonymous access to user routes is denied, machine
  credentials cannot use user routes, and user sessions cannot use machine
  routes.
- `/ready` reported `database`, `schema`, `auth`, and `incident` as ready.

The resolver remains QA-only and default-off. Deployment does not promote
Owner Truth, KBLite, Archive, Echo, or iOS UI reads to public authority.

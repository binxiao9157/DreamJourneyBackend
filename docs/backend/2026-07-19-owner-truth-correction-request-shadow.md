# Owner Truth Answer Correction Request Shadow

Date: 2026-07-19

## Scope

This is the request half of `WI-S1-01-08`: an Owner can report that a specific
answer citation is wrong without allowing a model, a generic review command or
the request itself to overwrite an authoritative MemoryVersion.

```text
Answer -> immutable Citation -> Correction Request -> private correction Source
       -> pending correction Candidate -> dedicated resolver (future slice)
```

The current slice stops at `pendingReview`. It neither creates a replacement
MemoryVersion nor changes the public Echo, legacy Archive, KBLite or iOS
behavior.

## Hidden Contract

The route is hidden from OpenAPI and requires the existing Owner Truth QA
switch, an authenticated Owner session and `X-DreamJourney-QA-Owner-Truth: 1`:

```text
POST /v2/vaults/{vaultId}/memories/{memoryId}/corrections
```

Input fields are:

- `commandId`
- `answerId`
- `citationId`
- `expectedMemoryVersionId`
- `correctionText`
- `reasonCode`

The response contains only identifiers, hashes, lengths and `pendingReview`.
It deliberately never returns raw correction text, source text, answer text or
MemoryVersion content, and sends `Cache-Control: no-store`.

## Safety Rules

Migration `0020_owner_truth_correction_requests` adds a default-off,
additive `owner_truth.correction_requests` ledger.

- The cited Answer, Citation, Memory, current MemoryVersion and original
  Source must all still belong to the active Owner Vault and have matching
  authority/source/version/content-hash state.
- A request creates a private immutable text Source for the correction text;
  only its hash and length are returned by the QA contract.
- The request creates exactly one pending Candidate whose content is the
  currently cited typed content. The correction request metadata points to the
  cited version and private correction Source.
- `(vault_id, command_id_hash)` is idempotent. Same command and same payload
  deduplicates; changed payload conflicts.
- Correction evidence fields cannot be changed or deleted at the database
  layer. The `status` field is intentionally reserved for the later dedicated
  resolver.
- `reviewMode=correction` is rejected by the existing generic Candidate
  decision-and-initial-activation path. This prevents a correction from
  accidentally creating an unrelated second MemoryRecord.

The next slice must add the correction-specific owner decision endpoint. It
must revalidate the requested version, create a new version of the same Memory,
mark the cited Answer outdated through an append-only event and request a
projection rebuild in the same transaction.

## Verification

### Local G0

- Unit tests cover request creation, exact Answer/Citation binding, idempotent
  replay, stale citation rejection, Owner boundary, value-free summaries and
  generic activation blocking.
- QA API tests cover default-hidden behavior and a value-free authenticated
  request response.
- Migration contract tests verify the default-off additive schema and that the
  migration does not update `memory_versions` directly.
- `scripts/backend-owner-truth-postgres-smoke.py` now exercises request/replay,
  generic-activation rejection and immutable request enforcement in a
  disposable Postgres database.
- `./scripts/verify_backend.sh`, Python compilation and `git diff --check`
  must pass before deployment.

### Deployment G2

Pending the backend commit/deployment for migration `0020`. After deployment,
run `scripts/run-backend-owner-truth-postgres-smoke.sh` against the server
Postgres administrator connection and verify `/ready` reports the new schema
head. The public Owner Truth QA switches remain disabled by default.

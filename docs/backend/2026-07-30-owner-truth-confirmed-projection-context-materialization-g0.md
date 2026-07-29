# Owner Truth Confirmed Projection Context Materialization G0

## Scope

This evidence records one local G0 sub-slice of `WI-S1-01-06`: a bounded,
default-off server-side context materializer for current confirmed Owner Truth
Memory Projections.

It does **not** cut over the public `POST /context/build` route, change Echo
reply input, promote a public feature, migrate data, call a Provider, deploy,
or claim retrieval quality. Legacy KBLite remains a compatibility path and is
not an Authority.

## Implemented Boundary

`OwnerTruthContextMaterializationService` has one narrow responsibility:

1. Reuse `OwnerTruthContextShadowBuildService` to select only policy-eligible,
   current confirmed Projection citations.
2. Re-read the current Projection and require the same vault, authority epoch,
   checkpoint, typed citation, source reference, version and content hash.
3. Render only the V1 confirmed content field for the supported memory kind:
   `experience.summary`, `knowledge.claim`, or `emotion.label`.
4. Bound the internal `generationContext.text` to 4096 characters.
5. Emit a content hash, typed citations, source count and truncation metadata
   for diagnostics without serializing the text outside the process.

The value-bearing text is intentionally available only to a future server-side
conversation adapter. `context_materialization_summary(...)` and the HTTP
response contain no raw query, memory body, answer text or provider payload.

## Failure Policy

- No ready Projection, a changed authority epoch/checkpoint, or a selected
  citation that no longer matches the current Projection returns an empty
  generation context and a no-personal-memory fallback.
- Query-ranked selection can only narrow already eligible confirmed citations;
  it cannot reintroduce unmatched, restricted, cross-vault, Candidate or
  stale content.
- The legacy Context packet is never read by this service.
- The QA route is hidden from OpenAPI and requires the existing Owner Truth
  QA feature gate, `x-dreamjourney-qa-owner-truth: 1`, and a self-owner user
  session. Its response is `Cache-Control: no-store`.

The hidden metadata route is:

```text
POST /v2/vaults/{vault_id}/context-shadow/materialize
```

It returns only a value-free summary. It is not a conversation or retrieval
API and must not be used as a public client contract.

## Authentication Inventory

The new route is registered as a `USER_SESSION` route under the
`ownerTruthContextMaterialization` policy. The authoritative route count is
therefore 120, including the deployed route-authentication smoke expectation
and runtime capability tests. This prevents production enforce mode from
silently accepting a route inventory drift.

## Local Verification

The following passed locally:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_context_shadow \
  tests.test_owner_truth_candidate_review_api \
  tests.test_route_ownership_registry -v

PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

`verify_backend.sh` completed all 1460 backend unit tests plus its existing
G0 contract and FastAPI smoke checks. The focused tests verify confirmed-only
materialization, query narrowing, fail-closed Projection absence, hidden API
gating, value-free responses and route-authentication inventory consistency.

## Open Gates and Next Boundary

`DATABASE_URL` is not configured in this local environment, so this command
was intentionally not claimed as passed:

```bash
scripts/run-backend-owner-truth-postgres-smoke.sh
```

No G2 deployment or online Postgres evidence is included in this record.
Before any public Context/Echo cutover, a separate server-side conversation
port must consume this private materialization in-process, preserve typed
answer citations, and pass its own authorization, replay, privacy and
rollback gates.

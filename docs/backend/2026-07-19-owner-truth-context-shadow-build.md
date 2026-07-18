# Owner Truth Context Shadow Build Evidence

Date: 2026-07-19

## Scope

This is the first narrow execution slice for `WI-S1-01-07` (Owner QA Context
and Typed Citation).  It is a default-off, QA-only Context V4 build adapter.
It does **not** promote Owner Truth to the public Echo read path and does not
change the compatibility response of `POST /context/build`.

```text
active confirmed MemoryVersion
  -> ready Owner Truth Projection
  -> QA-only Context V4 shadow build
  -> typed citation proof / explicit fallback
```

The build adapter deliberately has no legacy Archive or KBLite store
dependency.  It cannot silently supplement an unavailable V4 projection with
legacy personal data.

## Hidden Contract

The following endpoint is hidden from OpenAPI and disabled by default:

```text
POST /v2/vaults/{vaultId}/context-shadow/build
```

It requires all of the existing Owner Truth QA boundary conditions:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`.
2. An authenticated Owner user session.
3. `X-DreamJourney-QA-Owner-Truth: 1`.

Normal releases keep the switch false.  The public `/context/build` route,
legacy KBLite reader, Archive reader and public Echo behavior remain
unchanged.

Its value-free response includes:

- `request.intent`, `queryHash`, `queryLength`, never the raw query.
- projection state, Vault ID, authority epoch and checkpoint.
- `selectedContext` and `filteredContext` with typed MemoryVersion/Source
  citations only.
- deterministic `rankingTrace` marked `projectionCitationOrder`; this is not
  a relevance-ranking policy.
- `citationProof` proving every selected reference maps to the current
  confirmed projection entry.
- explicit `fallbacks` and counts.

No MemoryVersion content, Candidate proposal, DecisionReceipt rationale or raw
query is returned in the QA summary.

## Fallback and Access Rules

- If the projection is missing, stale or invalidated, the adapter returns no
  selected personal memory and
  `owner_truth_context_unavailable_no_personal_memory`.
- If the projection is ready but all records are ineligible, it returns
  `owner_truth_context_no_eligible_personal_memory`.
- An invalid or cross-Vault request is normalized to the projection access
  boundary (`403 ownerTruthMemoryProjectionDenied`); a Candidate-review
  repository exception must not become a 500 response.
- `legacyContextRead=false` is a deliberate invariant: V4 unavailability is a
  no-personal-memory fallback, not permission to read legacy private data.

## Verification

### G0 local

- `./scripts/verify_backend.sh` passed with **709** tests.
- Targeted tests cover default-hidden access, Owner-only access, cross-Vault
  denial, current typed citations, sensitivity filtering, raw query/content
  redaction and invalidated-projection fallback.
- `scripts/backend-owner-truth-postgres-smoke.py` now contains the matching
  isolated Postgres assertions for citation proof, value-free output and the
  no-personal-memory fallback.
- Python compilation and `git diff --check` are required before commit.

### G2 deployment

Pending this commit's server deployment.  The deployment acceptance is:

1. Run `scripts/run-backend-owner-truth-postgres-smoke.sh` against a disposable
   Postgres database and require `contextShadowBuildCitationProof`,
   `contextShadowBuildNoPersonalMemoryFallback` and
   `contextShadowBuildValueFree`.
2. Run `scripts/run-backend-route-authentication-postgres-smoke.sh` and require
   route registry count `85` with no unclassified routes.
3. Confirm `/ready` remains healthy.

## Gate Disposition

This adds scoped G0/G2 evidence for a Context V4 shadow **build** adapter only.
It does not complete `WI-S1-01-07`: public `/context/build` port separation,
iOS typed Context DTO/Echo evidence mapping, Answer/Citation persistence,
policy corpus breadth, performance/capacity testing, cohort promotion and G1/G3
evidence remain open.

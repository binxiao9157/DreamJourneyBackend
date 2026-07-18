# Owner Truth Candidate Synthetic Extraction Evidence

Date: 2026-07-19

## Scope

This records the scoped `WI-S1-01-03` foundation for the first authoritative
transition after Source creation:

```text
active Source -> ExtractionResult -> pending Candidate(s)
```

The implementation is intentionally provider-neutral and synthetic. It does
not enable a worker, call `/kb/extract`, invoke a model, create a
DecisionReceipt, create a MemoryVersion, write a Projection, or expose a
public API/UI.

## Implemented Boundary

1. `SyntheticCandidateExtractionCommand` derives a stable `ExtractionResult`
   identity from the immutable Source effect, content hash, processor/model,
   prompt version and policy version.
2. One result can create zero or more atomic Candidate proposals, each with one
   `memoryKind`, typed perspective/epistemic/sensitivity metadata, confidence,
   review mode, content hash, proposal hash and a Source `span` evidence ref.
3. Candidate content validates against the V1 ontology before persistence.
   Unknown/invalid schema is fail-closed; sensitive/restricted proposals require
   single-item review.
4. The application service rechecks live Source/vault owner, state, epoch,
   version and content hash inside the active Unit of Work. The Postgres adapter
   additionally verifies the span resolves against the current text Source.
5. The admitted path persists `extraction_results` plus `memory_candidates`
   with `decision_status=pending`, then records a typed Consumer Inbox/business
   completion in the same Unit of Work.
6. A stale/deleted/revoked Source creates no ExtractionResult or Candidate. It
   uses the existing typed blocked completion with the live reason code.
7. A retry with identical immutable meaning replays. Reusing the same stable
   extraction identity with different output fails closed instead of replacing
   the stored result.

## Verification

G0 local evidence for implementation commit `ae3d079`:

- `tests.test_owner_truth_candidate_extraction`
- `tests.test_async_effect_typed_consumer_completion`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` passed: 660 unit tests and all existing
  credential, FastAPI, knowledge, provider-boundary and backup smoke checks.
- `git diff --check` passed before commit.

G2 deployed Postgres evidence:

- Server revision: `ae3d079`.
- `scripts/migrate_db.py --apply --build-id ae3d079` reported schema head
  `0013` ready; this slice uses the predeclared Owner Truth tables and adds no
  migration.
- The disposable deployed smoke now creates and replays a synthetic result,
  verifies exactly one pending Candidate in PostgreSQL, verifies one Consumer
  completion, then continues to prove stale Source admission blocks the later
  completion path.

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true \
  candidateExtraction=true sourceTargetAdmission=true \
  sourceBlockedCompletion=true workerLease=true schedulerLease=true \
  consumerInbox=true rollback=true terminalGuard=true receiptsAppendOnly=true
  ```

- `https://dreamjourney-api.liftora.cn/ready` reported database, schema, auth
  and incident components ready after deployment.

## Gate Disposition

`WI-S1-01-03` is `INTERNAL_READY` at its scoped G0/G2 boundary. The G3 gate
remains open for a real Provider's quality, cost, region/privacy and failure
evidence. Synthetic results are only a deterministic internal contract and
must not be represented as real AI extraction.

The next Owner Truth slice is `WI-S1-01-04`: owner-scoped Candidate Inbox and
terminal accept/correct/reject decisions with one immutable DecisionReceipt per
Candidate. It must not create MemoryVersion or public projections before their
own work items and gates.

# Owner Truth SearchDocument Checkpoint Qualification Fix

## Issue

The additive `0046_owner_truth_search_document_projection` migration created
`owner_truth.validate_search_document_checkpoint()`. Its PL/pgSQL local named
`projection_source` collided with an unqualified selected table column of the
same name. A real PostgreSQL rebuild of the derived SearchDocument checkpoint
therefore failed with `AmbiguousColumn` and safely returned the owning
MemoryProjection worker job to retry.

The failure was found by the deployed-container, disposable
`backend-owner-truth-postgres-smoke.py` path. It did not write production
business rows.

## Fix

`0064_owner_truth_search_document_checkpoint_qualification` is an
append-only, compatibility `expand` migration. It replaces only the trigger
function with relation-qualified column references and `v_`-prefixed local
variables.

- It does not modify the immutable checksum of `0046`.
- It does not delete, rewrite, backfill, or expose any Source, Candidate,
  MemoryVersion, Projection, or SearchDocument data.
- All related QA/runtime flags remain default-off.
- The existing checkpoint ownership, epoch, state and source-checkpoint fences
  are unchanged.

## Verification

Before deployment:

```bash
STORE_BACKEND=memory .venv/bin/python -m unittest \
  tests.test_owner_truth_memory_search_projection_migration_contract -v
./scripts/verify_backend.sh
```

After applying `0064` on the deployed API container, rerun:

```bash
python scripts/migrate_db.py --verify --build-id <deployed-revision>
python scripts/backend-owner-truth-postgres-smoke.py
```

The smoke must return a completed MemoryProjection worker outcome and a
rebuilt/unchanged private SearchDocument projection using only a disposable
database.

The smoke constructs its UoW-bound conversation repository inside each start
and append transaction. This keeps its later InterviewTurnContext assertion on
the same Postgres execution boundary as the code it verifies.

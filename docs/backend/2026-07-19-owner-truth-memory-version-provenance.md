# Owner Truth MemoryVersion Provenance Shadow

Date: 2026-07-19

## Scope

`MemoryRecord` retains its original admission metadata for legacy compatibility.
This additive shadow migration makes every immutable `MemoryVersion` carry its
own source and decision lineage:

```text
MemoryVersion
  -> sourceId + sourceVersion
  -> decisionReceiptId
  -> supersedesVersionId (for v2+)
```

This is the prerequisite for the correction resolver. A correction-generated
v2 will cite its private correction Source and link to v1, instead of causing
new Projection/Citation records to silently reuse v1's source.

## Rules

- Existing v1 rows are backfilled only from their original `MemoryRecord`.
- New application activation writes source and receipt lineage explicitly.
- v2+ must name an earlier version of the same Memory as its predecessor.
- Version payload, content hash, source, receipt and predecessor linkage are
  immutable after insert; only `is_current` may move inside the existing
  one-current-version transaction boundary.
- Projection entries, Answer citations, correction requests and async
  projection target admission all read source provenance from `MemoryVersion`.
- The migration does not activate the correction resolver, alter public Echo,
  change iOS behavior or expose a new route.

## Verification

Before deployment run:

```bash
.venv/bin/python -m unittest tests.test_owner_truth_migration_contract
DATABASE_URL='<disposable-postgres-dsn>' scripts/run-backend-owner-truth-postgres-smoke.sh
./scripts/verify_backend.sh
git diff --check
```

After deployment, verify migration head `0021`, run the disposable Owner Truth
Postgres smoke and confirm `/ready` remains fully ready. The next slice can
then add the dedicated correction resolver without writing a second authority
or losing version-level provenance.

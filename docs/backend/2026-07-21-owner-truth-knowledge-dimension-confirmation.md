# Owner-Confirmed Knowledge Dimension Receipts

Date: 2026-07-21

## Scope

This narrow M0-B slice makes a knowledge-map dimension count only when the
Vault Owner has created a separate confirmation receipt for the exact current
`MemoryVersion` and content hash.

It does not add a public screen, modify Echo context, invoke a model, or store
raw memory text in a new table. It is QA-only and disabled by default.

```text
ready current MemoryVersion
  + exact content hash
  + Owner confirmation receipt
  -> eligible M0-B knowledge-dimension coverage
```

Embedded payload annotations, KBLite facts, and model labels never count as
Owner confirmation.

## Hidden QA Contract

The route is not included in OpenAPI:

```text
POST /v2/vaults/{vaultId}/memory-versions/{memoryVersionId}/knowledge-dimension-confirmations
```

It requires all of the following:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`.
2. `OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED=true`.
3. An authenticated Vault Owner session.
4. `X-DreamJourney-QA-Owner-Truth: 1`.

The command includes an opaque `commandId`, exact `expectedContentHash`, one
fixed dimension, the policy-defined facets, and a fixed explicit-selection
method. Responses are `Cache-Control: no-store` and contain receipt metadata
only.

## Persistence and Invalidation

Migration `0035_owner_truth_knowledge_dimension_confirmation_receipts` creates
an append-only ledger. It stores identifiers, hashes, dimension/facet metadata,
authority epoch and schema/method names only. It has no raw memory text,
provider output, user answer, or general JSON payload column.

- The database validates active Vault/Owner/authority, active standard
  knowledge memory, current version, and the exact content hash.
- `(vault_id, command_id_hash)` makes identical retries idempotent.
- `(vault_id, memory_version_id, dimension)` prevents conflicting duplicate
  receipts for the same version and dimension.
- Updates and deletes are rejected.
- A new current memory version automatically makes an older receipt ineligible
  for read coverage; historical data is not rewritten.

## Verification

Local G0:

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-owner-truth-knowledge-recommendation-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

Disposable Postgres G2 smoke (requires a database role that may create and
drop a temporary database):

```bash
DATABASE_URL='<admin postgres dsn>' \
  PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-knowledge-dimension-confirmation-postgres-smoke.sh
```

The disposable smoke proves route gating, receipt creation/replay, stale-hash
rejection, append-only enforcement, and automatic exclusion after the current
memory version changes. It does not write product data or enable QA flags in
the deployed runtime.

## Gate Disposition

This establishes a safe M0-B confirmation evidence boundary only. Public
knowledge-map UI, recommendation routing, broader owner review journeys, and
any Echo use remain outside this slice and must pass their own gates.

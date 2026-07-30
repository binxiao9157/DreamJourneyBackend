# Owner Truth Candidate Extraction Worker G0

## Scope

This slice closes the default-disabled execution boundary from a private
`ownerTruth.source.created` effect to a pending Owner Truth Candidate.

- Worker: `app/async_effects/owner_truth_candidate_extraction_worker.py`
- Input: active `owner_truth.sources` row, read only inside the worker UoW
- Output: immutable `ExtractionResult` and pending Candidate through the
  existing Candidate extraction service
- No public route, iOS UI, model call, Provider call, or MemoryVersion write

## Runtime Gate

All three switches are required, and all default to `false`:

```text
ASYNC_EFFECT_V1_ENABLED=true
ASYNC_EFFECT_WORKER_ENABLED=true
OWNER_TRUTH_CANDIDATE_EXTRACTION_WORKER_ENABLED=true
```

The worker is intentionally one-shot. It only claims
`ownerTruth.source.created` jobs whose target is `source/candidateExtraction`.

## Safety Rules

1. The worker live-rechecks Vault, owner, authority epoch, Source state, and
   Source version before reading private Source text.
2. It emits one restricted, inferred, single-review Candidate. This is a
   deterministic QA adapter, not semantic AI extraction and not a confirmation.
3. Source text is never included in worker JSON output, effect payloads, logs,
   or public API responses.
4. A stale, revoked, or deleted Source is terminally blocked without creating
   an ExtractionResult or Candidate.
5. Empty/corrupt text becomes a terminal quarantined ExtractionResult without a
   Candidate. Adapter/runtime errors release only the current lease for retry.
6. Candidate persistence, Consumer completion, and lease terminalization share
   the worker transaction. Replays retain the same immutable extraction and
   candidate identity.

## Local Verification

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-candidate-extraction-worker-g0-gate.sh
```

The standard `scripts/verify_backend.sh` also invokes this gate. No migration,
deployment, or production flag change is part of G0.

## G2 Disposable Postgres Smoke

`scripts/backend-async-effects-postgres-smoke.py` now enables the worker only
inside its randomly named disposable database. It verifies the typed effect
job, current Source read, restricted pending Candidate, typed consumer receipt,
terminal lease attempt, private worker output, and a subsequent idle rerun.
The script applies migrations to that temporary database and drops it only if
creation succeeded.

```bash
PYTHONPATH=. .venv/bin/python scripts/backend-async-effects-postgres-smoke.py
```

The runner must be able to resolve and connect to `DATABASE_URL` and create a
temporary database. This is not a deployment or production-worker enablement
command. Local static verification is present even when the workstation cannot
reach the container-only PostgreSQL hostname.

## Observability Status

The worker is explicitly cataloged as `NOT_INSTRUMENTED` in
`app/observability/operation_metric_coverage.py`. It therefore cannot support
an SLO or production-readiness claim until a value-free worker recorder is
attached in a later observability slice.

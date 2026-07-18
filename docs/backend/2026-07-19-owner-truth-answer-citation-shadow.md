# Owner Truth Answer/Citation Shadow Evidence

Date: 2026-07-19

## Scope

This is the second narrow execution slice for `WI-S1-01-07` (Owner QA Context
and Typed Citation). It adds default-off, QA-only persistence for immutable
Answer/Citation proof produced from the existing Context V4 shadow build.

```text
active confirmed MemoryVersion
  -> ready Owner Truth Projection
  -> Context V4 shadow build
  -> answer hash + typed citation ledger
```

This is not an Echo answer service. It does not call a model, does not store a
generated answer body, does not alter `POST /context/build`, and does not add
an iOS or public product surface.

## Hidden Contract

The endpoint is hidden from OpenAPI and disabled by default:

```text
POST /v2/vaults/{vaultId}/answer-citation-receipts
```

It requires all of the following:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`.
2. An authenticated Owner user session.
3. `X-DreamJourney-QA-Owner-Truth: 1`.

The command accepts an opaque `commandId`, an intent, a query and a transient
answer text. The response and persistence boundary expose only the following
proof metadata:

- `answerId`, `contextHash`, `contextVersion`, authority epoch and projection
  checkpoint.
- query hash/length and answer hash/length, never raw query or answer text.
- typed immutable `MemoryVersion`/`Source` citations, their content hashes and
  value-free fallback reasons.
- created/deduplicated outcome for idempotent command replay.

The endpoint sends `Cache-Control: no-store`.

## Persistence and Safety Rules

Migration `0018_owner_truth_answer_citations` adds append-only
`owner_truth.answers` and `owner_truth.answer_citations` ledgers.

- An answer row stores hashes, lengths, provenance and fallback metadata only;
  it has no raw query or answer column.
- A citation insert is accepted only when its Vault, Owner, authority epoch,
  active Memory, current MemoryVersion, Source version/state and content hash
  all match at insert time.
- Both tables reject update and delete operations through database triggers.
- `(vault_id, command_id_hash)` is unique. A replay with identical meaning is
  deduplicated; a changed payload for the same command is rejected.
- A missing, stale or invalidated projection produces the existing explicit
  no-personal-memory fallback. It cannot borrow legacy Archive or KBLite
  context.

## Verification

### G0 local

- Focused Answer/Citation, Context shadow, QA API, migration and route-registry
  suites passed.
- `./scripts/verify_backend.sh` passed with **713** unit tests, credential
  response-boundary tests, FastAPI smoke, knowledge smoke, deployment-file
  checks and backup contract smoke.
- `git diff --check` and Python compilation passed.
- The isolated Postgres smoke now verifies Answer/Citation creation,
  idempotent replay, hash-only output, forged-citation rejection and
  append-only enforcement.

### G2 deployment

Pending: deploy migration `0018` and run
`scripts/run-backend-owner-truth-postgres-smoke.sh` against a disposable
Postgres database on the deployment host. The normal runtime must continue to
keep Owner Truth QA switches and projection workers disabled.

## Gate Disposition

This is scoped evidence persistence only. It does not complete
`WI-S1-01-07`: public Context V4 routing, policy corpus breadth, iOS typed
Context/Echo evidence mapping, correction candidate creation, cohort rollout
and G1/G3 evidence remain open.

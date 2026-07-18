# Owner Truth Memory Projection Evidence

Date: 2026-07-19

## Scope

This records the first verified closure inside `WI-S1-01-06`: a default-off,
rebuildable Owner Truth projection over already-confirmed `MemoryVersion`
records.

```text
Source -> Candidate -> DecisionReceipt -> MemoryVersion
                                      -> Owner-only compatibility Projection
```

The projection does not replace legacy KBLite writes, `/context/build`, legacy
archive reads, or public Echo. It is not a new authority and does not expose a
public product surface.

## MemoryVersion Rebuild Intent

The same Owner decision transaction now also writes a value-free intent for a
future compatibility-projection rebuild:

```text
accepted | corrected Candidate
  -> immutable DecisionReceipt + active MemoryVersion
  -> async_effects operation/outbox/job/receipt (pending, disabled worker)
```

- `accept` and `correct` create one idempotent intent owned by the active
  `memoryVersion`, its version number and `authorityEpoch`.
- `reject` and `invalidated` create no intent.
- Command replay reuses the same effect operation; it cannot create another
  outbox record.
- The effect stores only the `contentHash`, opaque identifiers and state. It
  never stores the MemoryVersion payload, Candidate proposal, DecisionReceipt
  ID or review rationale.
- Writing the effect shares the same Postgres Unit of Work as the terminal
  decision and MemoryVersion activation. A failed effect write rolls all three
  changes back.
- The job remains `pending`. Neither the normal Compose deployment nor the
  public API starts a worker, rebuilds KBLite, invokes a provider, or changes
  Echo/context reads.

The registered names are intentionally explicit for a later read-only
consumer: `ownerTruth.memoryVersion.activated`,
`ownerTruth.memoryProjection.rebuildRequested`, and
`ownerTruth.memoryProjection.rebuild`.

## KBLite Compatibility Read

The first compatibility reader is now implemented, but remains default-off and
QA-only:

```text
active confirmed MemoryVersion
  -> ready Owner Truth projection checkpoint
  -> read-only KBLite compatibility graph
```

- It does not call legacy KBLite mutation APIs, write `/kb/sync`, or alter
  `/context/build`.
- It only maps an explicit current `memoryKind=knowledge` value with a valid
  `content.claim` into a compatibility `fact`.
- Experience, emotion, unsupported schema and empty claims are not guessed
  into facts, people, places or events. They produce a structured filtering
  reason without carrying their content.
- Missing or stale projection checkpoints return `state=rebuilding` with an
  empty graph. Disabled reads return `state=disabled` before reading the
  projection.
- Compatibility facts retain a typed citation to the immutable MemoryVersion
  and Source version. They do not copy Candidate payloads, DecisionReceipt
  identifiers or review rationale.
- The QA endpoint returns only a summary: fact identifiers, citation,
  confidence, filtering reason and checkpoint. It never returns fact text.

The three inspection endpoints are deliberately hidden from OpenAPI:

```text
GET  /v2/vaults/{vaultId}/memory-projection
POST /v2/vaults/{vaultId}/memory-projection/rebuild
GET  /v2/vaults/{vaultId}/kblite-compatibility
```

They require `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`,
`X-DreamJourney-QA-Owner-Truth: 1`, and an Owner user session. The public
release remains unaware of this adapter.

## Implemented Contract

- A projection reads only active, current `MemoryVersion` rows whose Vault,
  Owner, Source and `authority_epoch` still match.
- The snapshot is deterministic: the same current input set produces the same
  `sourceHash` and checkpoint. Rebuilding an unchanged snapshot reports
  `unchanged`.
- Missing checkpoints, changed MemoryVersion input, Source revocation, Vault
  owner/epoch mismatch or a corrupted stored projection return
  `state=rebuilding` with no entries. The service never serves a stale
  compatibility snapshot.
- Projection entries contain only the confirmed content and citations needed
  for a future compatibility reader. Candidate proposals, DecisionReceipt IDs
  and review rationale are excluded.
- Database triggers reject direct writes with a stale authority epoch, stale
  source/version/content, non-current version, or extra payload keys such as
  `decisionReceiptId`.

## Migration Correction

The initial additive migration `0016` created the tables and trigger, but the
first deployed Postgres smoke exposed a trigger SELECT/INTO mapping defect: it
omitted `memory_versions.schema_version` and attempted to parse the content
hash as JSONB.

The applied migration checksum was left untouched. Migration `0017` replaces
only the trigger function with the correct variable mapping and adds an
explicit content-schema equality check. This is an append-only repair, not a
rewrite of an already-recorded migration.

## Verification

### G0 local

- Focused projection/review/effect/compatibility/API suite passed, including
  default-hidden QA access, knowledge-only mapping, stale-checkpoint
  fail-closed behavior and value-free QA summaries.
- `./scripts/verify_backend.sh` passed with 686 unit tests, credential
  boundary tests, FastAPI smoke, knowledge checks, deployment-file checks and
  backup contract smoke.
- Python compilation, shell syntax checks and `git diff --check` passed.

### G2 deployed Postgres

Deployed backend head: `ddfc82e`.

- `migrate_db.py --apply --build-id 18ce3bd` reported no pending migrations;
  expected and applied schema heads are both `0017`.
- `scripts/run-backend-owner-truth-postgres-smoke.sh` passed in an isolated
  Postgres database with deterministic rebuild, corrected-content projection,
  source-revocation fail-closed behavior, stale-epoch rejection and
  DecisionReceipt-payload-leakage rejection all true. It additionally verified
  atomic/idempotent, value-free pending rebuild intents for accepted and
  corrected MemoryVersions, with no intent for a rejected Candidate. It now
  also verifies that KBLite compatibility maps only confirmed knowledge claims,
  fails closed after a stale checkpoint, and keeps QA summaries value-free.
- `scripts/run-backend-route-authentication-postgres-smoke.sh` passed with
  `routeCount=83`; the three QA-only routes remain user-session-only and do
  not change anonymous or machine access.
- `https://dreamjourney-api.liftora.cn/ready` reports database, schema, auth
  and incident components ready.

## Gate Disposition

This establishes scoped `G0/G2` evidence for the Projection foundation and
the first read-only KBLite compatibility adapter only. `WI-S1-01-06` remains
`PLANNED/STOP` in the Registry because its full scope still requires an
event-driven projection rebuild consumer, typed Citation/context integration,
correction flow integration and its remaining dependencies/G1 evidence.

The next safe closure is to use the typed compatibility citation in a
default-off context-read shadow path. It must not restore KBLite as a
fact-authority writer or make `/context/build` depend on a stale projection.

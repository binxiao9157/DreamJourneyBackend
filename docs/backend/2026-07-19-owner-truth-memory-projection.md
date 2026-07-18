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
- The job begins as `pending`. Neither the normal Compose deployment nor the
  public API starts a worker, rebuilds KBLite, invokes a provider, or changes
  Echo/context reads.

The registered names are intentionally explicit for a later read-only
consumer: `ownerTruth.memoryVersion.activated`,
`ownerTruth.memoryProjection.rebuildRequested`, and
`ownerTruth.memoryProjection.rebuild`.

## Default-Off Projection Worker

The typed rebuild consumer is now implemented but remains operationally
disabled by default. It is not part of the public API process or the generic
Compose shadow-worker profile.

```text
pending typed job
  -> claim one lease
  -> reload immutable intent
  -> lock/recheck Vault + MemoryVersion + Source authority
  -> rebuild derived checkpoint
  -> write value-free consumer receipt
  -> terminalize job/operation/outbox in the same UoW
```

It requires all three flags to be true:

```dotenv
ASYNC_EFFECT_V1_ENABLED=true
ASYNC_EFFECT_WORKER_ENABLED=true
OWNER_TRUTH_MEMORY_PROJECTION_WORKER_ENABLED=true
```

The normal deployment keeps all three false. A deliberate operator/QA run uses
`python -m app.async_effects.owner_truth_memory_projection_worker --once`.
The worker processes at most one job per invocation, never calls a Provider,
does not expose MemoryVersion content in its result, and does not change
`/context/build`, legacy KBLite writes, or public Echo.

Before it rebuilds, it locks and rechecks the active Vault, current
MemoryVersion, content hash, source state/version and authority epoch. A stale
or revoked target writes a terminal `blocked` consumer/coordination receipt;
an execution exception rolls the work transaction back and releases only that
lease to `retryWait`.

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

The four inspection endpoints are deliberately hidden from OpenAPI:

```text
GET  /v2/vaults/{vaultId}/memory-projection
POST /v2/vaults/{vaultId}/memory-projection/rebuild
GET  /v2/vaults/{vaultId}/kblite-compatibility
GET  /v2/vaults/{vaultId}/context-shadow
```

They require `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`,
`X-DreamJourney-QA-Owner-Truth: 1`, and an Owner user session. The public
release remains unaware of this adapter.

## Citation Context Shadow

The next compatibility slice adds a default-off, QA-only Context read shadow:

```text
ready Owner Truth projection
  -> citation-only selected/filtered Context trace
  -> QA inspection only
```

- The shadow does not call legacy KBLite, assemble generation text, change
  `/context/build`, or change public Echo behavior.
- It selects only current, Owner-visible, `standard` sensitivity MemoryVersion
  records. `sensitive` and `restricted` records remain value-free filtered
  evidence with `sensitivity_not_context_eligible`; they cannot be silently
  injected into a response.
- Each selected item carries a typed Source reference and immutable citation:
  `vaultId`, `sourceId`, `sourceVersion`, `memoryId`, `memoryVersionId`,
  `memoryVersion` and `contentHash`. It never returns the MemoryVersion
  content, Candidate payload, DecisionReceipt identifier or review rationale.
- Selection order is explicitly tagged as `projectionCitationOrder`. It is
  deterministic trace order, not a relevance score or a production ranking
  policy.
- Missing, stale or invalidated projection checkpoints return `rebuilding`
  with an empty selection. Disabled reads return `disabled` before reading the
  projection.
- The endpoint is Owner-session-only and uses the same explicit QA switch and
  header as the other Owner Truth inspection endpoints. It has its own
  unavailable/session error codes so a public client cannot infer an enabled
  feature from a normal response.

This is evidence for a future typed-Citation Context reader, not a cutover.
It leaves the existing Context Packet and public Echo source selection intact.

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
  fail-closed behavior, typed Context citations and value-free QA summaries.
- `./scripts/verify_backend.sh` passed with 706 unit tests, credential
  boundary tests, FastAPI smoke, knowledge checks, deployment-file checks and
  backup contract smoke.
- Python compilation, shell syntax checks and `git diff --check` passed.

### G2 deployed Postgres

Deployed backend head: `b5155ee`.

- `migrate_db.py --apply --build-id 18ce3bd` reported no pending migrations;
  expected and applied schema heads are both `0017`.
- `scripts/run-backend-owner-truth-postgres-smoke.sh` passed in an isolated
  Postgres database with deterministic rebuild, corrected-content projection,
  source-revocation fail-closed behavior, stale-epoch rejection and
  DecisionReceipt-payload-leakage rejection all true. It additionally verified
  atomic/idempotent, value-free pending rebuild intents for accepted and
  corrected MemoryVersions, with no intent for a rejected Candidate. It now
  also verifies that KBLite compatibility maps only confirmed knowledge claims,
  fails closed after a stale checkpoint, and keeps QA summaries value-free. It
  additionally verifies that the Context shadow selects typed citations,
  returns no MemoryVersion content in its QA summary and fails closed after an
  invalidated projection checkpoint.
- The same isolated smoke now enables the typed projection worker only inside
  its disposable database. It verifies two rebuild jobs terminalize as
  `completed/dispatched/succeeded`, write only their matching value-free
  consumer receipts, report deterministic `rebuilt` then `unchanged` outcomes,
  and block a stale Source target without rebuilding. The normal deployed
  process still leaves all worker flags `false`.
- `scripts/run-backend-route-authentication-postgres-smoke.sh` passed with
  `routeCount=84`; all four QA-only routes remain user-session-only and do
  not change anonymous or machine access.
- `https://dreamjourney-api.liftora.cn/ready` reports database, schema, auth
  and incident components ready.

## Gate Disposition

This establishes scoped `G0/G2` evidence for the Projection foundation, the
first read-only KBLite compatibility adapter, a default-off Citation Context
shadow and the default-off typed projection worker. The worker has current
scoped G0/G2 evidence but is not enabled in the normal deployment and is not a
public product capability.
`WI-S1-01-06` remains `PLANNED/STOP` in the Registry because its full scope
still requires a production-ranked typed Citation Context reader,
correction-flow integration, iOS cache-envelope/authority-epoch handling and
the remaining dependencies, G1 and G2 evidence.

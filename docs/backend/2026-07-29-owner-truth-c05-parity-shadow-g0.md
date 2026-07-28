# Owner Truth C05: Canonical Parity Shadow G0

## Scope

`WI-MIG-01-06` begins with a pure, default-off synthetic comparison contract:
`app/domain/owner_truth/migration_parity_shadow.py`.

It compares only opaque legacy/V4 hashes and enumerated dimensions across
`read`, `command`, `projection`, `context`, and `objectCopy` surfaces. It does
not query a legacy table, invoke a V4 route, create a target record, execute a
would-effect, copy an object, call a Provider, or change Authority.

## Mismatch taxonomy

The contract hard-codes the canonical promotion taxonomy from V4 section 28.4:

| Code | Dimensions | Gate behavior |
| --- | --- | --- |
| M01 | owner subject, Vault, principal, recipient | blocker; always zero |
| M02 | resource identity, legacy locator, deterministic target ID | blocker; always zero |
| M03 | visibility, grant, deleted/suspended/claim-pending state | blocker; always zero |
| M04 | terminal decision, active version, row/authority epoch | blocker; always zero |
| M05 | canonical content hash, version order, state transition | high; blocks promotion |
| M06 | source/evidence/citation lineage, object copy/state, command effect plan, Provider state/key and cost envelope | high; blocks promotion |
| M07 | count, sort, UTC time, pagination/cursor, projection checkpoint | high; blocks promotion |
| M08 | display normalization, non-authoritative sort, optional legacy metadata | reviewable only with a current bound approval |

Cost is deliberately M06 rather than M08. A cost difference that originates
from a Provider/effect path cannot be silently treated as a harmless display
normalization.

Each comparison window must contain a hash-only owner/Vault/authority scope
binding, references to its denominator and approved threshold source, plus an
exact expected sample denominator. The report rejects a partial or overfull
sample set instead of producing an unqualified percentage. M08 dimensions are
also rejected on `command` and `objectCopy` surfaces, so an operational
comparison cannot be mislabeled as a display-only difference.

## Allowlisted M08 differences

An M08 difference is ready only when exactly one allowance binds to that
observation hash and contains:

- an opaque reason code;
- an opaque Product/Data approval reference hash; and
- a future expiry timestamp.

M01–M07 cannot carry an allowance. An expired, missing, unknown, or
matching-observation allowance fails closed. The G0 contract proves binding
and expiry only; obtaining and retaining the actual Product/Data approval is a
separate external responsibility.

## Output boundary

The exported report has hashes, enums, counts and timestamps only. It reports
the following invariants explicitly:

```text
shadowOnly=true
cutoverAllowed=false
authorityEpochChanged=false
legacyWriterRetired=false
commandEffectExecutionCount=0
objectCopyExecutionCount=0
providerCallCount=0
providerCostCharged=false
writeOperationCount=0
```

This report cannot be used as an Authority-cutover decision. It is a C05 G0
synthetic corpus gate and it does not replace the C04 tail/effect mapping
report.

## Additive evidence persistence

`app/services/owner_truth_migration_parity_shadow.py` adds a separate,
default-off append-only ledger behind the existing store/UoW seam. Migration
`0049_owner_truth_migration_parity_shadow` persists only report hashes, sample
hashes, enum values, aggregate counts and an optional hashed M08 approval
reference. It never persists raw legacy/V4 values, identifiers, content,
Provider credentials or object locators.

The write path is fenced twice:

1. the service reads the active `vault_id + owner_subject_id + authority_epoch`
   and verifies the report's hash-only scope; and
2. the memory/Postgres repository reads that same authority again immediately
   before an append-only insert. Any owner, Vault or epoch change rejects the
   report rather than attempting a reverse lookup from `scopeHash`.

The SQL schema enforces active Vault ownership, the M01-M08 dimension/severity
taxonomy, M08-only approval fields, M08's `read/projection/context` surface
limit, exact mismatch counts, immutable rows and all zero-side-effect fields.
It contains no public route and no cutover switch.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-owner-truth-migration-parity-shadow-g0-gate.sh
scripts/run-backend-owner-truth-migration-parity-shadow-persistence-gate.sh
```

For the optional G2 disposable-Postgres smoke, provide a database role allowed
to create and drop a temporary database. It never uses the configured
application database directly:

```bash
RUN_OWNER_TRUTH_MIGRATION_PARITY_SHADOW_POSTGRES_SMOKE=1 \
  scripts/run-backend-owner-truth-migration-parity-shadow-persistence-gate.sh
```

The gate exercises all M01–M08 codes, match/mismatch determinism, exact
duplicate handling, immutable rebind rejection, denominator enforcement,
current/expired/missing M08 allowances, prohibition of M01–M07 waivers, and
the no-network/no-persistence import boundary. It is included in
`scripts/verify_backend.sh`.

## Deliberately deferred

1. Adapters that collect legacy and V4 results from a real production-shaped
   Postgres dataset.
2. iOS ViewState parity (C05 G1).
3. An approved production-shaped shadow window (C05 G2). The disposable
   Postgres smoke is available, but it has not been run against a real
   Postgres environment in this branch yet.
4. Any object copy, Provider request/query, command execution, Authority
   cutover, or legacy-writer retirement.

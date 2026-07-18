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

The projection does not replace KBLite, `/context/build`, legacy archive
reads, or public Echo. It is not a new authority and does not expose a public
product surface.

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

The two inspection endpoints are deliberately QA-only and excluded from the
OpenAPI schema:

```text
GET  /v2/vaults/{vaultId}/memory-projection
POST /v2/vaults/{vaultId}/memory-projection/rebuild
```

They require `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`,
`X-DreamJourney-QA-Owner-Truth: 1`, and an Owner user session. Responses are
summaries without projected memory content.

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

- Focused projection/migration/API suite: 16 tests passed after the trigger
  repair.
- `./scripts/verify_backend.sh` passed with 679 unit tests, credential
  boundary tests, FastAPI smoke, knowledge checks, deployment-file checks and
  backup contract smoke.
- Python compilation, shell syntax checks and `git diff --check` passed.

### G2 deployed Postgres

Deployed backend head: `f1f37c5`.

- `migrate_db.py --apply --build-id 9ac88e3` applied `0017`; expected and
  applied schema heads are both `0017`.
- `scripts/run-backend-owner-truth-postgres-smoke.sh` passed in an isolated
  Postgres database with deterministic rebuild, corrected-content projection,
  source-revocation fail-closed behavior, stale-epoch rejection and
  DecisionReceipt-payload-leakage rejection all true.
- `scripts/run-backend-route-authentication-postgres-smoke.sh` passed with
  `routeCount=82`; the two QA-only routes remain user-session-only and do not
  change anonymous or machine access.
- `https://dreamjourney-api.liftora.cn/ready` reports database, schema, auth
  and incident components ready.

## Gate Disposition

This establishes scoped `G0/G2` evidence for the Projection foundation only.
`WI-S1-01-06` remains `PLANNED/STOP` in the Registry because its full scope
still requires the KBLite compatibility adapter, rights/event-driven rebuild,
typed Citation/context integration and its remaining dependencies/G1 evidence.

The next safe closure is a read-only KBLite compatibility adapter that consumes
this projection behind a default-off flag. It must not restore KBLite as a
fact-authority writer.

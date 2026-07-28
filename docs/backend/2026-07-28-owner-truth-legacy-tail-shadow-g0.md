# Owner Truth C04: Legacy Tail Would-Run Shadow

## Scope

`WI-MIG-01-05` starts with a default-off G0 contract. It consumes a C03
immutable legacy backfill admission plan plus hash-only tail descriptors and
produces a deterministic mapping report for three future execution planes:

- asynchronous outbox/job admission;
- private object-reference reconciliation;
- provider-effect request/query/callback planning.

The report is a planning artifact. It does not create an async operation,
outbox row, job, object reference, provider effect, callback receipt, Source,
Candidate, MemoryVersion, or Authority transition.

## Input and Mapping Rules

Each input must bind to one C03 entry by `(domain, legacyIdHash, recordHash)`.
It carries only a collector-generated `tailCursorHash`, source version, channel
and any required opaque hashes. Raw tail cursors, object locators, payloads,
provider credentials, provider request bodies and callback bodies are rejected
by design.

- Only `requireIndependentLineageReplay`, `requireOwnerCandidateReview` and
  `requireEvidenceReview` entries can have a tail mapping.
- Quarantined and excluded entries cannot create a tail mapping.
- An outbox/job map creates only a simulated `AsyncEffectIntent.stable_key`.
- An object-reference map requires an opaque object reference hash; it does not
  run HEAD, COPY, PUT, DELETE or any storage request.
- A provider map must reference the existing provider catalog and include a
  callback-fixture hash. It builds only a simulated `ProviderEffectIntent` key;
  no provider request, query or callback processing occurs.
- Exact duplicate tail inputs are reported as duplicates. Rebinding the same
  semantic identity with a changed immutable input fails closed.

## Report and Gaps

The output includes a deterministic tail checkpoint and report hash, mapping
counts, missing outbox mappings, archive object-evidence gaps and unmapped
stable-provider catalog keys. It explicitly reports zero for:

- `effectExecutionCount`;
- `outboxWriteCount` and `jobWriteCount`;
- `objectStorageOperationCount`;
- `providerCallCount` and callback processing/acceptance counts.

An archive entry without an object-reference descriptor remains a gap. C04
does not infer that a legacy archive record has a valid media object. Likewise,
an unmapped provider capability remains a gap rather than being silently
treated as safe.

## G0 Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-owner-truth-legacy-tail-shadow-g0-gate.sh
```

The gate proves deterministic replay, exact-duplicate handling, immutable
rebind conflicts, gap reporting, quarantine/exclusion rejection, catalog and
callback-fixture requirements, and absence of persistence/network/provider
imports. It is included in `scripts/verify_backend.sh`.

## Deliberately Deferred

This does not close C04 or authorize a worker. Remaining work includes:

1. G2 append-only persistence and a real Postgres tail/checkpoint smoke.
2. Real legacy operation/object/provider inventory adapters under approved
   value-minimization and credential controls.
3. A deployment shadow window with side-effect count proven zero.
4. Separate approvals before any worker, object operation, provider call or
   Authority cutover can be enabled.

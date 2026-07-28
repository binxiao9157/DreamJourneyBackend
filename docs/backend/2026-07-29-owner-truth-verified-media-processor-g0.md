# Verified Media Processor Candidate-only G0

## Scope

This evidence closes the local G0 contract portion of `WI-S1-02-11` using the
existing default-off modules:

- `app/services/owner_truth_media_source_object_shadow.py`
- `app/services/owner_truth_verified_media_processor_shadow.py`

They model future admission for a verified private SourceObject and an
ExtractionResult-shaped, Candidate-only output. They do not read media bytes,
issue an upload intent, access object storage, enqueue a job, call OCR/parser/
ASR/vision, persist an extraction result, propose a Candidate, or write
confirmed Memory/Persona.

## Admission Boundary

A future processor can only be planned when the synthetic SourceObject:

1. uses the current SourceObject protocol, private non-mock storage metadata,
   matching magic MIME and complete checksum/head/scan receipts;
2. is in the `verified` state and matches the current Vault, Owner and
   `candidateExtraction` purpose;
3. carries no Candidate, confirmed Memory or Persona authority field; and
4. matches a versioned, enabled, media-kind-specific `synthetic` processor
   descriptor.

Legacy/local/mock objects, temporary locators, revoked/deleted/unverified
parents, owner/Vault/purpose mismatches, wrong MIME, incomplete receipts and
disabled/provider-mode descriptors all fail closed.

## Attempt and Result Semantics

The plan uses a deterministic request fingerprint over source fingerprint,
processor version and policy. A matching prior attempt has exactly one next
action:

- succeeded: deduplicate;
- retryable failure: future source-extraction retry;
- terminal failure: record terminal state;
- unknown: query/reconcile before any retry.

Stale or foreign attempts are rejected. A synthetic result can retain only
hashed segment references and confidence. It requires a separate Candidate
proposal command only when it is successful and non-empty. Empty, failed or
quarantined results cannot become Candidate proposals. All summaries assert:

- `extractionResultPersisted == false`;
- `candidateProposalPerformed == false`;
- `confirmedMemoryWritten == false`;
- `personaWritten == false`;
- `providerCallPerformed == false`.

## Verification

Run:

```bash
scripts/run-backend-verified-media-processor-shadow-gate.sh
scripts/verify_backend.sh
```

The focused gate runs the SourceObject, processor and Candidate-extraction
contract tests, compiles both modules, and rejects direct API/effect/persistence
imports and side-effect calls.

## Remaining Gates

This is not a real media processor. G1 still needs owner-scoped iOS transfer/
processing/extraction/candidate ViewState. G2 needs private object intent and
commit, durable ExtractionResult and receipt repositories, a worker, crash and
delete propagation evidence. G3 requires an approved processor per media kind,
including quality, region, cost, retention and delete/exit evidence. G4 needs
sensitive-media privacy and operations acceptance. The default public state
remains off until each applicable gate is independently satisfied.

# Owner Truth C09 Rights, Restore, and Replay G0

## Scope

This is local G0 evidence for `WI-MIG-01-10`. It composes existing contracts
instead of creating a second deletion, rights, recovery, or replay authority.

- Account lifecycle blocks generic upsert resurrection, restore-limit bypass,
  expired restore, and purge while a retention hold is active.
- Rights evidence keeps missing access-revocation or terminal cleanup receipts
  as `unknown`; it does not infer completion from a tombstone.
- Recovery read-only mode blocks writes before replay or cleanup can proceed.
- Replay requires deletion coverage and terminal deletion evidence; missing or
  unknown evidence is `incomplete` and cannot form a Go record.
- Existing post-restore dead-letter replay remains separately fenced by owner,
  vault, epoch, checkpoint, and a fresh recovery authorization receipt.

## Verification

Run:

```bash
scripts/run-backend-owner-truth-migration-rights-restore-replay-g0-gate.sh
scripts/verify_backend.sh
```

The composite gate runs the new cross-contract assertions plus the existing
account lifecycle, rights projection, recovery access, recovery record, and
async-effect recovery evidence tests.

## Explicit Non-Claims

This does not execute deletion, terminal purge, backup restore, replay,
Provider exit, or a deployed smoke. It does not prove every module/object/
Provider layer has a current receipt. It also does not substitute for privacy,
legal, operations, or device acceptance. G1-G4 remain open.

Any real C09 exercise must use approved non-production or isolated test assets,
preserve receipts and incidents, and use forward-fix/reconcile rather than
resurrecting access or rolling back terminal deletion facts.

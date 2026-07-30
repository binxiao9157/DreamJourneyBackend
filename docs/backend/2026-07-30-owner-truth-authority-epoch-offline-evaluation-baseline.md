# Owner Truth Authority-Epoch Offline Evaluation Baseline

## Scope

This is a synthetic-only G0 regression gate for the existing default-off
Owner Truth shadow lane. It verifies that an authority epoch change invalidates
old work before that work can mutate or re-expose private state. It does not
change routes, database schema, public UI, provider configuration, release
policy, or cutover state.

The versioned corpus is at
`tests/fixtures/owner_truth/authority_epoch_offline_evaluation_v1.json`.
Every scenario invokes existing in-memory Owner Truth or async-effect admission
components. The final report retains only synthetic case IDs, counters and
reason codes.

## Covered Negative Cases

1. A stale Source async callback is blocked when the live Vault epoch changes.
2. A stale Candidate decision is rejected and leaves no decision receipt.
3. An old Memory Projection/Context cache becomes rebuilding with no selected
   personal-memory entries after the Vault epoch changes.
4. A Context built at the old epoch cannot persist a new Answer/Citation
   receipt after the epoch changes.
5. A pending correction cannot resolve against an old MemoryVersion after the
   epoch changes; no terminal resolution or outdated-answer event is written.
6. A cross-owner async replay is blocked before consumer completion.
7. A cross-vault Context/Citation replay is denied without creating a receipt.

The evaluator additionally rejects any observation that reports legacy reads,
private-input leakage, or policy-violation metrics.

## Run

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-authority-epoch-offline-evaluation-gate.sh
```

The command is included in `scripts/verify_backend.sh`.

## Evidence Boundary

Passing this gate establishes only local synthetic G0 evidence for stale
authority-epoch invalidation. It does not establish PostgreSQL concurrency
behavior, production cutover, legacy writer retirement, provider quality,
real-user data safety, G3/G4 approval, or true-device readiness.

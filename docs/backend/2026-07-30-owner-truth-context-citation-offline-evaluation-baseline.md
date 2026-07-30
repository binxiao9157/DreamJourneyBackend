# Owner Truth Context/Citation Offline Evaluation Baseline

## Scope

This is a synthetic-only G0 regression gate for the existing default-off
Context V4 shadow builder and QA-only immutable Answer/Citation recorder. It
does not change `/context/build`, Echo, KBLite, database schema, Provider use,
or public release policy.

The versioned corpus is at
`tests/fixtures/owner_truth/context_citation_offline_evaluation_v1.json`.
Every scenario invokes the real in-memory Owner Truth Projection, Context
shadow build, and Answer/Citation receipt paths. The evaluator output retains
only synthetic case IDs, counters, and violation codes.

## Covered Negative Cases

1. Query match: only the current owner-scoped confirmed Projection entry is
   cited; an unmatched entry is filtered.
2. Query no match: zero citations and the explicit no-personal-memory fallback.
3. Projection unavailable: zero citations and no legacy/KBLite fallback.
4. Sensitive and AI-only entries: filtered before Context materialization.
5. Cross-owner request: denied before any Context or Answer/Citation record.
6. Cross-vault request: denied before another vault can supply Context or a
   receipt.
7. Source invalidated between build and receipt: receipt persistence is rejected
   and no immutable Answer/Citation record is created.

For every case the gate also checks that Context remains shadow-only,
`legacyContextRead=false`, citations stay in the expected vault, all persisted
citations are resolved, and value-free summaries do not contain synthetic query
or memory markers.

## Run

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-context-citation-offline-evaluation-gate.sh
```

The command is also included in `scripts/verify_backend.sh`.

## Evidence Boundary

Passing this gate establishes only local synthetic G0 correctness and privacy
regression evidence. It does not establish real retrieval quality, semantic
provider quality, PostgreSQL deployment, performance/SLO compliance, public
Echo promotion, or G3/G4 approval.

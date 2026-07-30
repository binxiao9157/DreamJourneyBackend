# Owner Truth Context Capacity Preflight

## Purpose

`scripts/backend-owner-truth-context-capacity-preflight.py` is a local,
synthetic preflight for the M0 Context Packet path. It exercises:

- a sustained request cadence;
- a concurrent burst;
- packet byte bounds;
- personal persona identity consistency; and
- cross-owner marker isolation.

Its JSON output contains aggregate timing, packet-size and error-code data only.
It must not contain source text, archive values, query text, IDs or credentials.

## Run locally

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
RUN_OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT=1 \
  scripts/run-backend-owner-truth-context-capacity-preflight-gate.sh
```

The default runtime profile is intentionally short: 10 QPS for two seconds and
a 100-request local executor burst. Override only in an isolated environment:

```bash
RUN_OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT=1 \
OWNER_TRUTH_CONTEXT_CAPACITY_QPS=10 \
OWNER_TRUTH_CONTEXT_CAPACITY_DURATION_SECONDS=1800 \
OWNER_TRUTH_CONTEXT_CAPACITY_BURST_CONCURRENCY=100 \
  scripts/run-backend-owner-truth-context-capacity-preflight-gate.sh
```

## Evidence boundary

Even the full parameter shape is only `localSyntheticPreflight`. It does not
prove deployed Postgres pool capacity, retrieval/projection lag, provider
latency, real cross-vault authorization, or operations approval. Those remain
separate M0-C DFX gates in an isolated deployed environment.

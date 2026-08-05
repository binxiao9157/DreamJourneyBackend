#!/usr/bin/env python3
"""Run the fixed M0 non-device DFX regression baseline.

This runner composes existing synthetic/disposable probes instead of creating a
second business harness.  Child output is parsed into counters and machine
codes only; it is never copied into the resulting evidence report.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.observability.m0_dfx_baseline import (
    M0DfxProbeResult,
    build_m0_dfx_baseline_report,
)


_CONTEXT_ENV = {
    "OWNER_TRUTH_CONTEXT_CAPACITY_QPS": "2",
    "OWNER_TRUTH_CONTEXT_CAPACITY_DURATION_SECONDS": "0.2",
    "OWNER_TRUTH_CONTEXT_CAPACITY_BURST_CONCURRENCY": "4",
    "OWNER_TRUTH_CONTEXT_CAPACITY_MAX_PACKET_BYTES": "65536",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _last_json_object(output: str) -> Mapping[str, Any] | None:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return parsed
    return None


def _run_child(name: str, script: str, *, environment: Mapping[str, str]) -> tuple[int, int, str]:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, str(ROOT_DIR / script)],
        cwd=ROOT_DIR,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return completed.returncode, elapsed_ms, completed.stdout


def _context_probe(environment: Mapping[str, str]) -> M0DfxProbeResult:
    return_code, elapsed_ms, stdout = _run_child(
        "contextPacket",
        "scripts/backend-owner-truth-context-capacity-preflight.py",
        environment=environment,
    )
    payload = _last_json_object(stdout)
    if return_code != 0 or not isinstance(payload, Mapping):
        return M0DfxProbeResult(
            probe_id="contextPacket",
            status="failed",
            elapsed_ms=elapsed_ms,
            sample_count=1,
            failure_count=1,
            error_codes=("contextProbeProcessFailed",),
        )
    execution = payload.get("execution") if isinstance(payload.get("execution"), Mapping) else {}
    sustained = execution.get("sustained") if isinstance(execution.get("sustained"), Mapping) else {}
    burst = execution.get("burst") if isinstance(execution.get("burst"), Mapping) else {}
    sustained_latency = sustained.get("latencyMs") if isinstance(sustained.get("latencyMs"), Mapping) else {}
    burst_latency = burst.get("latencyMs") if isinstance(burst.get("latencyMs"), Mapping) else {}
    sustained_packets = sustained.get("packetBytes") if isinstance(sustained.get("packetBytes"), Mapping) else {}
    burst_packets = burst.get("packetBytes") if isinstance(burst.get("packetBytes"), Mapping) else {}
    error_codes = set()
    for section in (sustained, burst):
        error_counts = section.get("errorCounts") if isinstance(section.get("errorCounts"), Mapping) else {}
        error_codes.update(str(code) for code in error_counts)
    sample_count = int(sustained.get("requestCount") or 0) + int(burst.get("requestCount") or 0)
    failure_count = int(sustained.get("failedRequestCount") or 0) + int(burst.get("failedRequestCount") or 0)
    passed = str(payload.get("status") or "") == "passed" and failure_count == 0 and sample_count > 0
    return M0DfxProbeResult(
        probe_id="contextPacket",
        status="passed" if passed else "failed",
        elapsed_ms=elapsed_ms,
        sample_count=max(1, sample_count),
        failure_count=failure_count if not passed else 0,
        error_codes=tuple(sorted(error_codes)) if not passed else (),
        metrics={
            "sustainedRequestCount": int(sustained.get("requestCount") or 0),
            "burstRequestCount": int(burst.get("requestCount") or 0),
            "latencyP95Ms": max(
                int(sustained_latency.get("p95") or 0),
                int(burst_latency.get("p95") or 0),
            ),
            "contextPacketP95Bytes": max(
                int(sustained_packets.get("p95") or 0),
                int(burst_packets.get("p95") or 0),
            ),
        },
    )


def _stage2_probe(environment: Mapping[str, str]) -> M0DfxProbeResult:
    return_code, elapsed_ms, stdout = _run_child(
        "stage2MediaCandidateProjection",
        "scripts/backend-owner-truth-media-processing-postgres-smoke.py",
        environment=environment,
    )
    payload = _last_json_object(stdout)
    required_flags = (
        "ownerBoundUpload",
        "commandReplayDeduplicated",
        "crossOwnerDenied",
        "candidateConfirmed",
        "memoryVersionCreated",
        "projectionLagBlocked",
        "projectionReady",
        "contextBuilt",
        "deletionAccessRevoked",
        "deletionReplayDeduplicated",
        "physicalDeletionCompleted",
        "deletedMediaExcludedFromContext",
        "mediaProcessingDeadLetterAdmitted",
        "mediaDeletionDeadLetterAdmitted",
        "responseRedaction",
    )
    verified_count = sum(bool(payload.get(flag)) for flag in required_flags) if payload else 0
    passed = return_code == 0 and payload is not None and verified_count == len(required_flags)
    return M0DfxProbeResult(
        probe_id="stage2MediaCandidateProjection",
        status="passed" if passed else "failed",
        elapsed_ms=elapsed_ms,
        sample_count=1,
        failure_count=0 if passed else 1,
        error_codes=() if passed else ("stage2PipelineContractIncomplete",),
        metrics={
            "verifiedCheckCount": verified_count,
            "requiredCheckCount": len(required_flags),
            "temporaryDatabaseCount": 1,
        },
    )


def _cross_vault_probe(environment: Mapping[str, str]) -> M0DfxProbeResult:
    return_code, elapsed_ms, stdout = _run_child(
        "crossVaultRevocation",
        "scripts/backend-owner-truth-review-ready-confirmation-handoff-postgres-smoke.py",
        environment=environment,
    )
    required_markers = (
        "formalPolicyOnly=true",
        "inboxOwnerVaultIsolated=true",
        "staleAndRedactedFiltered=true",
        "ownerBForbidden=true",
        "qaBypassDenied=true",
        "noDecisionReceiptMemoryVersionProjectionOrProviderEffect=true",
    )
    matched_count = sum(marker in stdout for marker in required_markers)
    passed = return_code == 0 and matched_count == len(required_markers)
    return M0DfxProbeResult(
        probe_id="crossVaultRevocation",
        status="passed" if passed else "failed",
        elapsed_ms=elapsed_ms,
        sample_count=1,
        failure_count=0 if passed else 1,
        error_codes=() if passed else ("crossVaultRevocationContractIncomplete",),
        metrics={
            "verifiedCheckCount": matched_count,
            "requiredCheckCount": len(required_markers),
            "temporaryDatabaseCount": 1,
        },
    )


def main() -> None:
    require(
        os.environ.get("RUN_M0_DFX_BASELINE") == "1",
        "RUN_M0_DFX_BASELINE=1 is required",
    )
    build = str(os.environ.get("M0_DFX_BUILD_ID") or "").strip()
    database_url = str(os.environ.get("DATABASE_URL") or "").strip()
    require(build, "M0_DFX_BUILD_ID is required")
    require(database_url, "DATABASE_URL is required")
    environment = str(os.environ.get("M0_DFX_ENVIRONMENT") or "serverContainer").strip()
    child_environment = dict(os.environ)
    child_environment["PYTHONPATH"] = str(ROOT_DIR)
    child_environment.update(_CONTEXT_ENV)
    child_environment["RUN_OWNER_TRUTH_MEDIA_LEASE_HEARTBEAT_SMOKE"] = "1"
    child_environment["RUN_OWNER_TRUTH_MEDIA_PHYSICAL_DELETION_SMOKE"] = "1"
    child_environment["DREAMJOURNEY_OWNER_TRUTH_REVIEW_READY_HANDOFF_SMOKE"] = "1"
    child_environment["OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL"] = database_url

    started = datetime.now(timezone.utc)
    probes = (
        _context_probe(child_environment),
        _stage2_probe(child_environment),
        _cross_vault_probe(child_environment),
    )
    completed = datetime.now(timezone.utc)
    report = build_m0_dfx_baseline_report(
        build=build,
        environment=environment,
        dataset={
            "ownerCount": 2,
            "archiveItemCountPerOwner": 12,
            "kbFactCountPerOwner": 8,
            "mediaDocumentCount": 1,
            "mediaFailureProbeCount": 1,
            "vaultCount": 2,
        },
        concurrency={
            "contextSustainedQps": int(_CONTEXT_ENV["OWNER_TRUTH_CONTEXT_CAPACITY_QPS"]),
            "contextBurstConcurrency": int(
                _CONTEXT_ENV["OWNER_TRUTH_CONTEXT_CAPACITY_BURST_CONCURRENCY"]
            ),
            "postgresProbeConcurrency": 1,
        },
        probes=probes,
        started_at=started,
        completed_at=completed,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

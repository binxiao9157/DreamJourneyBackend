#!/usr/bin/env python3
"""Generate synthetic iOS status fixtures from the production v3 serializer."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.owner_truth_interview_candidate_proposal import (
    OwnerTruthInterviewCandidateProposalStatus,
    OwnerTruthInterviewCandidateProposalStatusService,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
import app.main as main_module
from fastapi.testclient import TestClient


VAULT_ID = "00000000-0000-0000-0000-00000000f170"
BATCH_ID = "00000000-0000-0000-0000-00000000f171"


def envelope(status: OwnerTruthInterviewCandidateProposalStatus) -> dict[str, object]:
    route_path = (
        f"/v2/vaults/{VAULT_ID}/interview-review-batches/{BATCH_ID}/"
        "candidate-proposal/status"
    )
    context = OwnerTruthCommandContext(
        vault_id=VAULT_ID,
        owner_subject_id=VAULT_ID,
        actor_subject_id=VAULT_ID,
    )
    with patch.object(
        main_module,
        "_owner_truth_interview_candidate_proposal_status_context",
        return_value=context,
    ), patch.object(
        OwnerTruthInterviewCandidateProposalStatusService,
        "read_status",
        return_value=status,
    ), patch.object(
        main_module,
        "DATABASE_TRANSACTION_BYPASS_PATHS",
        main_module.DATABASE_TRANSACTION_BYPASS_PATHS | frozenset({route_path}),
    ):
        response = TestClient(main_module.app).get(
            route_path,
            headers={"x-dreamjourney-qa-owner-truth": "1"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"status route returned {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("status route did not return an object")
    return payload


def status(
    *,
    extraction: str,
    job_state: str,
    review: str,
    attempt: int,
    max_attempts: int = 3,
    failure_code: str | None = None,
    first_failure_code: str | None = None,
    retry_available_at: str | None = None,
) -> OwnerTruthInterviewCandidateProposalStatus:
    return OwnerTruthInterviewCandidateProposalStatus(
        review_batch_id=BATCH_ID,
        review_batch_state="acknowledged",
        candidate_proposal_status="admitted",
        source_status="admitted",
        candidate_extraction_status=extraction,
        effect_execution_status="disabled",
        candidate_review_status=review,
        candidate_extraction_job_state=job_state,
        candidate_extraction_attempt=attempt,
        candidate_extraction_max_attempts=max_attempts,
        candidate_extraction_retry_available_at=retry_available_at,
        candidate_extraction_first_failure_code=first_failure_code,
        candidate_extraction_failure_code=failure_code,
        candidate_extraction_terminal_reason_code=(
            "candidateExtractionRetriesExhausted" if job_state == "failed" else None
        ),
        candidate_extraction_dead_letter_state=(
            "admitted" if job_state == "failed" else None
        ),
    )


def source_fingerprint() -> str:
    source = ROOT_DIR / "app/services/owner_truth_interview_candidate_proposal.py"
    return sha256(source.read_bytes()).hexdigest()


def route_fingerprint() -> str:
    return sha256((ROOT_DIR / "app/main.py").read_bytes()).hexdigest()


def backend_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, text=True
    ).strip()


def build_fixture() -> dict[str, object]:
    contract_failure = "candidateExtraction.live.supportValidate.factOmitted"
    return {
        "schemaVersion": "dreamjourney-ios-owner-truth-status-fixtures-v1",
        "syntheticDataOnly": True,
        "generatedBy": "scripts/generate-owner-truth-candidate-status-ios-fixtures.py",
        "backendHead": backend_head(),
        "productionSerializerSHA256": source_fingerprint(),
        "productionRouteSHA256": route_fingerprint(),
        "generatedVia": "fastapiStatusRoute",
        "vaultId": VAULT_ID,
        "reviewBatchId": BATCH_ID,
        "cases": {
            "queued": envelope(status(
                extraction="requested",
                job_state="pending",
                review="notReady",
                attempt=0,
            )),
            "retryWaiting": envelope(status(
                extraction="requested",
                job_state="retryWait",
                review="notReady",
                attempt=1,
                first_failure_code=contract_failure,
                failure_code=contract_failure,
                retry_available_at="2099-01-01T00:00:05Z",
            )),
            "failed": envelope(status(
                extraction="failed",
                job_state="failed",
                review="extractionFailed",
                attempt=2,
                first_failure_code=contract_failure,
                failure_code=contract_failure,
            )),
            "reviewReady": envelope(status(
                extraction="succeeded",
                job_state="succeeded",
                review="reviewReady",
                attempt=2,
            )),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_fixture(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

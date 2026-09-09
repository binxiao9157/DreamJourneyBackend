#!/usr/bin/env python3
"""Run the 200-case synthetic corpus through the configured DeepSeek organizer.

This paid/provider operation is explicit opt-in. A checkpoint retains only
value-free counters and synthetic case IDs, so an interrupted run can resume
without persisting model output or accidentally reading a private Vault.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import Settings
from app.services.deepseek import DeepSeekTextMemoryOrganizationProxy
from app.services.owner_truth_memory_organization_quality import (
    OwnerTruthOrganizationQualityCaseResult,
    organization_quality_summary,
    provider_error_case_result,
    score_organization_quality_case,
)
from app.services.owner_truth_memory_search_quality_corpus import (
    load_owner_truth_memory_search_quality_corpus,
)


APPROVAL_ENV = "OWNER_TRUTH_REAL_TEXT_ORGANIZATION_QUALITY_APPROVED"
DEFAULT_CORPUS = ROOT_DIR / "tests/fixtures/owner_truth/memory_search_quality_zh_v1.json"


def _request_case_organization(*, proxy, case) -> dict[str, object]:
    """Use the production subject boundary for one synthetic quality case."""

    if case.category != "familyReport":
        return proxy.request_organization(text=case.fact_text)
    organization = proxy.request_family_organization(text=case.fact_text)
    memories = organization.get("memories")
    if not isinstance(memories, list):
        memories = []
    return {
        "memories": [
            memory
            for memory in memories
            if isinstance(memory, dict)
            and memory.get("subjectRole") == "memorySubject"
        ]
    }


def _read_checkpoint(
    path: Path,
    *,
    model_id: str,
    prompt_version: str,
    corpus_sha256: str,
) -> dict[str, OwnerTruthOrganizationQualityCaseResult]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("organization quality checkpoint is invalid")
    expected_identity = {
        "schemaVersion": "owner-truth-memory-organization-quality-checkpoint-v2",
        "modelId": model_id,
        "promptVersion": prompt_version,
        "corpusSha256": corpus_sha256,
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("organization quality checkpoint identity does not match this run")
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("organization quality checkpoint is invalid")
    results: dict[str, OwnerTruthOrganizationQualityCaseResult] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("organization quality checkpoint row is invalid")
        result = OwnerTruthOrganizationQualityCaseResult(
            case_id=str(row["caseId"]),
            split=str(row["split"]),
            category=str(row["category"]),
            candidate_count=int(row["candidateCount"]),
            supported_candidate_count=int(row["supportedCandidateCount"]),
            evidence_claim_count=int(row["evidenceClaimCount"]),
            supported_evidence_claim_count=int(row["supportedEvidenceClaimCount"]),
            anchor_recalled=bool(row["anchorRecalled"]),
            expected_candidate=bool(
                row.get(
                    "expectedCandidate",
                    str(row["category"]) != "questionAndHypothesis",
                )
            ),
            unexpected_candidate_count=int(
                row.get(
                    "unexpectedCandidateCount",
                    int(row["candidateCount"])
                    if str(row["category"]) == "questionAndHypothesis"
                    else 0,
                )
            ),
            provider_error=bool(row["providerError"]),
            provider_error_code=str(row.get("providerErrorCode") or ""),
        )
        results[result.case_id] = result
    return results


def _write_checkpoint(
    path: Path,
    results: dict[str, OwnerTruthOrganizationQualityCaseResult],
    *,
    model_id: str,
    prompt_version: str,
    corpus_sha256: str,
) -> None:
    payload = {
        "schemaVersion": "owner-truth-memory-organization-quality-checkpoint-v2",
        "modelId": model_id,
        "promptVersion": prompt_version,
        "corpusSha256": corpus_sha256,
        "cases": [results[key].value_free_descriptor() for key in sorted(results)],
        "responseContentRetained": False,
        "privateVaultRead": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    if os.environ.get(APPROVAL_ENV) != "1":
        print(
            f"BLOCKED: set {APPROVAL_ENV}=1 after approving the bounded "
            "synthetic DeepSeek quality run.",
            file=sys.stderr,
        )
        return 3
    settings = Settings.from_env()
    if not settings.deepseek_api_key:
        print("BLOCKED: DeepSeek configuration is incomplete.", file=sys.stderr)
        return 3
    corpus_sha256 = sha256(args.corpus.read_bytes()).hexdigest()
    corpus = load_owner_truth_memory_search_quality_corpus(args.corpus)
    if args.limit < 1 or args.limit > len(corpus):
        raise ValueError("organization quality limit is outside the corpus")
    selected = corpus[: args.limit]
    proxy = DeepSeekTextMemoryOrganizationProxy(settings)
    results = _read_checkpoint(
        args.checkpoint,
        model_id=proxy.model,
        prompt_version=proxy.prompt_version,
        corpus_sha256=corpus_sha256,
    )
    expected_ids = {case.case_id for case in selected}
    if not set(results).issubset(expected_ids):
        raise ValueError("organization quality checkpoint belongs to another run")

    for case in selected:
        if case.case_id in results and not results[case.case_id].provider_error:
            continue
        try:
            organization = _request_case_organization(proxy=proxy, case=case)
            result = score_organization_quality_case(
                case=case,
                organization=organization,
            )
        except Exception as error:
            result = provider_error_case_result(
                case,
                code=_provider_error_code(error),
            )
        results[case.case_id] = result
        _write_checkpoint(
            args.checkpoint,
            results,
            model_id=proxy.model,
            prompt_version=proxy.prompt_version,
            corpus_sha256=corpus_sha256,
        )

    ordered = [results[case.case_id] for case in selected if case.case_id in results]
    report = organization_quality_summary(
        ordered,
        expected_case_count=len(corpus),
        model_id=proxy.model,
        prompt_version=proxy.prompt_version,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if report["status"] == "passed":
        return 0
    return 4 if report["status"] == "incomplete" else 2


def _provider_error_code(error: Exception) -> str:
    """Return a value-free diagnostic code without retaining provider content."""

    if isinstance(error, httpx.HTTPStatusError):
        return f"http_{error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.RequestError):
        return "request_error"
    message = str(error)
    if "returned invalid JSON" in message:
        return "invalid_json"
    if "returned too many memories" in message:
        return "too_many_memories"
    if "invalid subject role" in message:
        return "invalid_subject_role"
    if "invalid kind" in message:
        return "invalid_memory_kind"
    if "invalid content" in message:
        return "invalid_content"
    if "violates typed schema" in message:
        match = re.search(
            r"violates typed schema:\s*([A-Za-z][A-Za-z0-9]*)",
            message,
        )
        return f"typed_schema_{match.group(1)}" if match else "typed_schema"
    if "empty choices" in message:
        return "empty_choices"
    if "empty content" in message:
        return "empty_content"
    return "value_error" if isinstance(error, ValueError) else "unexpected_error"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

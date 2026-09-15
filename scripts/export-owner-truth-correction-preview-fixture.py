#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)
from tests.test_owner_truth_candidate_review_api import (
    OwnerTruthCandidateReviewAPITests,
    _content_hash,
    client,
)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def export_fixture(output_path: Path) -> None:
    case = OwnerTruthCandidateReviewAPITests(
        methodName="test_v5_correction_preview_binds_source_candidate_and_resolved_content"
    )
    case.setUp()
    try:
        owner_id, headers = case._login("13800139991")
        vault_id = "vault-fixture-correction-binding"
        candidate = case._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        original_content = enrich_memory_payload_v5(
            kind=MemoryKind.KNOWLEDGE,
            payload={
                "statement": "合成测试阅读清单代号是旧值。",
                "knowledgeType": "preference",
                "domains": ["knowledgeSkills"],
                "factType": "knowledge",
                "predicate": "readingListCodeName",
                "object": {"label": "旧值", "category": "codeName"},
            },
            provenance={"mode": "selfReport"},
            memory_subject_id="person-fixture-owner",
            claim_subject_id="person-fixture-owner",
        )
        candidate = replace(
            candidate,
            memory_kind=MemoryKind.KNOWLEDGE,
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            content_hash=_content_hash(original_content),
            payload={
                **candidate.payload,
                "content": original_content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
            },
        )
        case._seed(candidate)

        original_response = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers=headers,
        )
        original_response.raise_for_status()
        original_candidate = original_response.json()["candidates"][0]

        corrected_submission = {"statement": "合成测试阅读清单代号是新值。"}
        preview_response = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/changeset-preview",
            headers=headers,
            json={
                "correctedValue": corrected_submission,
                "correctedValueSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
            },
        )
        preview_response.raise_for_status()
        preview = preview_response.json()

        fixture = {
            "schemaVersion": "owner-truth-correction-preview-producer-fixture-v1",
            "producer": "DreamJourneyBackend real candidate changeset builder",
            "syntheticDataOnly": True,
            "originalCandidate": original_candidate,
            "correctionPreview": preview,
            "assertions": {
                "sourceHashMatchesOriginal": (
                    preview["correctionBinding"]["sourceCandidateContentHash"]
                    == original_candidate["contentHash"]
                ),
                "resolvedHashMatchesProposal": (
                    preview["correctionBinding"]["resolvedContentHash"]
                    == preview["proposedChangeSet"]["candidateContentHash"]
                ),
                "resolvedHashDiffersFromSource": (
                    preview["correctionBinding"]["resolvedContentHash"]
                    != preview["correctionBinding"]["sourceCandidateContentHash"]
                ),
            },
        }
        fixture["payloadFingerprint"] = _fingerprint(fixture)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        case.tearDown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_fixture(args.output)


if __name__ == "__main__":
    main()

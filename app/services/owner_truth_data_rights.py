"""Owner Truth data-rights read and counting projections.

The V4 Owner Truth tables deliberately preserve a number of append-only
authority/evidence records.  These helpers expose the owner-readable subset
for a bounded application-data export and count the corresponding resources
for terminal-purge disclosure.  They never perform deletion: a future
rights reconciler must first revoke access and then apply the retention policy
for immutable ledgers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


OwnerTruthRows = Callable[[str, Sequence[Any]], Sequence[Mapping[str, Any]]]
OwnerTruthRow = Callable[[str, Sequence[Any]], Optional[Mapping[str, Any]]]


def empty_owner_truth_data_rights_records() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "vault": [],
        "source": [],
        "candidate": [],
        "decisionReceipt": [],
        "memoryVersion": [],
        "answerCitation": [],
        "answerFeedback": [],
        "correction": [],
        "familyContributionGrant": [],
        "familyContributionSubmission": [],
    }


def read_owner_truth_data_rights_records(
    *,
    subject_id: str,
    fetchall: OwnerTruthRows,
) -> Dict[str, List[Dict[str, Any]]]:
    """Read canonical Owner Truth data through the Vault-owner boundary."""

    owner_id = str(subject_id or "").strip()
    if not owner_id:
        return empty_owner_truth_data_rights_records()

    def rows(query: str) -> List[Dict[str, Any]]:
        return [
            deepcopy(dict(row["payload"]))
            for row in fetchall(query, (owner_id,))
            if isinstance(row.get("payload"), Mapping)
        ]

    return {
        "vault": rows(
            """
            SELECT jsonb_build_object(
                'vaultId', vault.vault_id,
                'ownerSubjectId', vault.owner_subject_id,
                'authorityEpoch', vault.authority_epoch,
                'status', vault.status,
                'createdAt', vault.created_at,
                'updatedAt', vault.updated_at
            ) AS payload
            FROM owner_truth.vaults AS vault
            WHERE vault.owner_subject_id = %s
            ORDER BY vault.created_at, vault.vault_id
            LIMIT 1000
            """
        ),
        "source": rows(
            """
            SELECT jsonb_build_object(
                'id', source.id,
                'vaultId', source.vault_id,
                'ownerSubjectId', source.owner_subject_id,
                'sourceKind', source.source_kind,
                'state', source.state,
                'sourceVersion', source.source_version,
                'contentHash', source.content_hash,
                'contentPayload', source.content_payload,
                'metadata', source.metadata,
                'policyVersion', source.policy_version,
                'authorityEpoch', source.authority_epoch,
                'rowVersion', source.row_version,
                'createdAt', source.created_at,
                'updatedAt', source.updated_at
            ) AS payload
            FROM owner_truth.sources AS source
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = source.vault_id
            WHERE vault.owner_subject_id = %s
              AND source.owner_subject_id = vault.owner_subject_id
            ORDER BY source.created_at, source.id
            LIMIT 1000
            """
        ),
        "candidate": rows(
            """
            SELECT jsonb_build_object(
                'candidateId', candidate.id,
                'vaultId', candidate.vault_id,
                'ownerSubjectId', candidate.owner_subject_id,
                'sourceId', candidate.source_id,
                'extractionResultId', candidate.extraction_result_id,
                'memoryKind', candidate.candidate_kind,
                'perspectiveType', candidate.perspective_type,
                'epistemicStatus', candidate.epistemic_status,
                'sensitivity', candidate.sensitivity,
                'decision', candidate.decision_status,
                'quarantineCode', candidate.quarantine_code,
                'policyVersion', candidate.policy_version,
                'authorityEpoch', candidate.authority_epoch,
                'rowVersion', candidate.row_version,
                'contentHash', candidate.content_hash,
                'contentSchemaVersion', candidate.payload_schema_version,
                'payload', candidate.payload,
                'createdAt', candidate.created_at,
                'updatedAt', candidate.updated_at
            ) AS payload
            FROM owner_truth.memory_candidates AS candidate
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = candidate.vault_id
            WHERE vault.owner_subject_id = %s
              AND candidate.owner_subject_id = vault.owner_subject_id
            ORDER BY candidate.created_at, candidate.id
            LIMIT 1000
            """
        ),
        "decisionReceipt": rows(
            """
            SELECT jsonb_build_object(
                'id', receipt.id,
                'vaultId', receipt.vault_id,
                'ownerSubjectId', candidate.owner_subject_id,
                'candidateId', receipt.candidate_id,
                'decision', receipt.decision,
                'actorSubjectId', receipt.actor_subject_id,
                'authorityEpoch', receipt.authority_epoch,
                'policyVersion', receipt.policy_version,
                'rationaleHash', receipt.rationale_hash,
                'createdAt', receipt.created_at
            ) AS payload
            FROM owner_truth.decision_receipts AS receipt
            INNER JOIN owner_truth.memory_candidates AS candidate
                ON candidate.vault_id = receipt.vault_id
               AND candidate.id = receipt.candidate_id
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = receipt.vault_id
            WHERE vault.owner_subject_id = %s
              AND candidate.owner_subject_id = vault.owner_subject_id
            ORDER BY receipt.created_at, receipt.id
            LIMIT 1000
            """
        ),
        "memoryVersion": rows(
            """
            SELECT jsonb_build_object(
                'memoryId', memory.id,
                'memoryVersionId', version.id,
                'vaultId', version.vault_id,
                'ownerSubjectId', memory.owner_subject_id,
                'sourceId', memory.source_id,
                'sourceVersion', memory.source_version,
                'memoryKind', memory.memory_kind,
                'perspectiveType', memory.perspective_type,
                'epistemicStatus', memory.epistemic_status,
                'sensitivity', memory.sensitivity,
                'memoryStatus', memory.status,
                'authorityEpoch', memory.authority_epoch,
                'memoryRowVersion', memory.row_version,
                'versionNumber', version.version_number,
                'isCurrent', version.is_current,
                'contentHash', version.content_hash,
                'contentSchemaVersion', version.schema_version,
                'payload', version.payload,
                'createdAt', version.created_at
            ) AS payload
            FROM owner_truth.memory_versions AS version
            INNER JOIN owner_truth.memories AS memory
                ON memory.vault_id = version.vault_id
               AND memory.id = version.memory_id
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = version.vault_id
            WHERE vault.owner_subject_id = %s
              AND memory.owner_subject_id = vault.owner_subject_id
            ORDER BY version.created_at, version.id
            LIMIT 1000
            """
        ),
        "answerCitation": rows(
            """
            SELECT jsonb_build_object(
                'answerId', answer.id,
                'vaultId', answer.vault_id,
                'ownerSubjectId', answer.owner_subject_id,
                'commandIdHash', answer.command_id_hash,
                'contextHash', answer.context_hash,
                'contextVersion', answer.context_version,
                'contextTraceIdHash', answer.context_trace_id_hash,
                'queryHash', answer.query_hash,
                'queryLength', answer.query_length,
                'answerHash', answer.answer_hash,
                'answerLength', answer.answer_length,
                'authorityEpoch', answer.authority_epoch,
                'projectionCheckpoint', answer.projection_checkpoint,
                'fallbacks', answer.fallbacks,
                'createdAt', answer.created_at,
                'citations', COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'citationId', citation.id,
                            'citationPosition', citation.citation_position,
                            'memoryId', citation.memory_id,
                            'memoryVersionId', citation.memory_version_id,
                            'memoryVersion', citation.memory_version,
                            'sourceId', citation.source_id,
                            'sourceVersion', citation.source_version,
                            'contentHash', citation.content_hash,
                            'createdAt', citation.created_at
                        )
                        ORDER BY citation.citation_position
                    ) FILTER (WHERE citation.id IS NOT NULL),
                    '[]'::jsonb
                )
            ) AS payload
            FROM owner_truth.answers AS answer
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = answer.vault_id
            LEFT JOIN owner_truth.answer_citations AS citation
                ON citation.vault_id = answer.vault_id
               AND citation.answer_id = answer.id
            WHERE vault.owner_subject_id = %s
              AND answer.owner_subject_id = vault.owner_subject_id
            GROUP BY answer.id, answer.vault_id, answer.owner_subject_id,
                answer.command_id_hash, answer.context_hash, answer.context_version,
                answer.context_trace_id_hash,
                answer.query_hash, answer.query_length, answer.answer_hash,
                answer.answer_length, answer.authority_epoch,
                answer.projection_checkpoint, answer.fallbacks, answer.created_at
            ORDER BY answer.created_at, answer.id
            LIMIT 1000
            """
        ),
        "answerFeedback": rows(
            """
            SELECT jsonb_build_object(
                'feedbackId', feedback.id,
                'vaultId', feedback.vault_id,
                'ownerSubjectId', feedback.owner_subject_id,
                'commandIdHash', feedback.command_id_hash,
                'commandPayloadHash', feedback.command_payload_hash,
                'answerId', feedback.answer_id,
                'helpful', feedback.helpful,
                'citationCount', feedback.citation_count,
                'eligibleCitationCount', feedback.eligible_citation_count,
                'metricEligible', feedback.metric_eligible,
                'eligibilityReason', feedback.eligibility_reason,
                'authorityEpoch', feedback.authority_epoch,
                'createdAt', feedback.created_at
            ) AS payload
            FROM owner_truth.answer_feedback AS feedback
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = feedback.vault_id
            WHERE vault.owner_subject_id = %s
              AND feedback.owner_subject_id = vault.owner_subject_id
            ORDER BY feedback.created_at, feedback.id
            LIMIT 1000
            """
        ),
        "correction": rows(
            """
            SELECT jsonb_build_object(
                'correctionRequestId', request.id,
                'vaultId', request.vault_id,
                'ownerSubjectId', request.owner_subject_id,
                'candidateId', request.candidate_id,
                'answerId', request.answer_id,
                'citationId', request.citation_id,
                'memoryId', request.memory_id,
                'expectedMemoryVersionId', request.expected_memory_version_id,
                'correctionSourceId', request.correction_source_id,
                'correctionTextHash', request.correction_text_hash,
                'correctionTextLength', request.correction_text_length,
                'reasonCodeHash', request.reason_code_hash,
                'status', request.status,
                'createdAt', request.created_at,
                'resolution', CASE
                    WHEN resolution.id IS NULL THEN NULL
                    ELSE jsonb_build_object(
                        'id', resolution.id,
                        'candidateId', resolution.candidate_id,
                        'decisionReceiptId', resolution.decision_receipt_id,
                        'decision', resolution.decision,
                        'expectedMemoryVersionId', resolution.expected_memory_version_id,
                        'replacementMemoryVersionId', resolution.replacement_memory_version_id,
                        'createdAt', resolution.created_at
                    )
                END
            ) AS payload
            FROM owner_truth.correction_requests AS request
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = request.vault_id
            LEFT JOIN owner_truth.correction_resolutions AS resolution
                ON resolution.vault_id = request.vault_id
               AND resolution.correction_request_id = request.id
            WHERE vault.owner_subject_id = %s
              AND request.owner_subject_id = vault.owner_subject_id
            ORDER BY request.created_at, request.id
            LIMIT 1000
            """
        ),
        "familyContributionGrant": rows(
            """
            SELECT jsonb_build_object(
                'grantId', family_grant.id,
                'vaultId', family_grant.vault_id,
                'relationshipId', family_grant.relationship_id,
                'relationshipEpoch', family_grant.relationship_epoch,
                'scope', family_grant.scope,
                'status', family_grant.status,
                'rowVersion', family_grant.row_version,
                'createdAt', family_grant.created_at,
                'updatedAt', family_grant.updated_at,
                'revokedAt', family_grant.revoked_at,
                'revocationReason', family_grant.revocation_reason
            ) AS payload
            FROM owner_truth.family_contribution_grants AS family_grant
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = family_grant.vault_id
            WHERE vault.owner_subject_id = %s
              AND family_grant.owner_subject_id = vault.owner_subject_id
            ORDER BY family_grant.created_at, family_grant.id
            LIMIT 1000
            """
        ),
        "familyContributionSubmission": rows(
            """
            SELECT jsonb_build_object(
                'submissionId', submission.id,
                'vaultId', submission.vault_id,
                'grantId', submission.grant_id,
                'relationshipId', submission.relationship_id,
                'relationshipEpoch', submission.relationship_epoch,
                'grantVersion', submission.grant_version,
                'materialKind', submission.material_kind,
                'text', submission.text_content,
                'sourceObjectId', submission.source_object_id,
                'sourceId', submission.source_id,
                'status', submission.status,
                'rowVersion', submission.row_version,
                'decidedAt', submission.decided_at,
                'decisionReason', submission.decision_reason,
                'createdAt', submission.created_at,
                'updatedAt', submission.updated_at
            ) AS payload
            FROM owner_truth.family_contribution_submissions AS submission
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = submission.vault_id
            WHERE vault.owner_subject_id = %s
              AND submission.owner_subject_id = vault.owner_subject_id
            ORDER BY submission.created_at, submission.id
            LIMIT 1000
            """
        ),
    }


def count_owner_truth_data_rights_records(
    *,
    subject_id: str,
    fetchone: OwnerTruthRow,
) -> Dict[str, int]:
    """Count records that remain subject to the Owner Truth rights lane."""

    owner_id = str(subject_id or "").strip()
    keys = (
        "ownerTruthVault",
        "ownerTruthSource",
        "ownerTruthCandidate",
        "ownerTruthDecisionReceipt",
        "ownerTruthMemoryVersion",
        "ownerTruthAnswerCitation",
        "ownerTruthAnswerFeedback",
        "ownerTruthCorrection",
        "ownerTruthFamilyContributionGrant",
        "ownerTruthFamilyContributionSubmission",
    )
    if not owner_id:
        return {key: 0 for key in keys}

    def count(query: str) -> int:
        row = fetchone(query, (owner_id,))
        return int((row or {}).get("count") or 0)

    return {
        "ownerTruthVault": count(
            "SELECT COUNT(*) AS count FROM owner_truth.vaults WHERE owner_subject_id = %s"
        ),
        "ownerTruthSource": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.sources AS source
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = source.vault_id
            WHERE vault.owner_subject_id = %s
              AND source.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthCandidate": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.memory_candidates AS candidate
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = candidate.vault_id
            WHERE vault.owner_subject_id = %s
              AND candidate.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthDecisionReceipt": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.decision_receipts AS receipt
            INNER JOIN owner_truth.memory_candidates AS candidate
                ON candidate.vault_id = receipt.vault_id AND candidate.id = receipt.candidate_id
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = receipt.vault_id
            WHERE vault.owner_subject_id = %s
              AND candidate.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthMemoryVersion": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.memory_versions AS version
            INNER JOIN owner_truth.memories AS memory
                ON memory.vault_id = version.vault_id AND memory.id = version.memory_id
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = version.vault_id
            WHERE vault.owner_subject_id = %s
              AND memory.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthAnswerCitation": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.answers AS answer
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = answer.vault_id
            WHERE vault.owner_subject_id = %s
              AND answer.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthAnswerFeedback": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.answer_feedback AS feedback
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = feedback.vault_id
            WHERE vault.owner_subject_id = %s
              AND feedback.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthCorrection": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.correction_requests AS request
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = request.vault_id
            WHERE vault.owner_subject_id = %s
              AND request.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthFamilyContributionGrant": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.family_contribution_grants AS family_grant
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = family_grant.vault_id
            WHERE vault.owner_subject_id = %s
              AND family_grant.owner_subject_id = vault.owner_subject_id
            """
        ),
        "ownerTruthFamilyContributionSubmission": count(
            """
            SELECT COUNT(*) AS count
            FROM owner_truth.family_contribution_submissions AS submission
            INNER JOIN owner_truth.vaults AS vault ON vault.vault_id = submission.vault_id
            WHERE vault.owner_subject_id = %s
              AND submission.owner_subject_id = vault.owner_subject_id
            """
        ),
    }


__all__ = [
    "count_owner_truth_data_rights_records",
    "empty_owner_truth_data_rights_records",
    "read_owner_truth_data_rights_records",
]

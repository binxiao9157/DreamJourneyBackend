"""Application boundary for pending Owner Truth Candidate extraction results.

The service deliberately accepts only a deterministic, provider-neutral command
in this slice.  It uses the live Source target admission guard, persists an
immutable result plus pending candidates, and records the typed Consumer Inbox
completion in one Unit of Work.  No route, worker or Provider is enabled here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.async_effects.consumer_repository import (
    AsyncEffectConsumerReceipt,
    OwnerTruthSourceBlockedConsumerCommand,
    OwnerTruthSourceCandidateExtractionConsumerCommand,
)
from app.async_effects.target_admission import AsyncEffectTargetAdmission
from app.domain.owner_truth.candidate_extraction import (
    ExtractionResultStatus,
    OwnerTruthCandidateExtractionConflict,
    OwnerTruthCandidateExtractionWriteRecord,
    SyntheticCandidateExtractionCommand,
)


class OwnerTruthCandidateExtractionIncomplete(RuntimeError):
    """A persisted extraction does not retain exactly its pending candidates."""


@dataclass(frozen=True)
class OwnerTruthCandidateExtractionPersistenceResult:
    outcome: str
    extraction_id: str
    status: ExtractionResultStatus
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class OwnerTruthCandidateExtractionResult:
    outcome: str
    status: ExtractionResultStatus | None
    reason_code: str
    extraction_id: str | None
    candidate_ids: tuple[str, ...]
    admission: AsyncEffectTargetAdmission
    consumer: AsyncEffectConsumerReceipt


class OwnerTruthCandidateExtractionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_source_target_admission_repository(self) -> Any:
        ...

    def owner_truth_candidate_extraction_repository(self) -> Any:
        ...

    def async_effect_consumer_repository(self) -> Any:
        ...


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class InMemoryOwnerTruthCandidateExtractionRepository:
    """G0 semantic double for immutable result/candidate persistence."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._extractions: dict[str, dict[str, Any]] = {}
        self._candidates: dict[str, dict[str, Any]] = {}

    def persist(
        self,
        record: OwnerTruthCandidateExtractionWriteRecord,
    ) -> OwnerTruthCandidateExtractionPersistenceResult:
        if not isinstance(record, OwnerTruthCandidateExtractionWriteRecord):
            raise TypeError("owner truth candidate extraction record is required")
        with self._lock:
            existing = self._extractions.get(record.extraction_id)
            if existing is not None:
                if existing["fingerprint"] != record.immutable_fingerprint():
                    raise OwnerTruthCandidateExtractionConflict(
                        "stable extraction cannot be reused with a different result"
                    )
                self._assert_candidate_set(record)
                return OwnerTruthCandidateExtractionPersistenceResult(
                    outcome="deduplicated",
                    extraction_id=record.extraction_id,
                    status=record.status,
                    candidate_ids=tuple(candidate.candidate_id for candidate in record.candidate_records),
                )

            self._extractions[record.extraction_id] = {
                "candidateIds": [candidate.candidate_id for candidate in record.candidate_records],
                "failureCode": record.failure_code,
                "fingerprint": record.immutable_fingerprint(),
                "payload": deepcopy(dict(record.payload)),
                "resultHash": record.result_hash,
                "sourceId": record.source_ref.source_id,
                "sourceVersion": record.source_ref.source_version,
                "status": record.status.value,
            }
            for candidate in record.candidate_records:
                existing_candidate = self._candidates.get(candidate.candidate_id)
                if existing_candidate is not None:
                    raise OwnerTruthCandidateExtractionConflict(
                        "candidate identity already belongs to another extraction"
                    )
                self._candidates[candidate.candidate_id] = {
                    "contentHash": candidate.content_hash,
                    "decisionStatus": "pending",
                    "extractionId": record.extraction_id,
                    "payload": deepcopy(dict(candidate.payload)),
                    "proposalHash": candidate.proposal_hash,
                    "sourceId": candidate.source_ref.source_id,
                    "sourceVersion": candidate.source_ref.source_version,
                }
            return OwnerTruthCandidateExtractionPersistenceResult(
                outcome="created",
                extraction_id=record.extraction_id,
                status=record.status,
                candidate_ids=tuple(candidate.candidate_id for candidate in record.candidate_records),
            )

    def _assert_candidate_set(self, record: OwnerTruthCandidateExtractionWriteRecord) -> None:
        expected_ids = {candidate.candidate_id for candidate in record.candidate_records}
        actual_ids = {
            candidate_id
            for candidate_id, candidate in self._candidates.items()
            if candidate["extractionId"] == record.extraction_id
        }
        if actual_ids != expected_ids:
            raise OwnerTruthCandidateExtractionIncomplete(
                "persisted extraction does not retain its exact candidate set"
            )
        for candidate in record.candidate_records:
            stored = self._candidates[candidate.candidate_id]
            if (
                stored["proposalHash"] != candidate.proposal_hash
                or stored["contentHash"] != candidate.content_hash
                or stored["payload"] != dict(candidate.payload)
            ):
                raise OwnerTruthCandidateExtractionConflict(
                    "candidate identity cannot be reused with different immutable content"
                )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                "candidates": deepcopy(self._candidates),
                "extractions": deepcopy(self._extractions),
            }


class PostgresOwnerTruthCandidateExtractionRepository:
    """Persist a typed ExtractionResult and pending Candidates in the active UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def persist(
        self,
        record: OwnerTruthCandidateExtractionWriteRecord,
    ) -> OwnerTruthCandidateExtractionPersistenceResult:
        if not isinstance(record, OwnerTruthCandidateExtractionWriteRecord):
            raise TypeError("owner truth candidate extraction record is required")
        with self._cursor() as cursor:
            self._assert_live_source(cursor, record)
            cursor.execute(
                """
                INSERT INTO owner_truth.extraction_results (
                    id, vault_id, source_id, source_version, extractor_id,
                    schema_version, status, result_hash, payload, failure_code, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                (
                    record.extraction_id,
                    record.source_ref.vault_id,
                    record.source_ref.source_id,
                    record.source_ref.source_version,
                    record.extractor_id,
                    "owner-truth-extraction-result-v1",
                    record.status.value,
                    record.result_hash,
                    self._json(record.payload),
                    record.failure_code,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                self._assert_existing_extraction(cursor, record)
                return OwnerTruthCandidateExtractionPersistenceResult(
                    outcome="deduplicated",
                    extraction_id=record.extraction_id,
                    status=record.status,
                    candidate_ids=tuple(candidate.candidate_id for candidate in record.candidate_records),
                )

            for candidate in record.candidate_records:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_candidates (
                        id, vault_id, owner_subject_id, source_id, extraction_result_id,
                        candidate_kind, perspective_type, epistemic_status, sensitivity,
                        decision_status, quarantine_code, policy_version, authority_epoch,
                        content_hash, payload_schema_version, payload
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'pending', NULL, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        candidate.candidate_id,
                        record.source_ref.vault_id,
                        record.intent.target.owner_subject_id,
                        candidate.source_ref.source_id,
                        record.extraction_id,
                        candidate.candidate_kind.value,
                        candidate.perspective_type.value,
                        candidate.epistemic_status.value,
                        candidate.sensitivity.value,
                        record.policy_version,
                        record.intent.target.authority_epoch,
                        candidate.content_hash,
                        candidate.payload_schema_version,
                        self._json(candidate.payload),
                    ),
                )

        return OwnerTruthCandidateExtractionPersistenceResult(
            outcome="created",
            extraction_id=record.extraction_id,
            status=record.status,
            candidate_ids=tuple(candidate.candidate_id for candidate in record.candidate_records),
        )

    def _assert_live_source(self, cursor: Any, record: OwnerTruthCandidateExtractionWriteRecord) -> None:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            FOR SHARE
            """,
            (record.source_ref.vault_id,),
        )
        vault = cursor.fetchone()
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, source_version, state, content_hash,
                source_kind, content_payload
            FROM owner_truth.sources
            WHERE vault_id = %s AND id = %s
            FOR SHARE
            """,
            (record.source_ref.vault_id, record.source_ref.source_id),
        )
        source = cursor.fetchone()
        target = record.intent.target
        if vault is None or source is None:
            raise OwnerTruthCandidateExtractionConflict("Source target disappeared before result persistence")
        if (
            str(vault["owner_subject_id"]) != target.owner_subject_id
            or str(vault["status"]) != "active"
            or int(vault["authority_epoch"]) != target.authority_epoch
            or str(source["owner_subject_id"]) != target.owner_subject_id
            or str(source["state"]) != "active"
            or int(source["authority_epoch"]) != target.authority_epoch
            or int(source["source_version"]) != record.source_ref.source_version
            or str(source["content_hash"]) != record.source_content_hash
        ):
            raise OwnerTruthCandidateExtractionConflict(
                "Source target changed before extraction result persistence"
            )
        if str(source["source_kind"]) not in {"text", "conversation"}:
            raise OwnerTruthCandidateExtractionConflict(
                "synthetic Candidate extraction requires a text-bearing Source"
            )
        source_text = str((source["content_payload"] or {}).get("text") or "")
        for candidate in record.candidate_records:
            evidence_refs = candidate.payload.get("evidenceRefs") or []
            span = evidence_refs[0].get("span") if evidence_refs else None
            if not isinstance(span, Mapping) or int(span.get("end") or 0) > len(source_text):
                raise OwnerTruthCandidateExtractionConflict(
                    "candidate evidence span is not resolvable against the active Source"
                )

    def _assert_existing_extraction(
        self,
        cursor: Any,
        record: OwnerTruthCandidateExtractionWriteRecord,
    ) -> None:
        cursor.execute(
            """
            SELECT id, vault_id, source_id, source_version, extractor_id,
                schema_version, status, result_hash, payload, failure_code
            FROM owner_truth.extraction_results
            WHERE id = %s
            FOR UPDATE
            """,
            (record.extraction_id,),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise OwnerTruthCandidateExtractionIncomplete(
                "extraction identity conflict did not produce a persisted result"
            )
        expected = {
            "vault_id": record.source_ref.vault_id,
            "source_id": record.source_ref.source_id,
            "source_version": record.source_ref.source_version,
            "extractor_id": record.extractor_id,
            "schema_version": "owner-truth-extraction-result-v1",
            "status": record.status.value,
            "result_hash": record.result_hash,
            "failure_code": record.failure_code,
        }
        if any(str(existing[key]) != str(value) for key, value in expected.items()):
            raise OwnerTruthCandidateExtractionConflict(
                "stable extraction cannot be reused with different immutable metadata"
            )
        if _canonical_json(existing["payload"] or {}) != _canonical_json(dict(record.payload)):
            raise OwnerTruthCandidateExtractionConflict(
                "stable extraction cannot be reused with a different result payload"
            )
        self._assert_existing_candidates(cursor, record)

    def _assert_existing_candidates(
        self,
        cursor: Any,
        record: OwnerTruthCandidateExtractionWriteRecord,
    ) -> None:
        cursor.execute(
            """
            SELECT id, source_id, extraction_result_id, candidate_kind,
                perspective_type, epistemic_status, sensitivity, decision_status,
                policy_version, authority_epoch, content_hash, payload_schema_version, payload
            FROM owner_truth.memory_candidates
            WHERE vault_id = %s AND extraction_result_id = %s
            ORDER BY id ASC
            """,
            (record.source_ref.vault_id, record.extraction_id),
        )
        rows = cursor.fetchall()
        expected_by_id = {candidate.candidate_id: candidate for candidate in record.candidate_records}
        actual_ids = {str(row["id"]) for row in rows}
        if actual_ids != set(expected_by_id):
            raise OwnerTruthCandidateExtractionIncomplete(
                "persisted extraction does not retain its exact candidate set"
            )
        for row in rows:
            candidate = expected_by_id[str(row["id"])]
            expected = {
                "source_id": candidate.source_ref.source_id,
                "extraction_result_id": record.extraction_id,
                "candidate_kind": candidate.candidate_kind.value,
                "perspective_type": candidate.perspective_type.value,
                "epistemic_status": candidate.epistemic_status.value,
                "sensitivity": candidate.sensitivity.value,
                "decision_status": "pending",
                "policy_version": record.policy_version,
                "authority_epoch": record.intent.target.authority_epoch,
                "content_hash": candidate.content_hash,
                "payload_schema_version": candidate.payload_schema_version,
            }
            if any(str(row[key]) != str(value) for key, value in expected.items()):
                raise OwnerTruthCandidateExtractionConflict(
                    "candidate identity cannot be reused with different immutable metadata"
                )
            if _canonical_json(row["payload"] or {}) != _canonical_json(dict(candidate.payload)):
                raise OwnerTruthCandidateExtractionConflict(
                    "candidate identity cannot be reused with different immutable content"
                )

    @staticmethod
    def _json(value: Mapping[str, Any]) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return _canonical_json(value)
        return Jsonb(dict(value))

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthCandidateExtractionService:
    """Record a synthetic extraction only after a live Source authority recheck."""

    def __init__(self, store: OwnerTruthCandidateExtractionStore):
        self._store = store

    def record(self, command: SyntheticCandidateExtractionCommand) -> OwnerTruthCandidateExtractionResult:
        if not isinstance(command, SyntheticCandidateExtractionCommand):
            raise TypeError("synthetic candidate extraction command is required")
        record = command.write_record()
        with self._store.request_unit_of_work(
            correlation_id=f"owner-truth-candidate-extraction-{record.extraction_id}",
            command_id=record.extraction_id,
        ):
            admission = self._store.owner_truth_source_target_admission_repository().admit_owner_truth_source(
                record.intent
            )
            consumer_repository = self._store.async_effect_consumer_repository()
            if not admission.allowed:
                consumer = consumer_repository.consume(
                    OwnerTruthSourceBlockedConsumerCommand(
                        intent=record.intent,
                        consumer_name="ownerTruth.source.blocked",
                        business_target_key=record.intent.business_target_key,
                        outcome="blocked",
                        reason_code=admission.reason_code,
                        result_ref_hash=record.result_hash,
                        admission=admission,
                    )
                )
                return OwnerTruthCandidateExtractionResult(
                    outcome="blocked",
                    status=None,
                    reason_code=admission.reason_code,
                    extraction_id=None,
                    candidate_ids=(),
                    admission=admission,
                    consumer=consumer,
                )

            persisted = self._store.owner_truth_candidate_extraction_repository().persist(record)
            consumer = consumer_repository.consume(
                OwnerTruthSourceCandidateExtractionConsumerCommand(
                    intent=record.intent,
                    consumer_name="ownerTruth.source.extraction",
                    business_target_key=record.business_target_key,
                    outcome=record.completion_outcome,
                    reason_code=record.completion_reason_code,
                    result_ref_hash=record.result_hash,
                    admission=admission,
                    extraction_id=record.extraction_id,
                    extraction_status=record.status.value,
                )
            )
        return OwnerTruthCandidateExtractionResult(
            outcome=persisted.outcome,
            status=persisted.status,
            reason_code=record.completion_reason_code,
            extraction_id=persisted.extraction_id,
            candidate_ids=persisted.candidate_ids,
            admission=admission,
            consumer=consumer,
        )


__all__ = [
    "InMemoryOwnerTruthCandidateExtractionRepository",
    "OwnerTruthCandidateExtractionIncomplete",
    "OwnerTruthCandidateExtractionPersistenceResult",
    "OwnerTruthCandidateExtractionResult",
    "OwnerTruthCandidateExtractionService",
    "PostgresOwnerTruthCandidateExtractionRepository",
]

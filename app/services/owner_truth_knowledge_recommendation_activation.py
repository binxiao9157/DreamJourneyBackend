"""Server-verified acceptance receipts for default-off M0-B recommendations.

The recommendation planner is intentionally value-free.  This service adds
the first durable user-action boundary without turning a planned suggestion
into an Echo prompt, a message, a Candidate, or a MemoryVersion.  A caller can
only name an opaque candidate returned by the current server plan.  The server
re-plans under the active Owner authority, checks the live session version,
then records one append-only acceptance receipt.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank, require_uuid
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
)
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.knowledge_dimension_read import OwnerTruthKnowledgeDimensionReadState
from app.domain.owner_truth.knowledge_recommendations import (
    KnowledgeDimension,
    RecommendationDecision,
    RecommendationSlot,
    knowledge_dimension_facets,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)
from app.services.owner_truth_knowledge_recommendation_read import (
    OwnerTruthKnowledgeRecommendationReadService,
)


OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION = (
    "owner-truth-knowledge-recommendation-activation-v1"
)
OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_UI_SCHEMA_VERSION = (
    "knowledge-recommendation-activation-v1"
)

_ACTIVATION_NAMESPACE = UUID("94fdb552-905e-4ca0-85a0-4b6e1e08e039")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthKnowledgeRecommendationActivationError(OwnerTruthContractError):
    """A recommendation acceptance cannot be interpreted safely."""


class OwnerTruthKnowledgeRecommendationActivationAccessDenied(
    OwnerTruthKnowledgeRecommendationActivationError
):
    """Only the active Vault Owner may accept an M0-B recommendation."""


class OwnerTruthKnowledgeRecommendationActivationConflict(
    OwnerTruthKnowledgeRecommendationActivationError
):
    """A command or selected candidate was reused incompatibly."""


class OwnerTruthKnowledgeRecommendationActivationStale(
    OwnerTruthKnowledgeRecommendationActivationConflict
):
    """The candidate or live conversation authority changed before acceptance."""


class OwnerTruthKnowledgeRecommendationActivationUnavailable(
    OwnerTruthKnowledgeRecommendationActivationError
):
    """The QA-only activation lane is disabled or cannot safely plan."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthKnowledgeRecommendationActivationError(
            "recommendation activation payload must be JSON serializable"
        ) from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _digest(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(value))


def _hash(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthKnowledgeRecommendationActivationError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _opaque_identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_IDENTIFIER.fullmatch(normalized) is None:
        raise OwnerTruthKnowledgeRecommendationActivationError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _positive_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OwnerTruthKnowledgeRecommendationActivationError(
            f"{field} must be a positive integer"
        )
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthKnowledgeRecommendationActivationError(
            f"{field} must be a non-negative integer"
        )
    return value


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(
            "only the Vault Owner may accept a recommendation"
        )


@dataclass(frozen=True)
class OwnerTruthKnowledgeRecommendationActivationCommand:
    """A caller can accept only one opaque result from a current server plan."""

    command_id: str
    expected_candidate_id: str
    slot: RecommendationSlot | str
    expected_session_version: int
    guided_recommendation_set_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "expected_candidate_id",
            _opaque_identifier(self.expected_candidate_id, field="expected_candidate_id"),
        )
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthKnowledgeRecommendationActivationError("slot is not supported") from exc
        _positive_int(self.expected_session_version, field="expected_session_version")
        if self.guided_recommendation_set_id is not None:
            object.__setattr__(
                self,
                "guided_recommendation_set_id",
                _hash(
                    self.guided_recommendation_set_id,
                    field="guided_recommendation_set_id",
                ),
            )

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        payload: dict[str, Any] = {
            "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION,
            "expectedCandidateId": self.expected_candidate_id,
            "slot": self.slot.value,
            "expectedSessionVersion": self.expected_session_version,
            "uiSchemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_UI_SCHEMA_VERSION,
        }
        # Keep the historical QA command digest stable when no formal guided
        # selection binding exists. Formal product activation adds this opaque
        # binding so command-id replay cannot cross recommendation sets.
        if self.guided_recommendation_set_id is not None:
            payload["guidedRecommendationSetId"] = self.guided_recommendation_set_id
        return _digest(payload)


@dataclass(frozen=True)
class OwnerTruthKnowledgeRecommendationActivationResult:
    """The minimal trace-safe acceptance outcome; no question or memory text."""

    outcome: str
    activation_id: str
    candidate_id: str
    slot: RecommendationSlot | str
    next_action: InterviewAction | str
    thread_id: str
    session_id: str
    expected_session_version: int
    target_dimension: KnowledgeDimension | str
    missing_facet: str
    authority_epoch: int
    evidence_ref_count: int
    reason_code: str

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "recommendation activation outcome is not supported"
            )
        for field in ("activation_id", "thread_id", "session_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        object.__setattr__(self, "candidate_id", _opaque_identifier(self.candidate_id, field="candidate_id"))
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
            object.__setattr__(self, "next_action", InterviewAction(self.next_action))
            object.__setattr__(self, "target_dimension", KnowledgeDimension(self.target_dimension))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "recommendation activation contains an unsupported enum value"
            ) from exc
        object.__setattr__(self, "missing_facet", require_nonblank(self.missing_facet, field="missing_facet"))
        if self.missing_facet not in knowledge_dimension_facets(self.target_dimension):
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "missing_facet is not valid for target_dimension"
            )
        _positive_int(self.expected_session_version, field="expected_session_version")
        _nonnegative_int(self.authority_epoch, field="authority_epoch")
        _nonnegative_int(self.evidence_ref_count, field="evidence_ref_count")
        object.__setattr__(self, "reason_code", _opaque_identifier(self.reason_code, field="reason_code"))
        expected_action = (
            InterviewAction.BROADEN
            if self.slot is RecommendationSlot.BREADTH
            else InterviewAction.LISTEN
        )
        if self.next_action is not expected_action:
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "recommendation slot must bind its deterministic next action"
            )


class OwnerTruthKnowledgeRecommendationActivationRepository(Protocol):
    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationActivationCommand,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult | None:
        ...

    def replay_guided(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id_hash: str,
        recommendation_set_id: str,
        slot: RecommendationSlot,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult | None:
        ...

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeRecommendationActivationResult:
        ...

    def list_accepted_candidate_ids(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> frozenset[str]:
        ...


class OwnerTruthKnowledgeRecommendationActivationStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_knowledge_recommendation_activation_repository(
        self,
    ) -> OwnerTruthKnowledgeRecommendationActivationRepository:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...

    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_knowledge_dimension_confirmation_repository(self) -> Any:
        ...

    def owner_truth_saved_continuation_cue_repository(self) -> Any:
        ...

    def owner_truth_thread_preference_repository(self) -> Any:
        ...


def _result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthKnowledgeRecommendationActivationResult:
    return OwnerTruthKnowledgeRecommendationActivationResult(
        outcome=outcome,
        activation_id=str(record.get("activationId") or ""),
        candidate_id=str(record.get("candidateId") or ""),
        slot=record.get("slot"),
        next_action=record.get("nextAction"),
        thread_id=str(record.get("threadId") or ""),
        session_id=str(record.get("sessionId") or ""),
        expected_session_version=record.get("expectedSessionVersion"),
        target_dimension=record.get("targetDimension"),
        missing_facet=str(record.get("missingFacet") or ""),
        authority_epoch=record.get("authorityEpoch"),
        evidence_ref_count=record.get("evidenceRefCount"),
        reason_code=str(record.get("reasonCode") or ""),
    )


class InMemoryOwnerTruthKnowledgeRecommendationActivationRepository:
    """Thread-safe semantic double for append-only recommendation acceptances."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._records_by_candidate: dict[tuple[str, str], dict[str, Any]] = {}

    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationActivationCommand,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult | None:
        _assert_owner_context(context)
        command_key = (context.vault_id, command.command_id_hash)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is None:
                return None
            if str(existing.get("payloadHash") or "") != command.payload_hash:
                raise OwnerTruthKnowledgeRecommendationActivationConflict(
                    "commandId cannot be reused with different recommendation acceptance"
                )
            return _result_from_record(existing, outcome="deduplicated")

    def replay_guided(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id_hash: str,
        recommendation_set_id: str,
        slot: RecommendationSlot,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult | None:
        _assert_owner_context(context)
        normalized_command_hash = _hash(command_id_hash, field="command_id_hash")
        normalized_set_id = _hash(
            recommendation_set_id,
            field="recommendation_set_id",
        )
        with self._lock:
            existing = self._records_by_command.get((context.vault_id, normalized_command_hash))
            if existing is None:
                return None
            if (
                str(existing.get("guidedRecommendationSetId") or "") != normalized_set_id
                or str(existing.get("slot") or "") != slot.value
            ):
                raise OwnerTruthKnowledgeRecommendationActivationConflict(
                    "commandId cannot be reused with a different guided recommendation"
                )
            return _result_from_record(existing, outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeRecommendationActivationResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        if (
            str(normalized.get("vaultId") or "") != context.vault_id
            or str(normalized.get("ownerSubjectId") or "") != context.owner_subject_id
            or str(normalized.get("actorSubjectId") or "") != context.owner_subject_id
        ):
            raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(
                "recommendation activation does not match Owner context"
            )
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        candidate_id = _opaque_identifier(normalized.get("candidateId"), field="candidateId")
        command_key = (context.vault_id, command_hash)
        candidate_key = (context.vault_id, candidate_id)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is not None:
                if str(existing.get("payloadHash") or "") != payload_hash:
                    raise OwnerTruthKnowledgeRecommendationActivationConflict(
                        "commandId cannot be reused with different recommendation acceptance"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            if candidate_key in self._records_by_candidate:
                raise OwnerTruthKnowledgeRecommendationActivationConflict(
                    "server-planned recommendation was already accepted"
                )
            self._records_by_command[command_key] = normalized
            self._records_by_candidate[candidate_key] = normalized
            return _result_from_record(normalized, outcome="created")

    def list_accepted_candidate_ids(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> frozenset[str]:
        _assert_owner_context(context)
        _nonnegative_int(authority_epoch, field="authority_epoch")
        with self._lock:
            return frozenset(
                str(record["candidateId"])
                for record in self._records_by_command.values()
                if str(record.get("vaultId") or "") == context.vault_id
                and str(record.get("ownerSubjectId") or "") == context.owner_subject_id
                and str(record.get("actorSubjectId") or "") == context.owner_subject_id
                and int(
                    record["authorityEpoch"]
                    if record.get("authorityEpoch") is not None
                    else -1
                )
                == authority_epoch
            )


class PostgresOwnerTruthKnowledgeRecommendationActivationRepository:
    """Postgres persistence for append-only, owner-scoped acceptance receipts."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationActivationCommand,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult | None:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-activation-command:"
                    f"{context.vault_id}:{command.command_id_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, slot, next_action, thread_id, session_id,
                    expected_session_version, target_dimension, missing_facet,
                    evidence_ref_count, reason_code, command_id_hash, command_payload_hash,
                    guided_recommendation_set_id
                FROM owner_truth.knowledge_recommendation_activation_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command.command_id_hash),
            )
            existing = cursor.fetchone()
        if existing is None:
            return None
        if str(existing["command_payload_hash"]) != command.payload_hash:
            raise OwnerTruthKnowledgeRecommendationActivationConflict(
                "commandId cannot be reused with different recommendation acceptance"
            )
        return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

    def replay_guided(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id_hash: str,
        recommendation_set_id: str,
        slot: RecommendationSlot,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult | None:
        _assert_owner_context(context)
        normalized_command_hash = _hash(command_id_hash, field="command_id_hash")
        normalized_set_id = _hash(
            recommendation_set_id,
            field="recommendation_set_id",
        )
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-activation-command:"
                    f"{context.vault_id}:{normalized_command_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, slot, next_action, thread_id, session_id,
                    expected_session_version, target_dimension, missing_facet,
                    evidence_ref_count, reason_code, command_id_hash, command_payload_hash,
                    guided_recommendation_set_id
                FROM owner_truth.knowledge_recommendation_activation_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, normalized_command_hash),
            )
            existing = cursor.fetchone()
        if existing is None:
            return None
        if (
            str(existing["guided_recommendation_set_id"] or "") != normalized_set_id
            or str(existing["slot"]) != slot.value
        ):
            raise OwnerTruthKnowledgeRecommendationActivationConflict(
                "commandId cannot be reused with a different guided recommendation"
            )
        return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeRecommendationActivationResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        candidate_id = _opaque_identifier(normalized.get("candidateId"), field="candidateId")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-activation-command:"
                    f"{context.vault_id}:{command_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, slot, next_action, thread_id, session_id,
                    expected_session_version, target_dimension, missing_facet,
                    evidence_ref_count, reason_code, command_id_hash, command_payload_hash,
                    guided_recommendation_set_id
                FROM owner_truth.knowledge_recommendation_activation_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != payload_hash:
                    raise OwnerTruthKnowledgeRecommendationActivationConflict(
                        "commandId cannot be reused with different recommendation acceptance"
                    )
                return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-activation-candidate:"
                    f"{context.vault_id}:{candidate_id}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id
                FROM owner_truth.knowledge_recommendation_activation_receipts
                WHERE vault_id = %s AND candidate_id = %s
                FOR UPDATE
                """,
                (context.vault_id, candidate_id),
            )
            if cursor.fetchone() is not None:
                raise OwnerTruthKnowledgeRecommendationActivationConflict(
                    "server-planned recommendation was already accepted"
                )
            self._assert_current_target(cursor, context=context, record=normalized)
            cursor.execute(
                """
                INSERT INTO owner_truth.knowledge_recommendation_activation_receipts (
                    id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, slot, next_action, thread_id, session_id,
                    expected_session_version, target_dimension, missing_facet,
                    selection_policy_version, orchestration_policy_version,
                    evidence_ref_count, evidence_ref_digest, reason_code,
                    command_id_hash, command_payload_hash, guided_recommendation_set_id,
                    schema_version, ui_schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s
                )
                """,
                (
                    normalized["activationId"],
                    normalized["vaultId"],
                    normalized["ownerSubjectId"],
                    normalized["actorSubjectId"],
                    normalized["authorityEpoch"],
                    candidate_id,
                    normalized["slot"],
                    normalized["nextAction"],
                    normalized["threadId"],
                    normalized["sessionId"],
                    normalized["expectedSessionVersion"],
                    normalized["targetDimension"],
                    normalized["missingFacet"],
                    normalized["selectionPolicyVersion"],
                    normalized["orchestrationPolicyVersion"],
                    normalized["evidenceRefCount"],
                    normalized["evidenceRefDigest"],
                    normalized["reasonCode"],
                    command_hash,
                    payload_hash,
                    normalized.get("guidedRecommendationSetId"),
                    normalized["schemaVersion"],
                    normalized["uiSchemaVersion"],
                ),
            )
        return _result_from_record(normalized, outcome="created")

    def list_accepted_candidate_ids(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> frozenset[str]:
        _assert_owner_context(context)
        _nonnegative_int(authority_epoch, field="authority_epoch")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT receipt.candidate_id
                FROM owner_truth.knowledge_recommendation_activation_receipts AS receipt
                JOIN owner_truth.vaults AS vault ON vault.vault_id = receipt.vault_id
                WHERE receipt.vault_id = %s
                  AND receipt.owner_subject_id = %s
                  AND receipt.actor_subject_id = %s
                  AND receipt.authority_epoch = %s
                  AND vault.owner_subject_id = %s
                  AND vault.status = 'active'
                  AND vault.authority_epoch = %s
                ORDER BY receipt.candidate_id ASC
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    context.owner_subject_id,
                    authority_epoch,
                    context.owner_subject_id,
                    authority_epoch,
                ),
            )
            return frozenset(str(row["candidate_id"]) for row in cursor.fetchall())

    @staticmethod
    def _assert_current_target(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> None:
        cursor.execute(
            """
            SELECT vault.owner_subject_id, vault.authority_epoch, vault.status,
                thread.owner_subject_id AS thread_owner_subject_id,
                thread.authority_epoch AS thread_authority_epoch, thread.state AS thread_state,
                session.owner_subject_id AS session_owner_subject_id,
                session.authority_epoch AS session_authority_epoch,
                session.current_thread_id, session.state AS session_state,
                session.boundary AS session_boundary, session.row_version AS session_row_version
            FROM owner_truth.vaults AS vault
            JOIN owner_truth.conversation_threads AS thread
              ON thread.vault_id = vault.vault_id AND thread.id = %s
            JOIN owner_truth.interview_sessions AS session
              ON session.vault_id = vault.vault_id AND session.id = %s
            WHERE vault.vault_id = %s
            FOR SHARE OF vault, thread, session
            """,
            (record["threadId"], record["sessionId"], context.vault_id),
        )
        snapshot = cursor.fetchone()
        if (
            snapshot is None
            or str(snapshot["owner_subject_id"]) != context.owner_subject_id
            or str(snapshot["status"]) != "active"
            or int(snapshot["authority_epoch"]) != int(record["authorityEpoch"])
            or str(snapshot["thread_owner_subject_id"]) != context.owner_subject_id
            or int(snapshot["thread_authority_epoch"]) != int(record["authorityEpoch"])
            or str(snapshot["thread_state"]) != "active"
            or str(snapshot["session_owner_subject_id"]) != context.owner_subject_id
            or int(snapshot["session_authority_epoch"]) != int(record["authorityEpoch"])
            or str(snapshot["current_thread_id"]) != str(record["threadId"])
            or str(snapshot["session_state"]) != "active"
            or str(snapshot["session_boundary"]) != "open"
            or int(snapshot["session_row_version"]) != int(record["expectedSessionVersion"])
        ):
            raise OwnerTruthKnowledgeRecommendationActivationStale(
                "recommendation acceptance must bind the current active open interview session"
            )

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "activationId": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "ownerSubjectId": str(row["owner_subject_id"]),
            "actorSubjectId": str(row["actor_subject_id"]),
            "authorityEpoch": int(row["authority_epoch"]),
            "candidateId": str(row["candidate_id"]),
            "slot": str(row["slot"]),
            "nextAction": str(row["next_action"]),
            "threadId": str(row["thread_id"]),
            "sessionId": str(row["session_id"]),
            "expectedSessionVersion": int(row["expected_session_version"]),
            "targetDimension": str(row["target_dimension"]),
            "missingFacet": str(row["missing_facet"]),
            "evidenceRefCount": int(row["evidence_ref_count"]),
            "reasonCode": str(row["reason_code"]),
            "commandIdHash": str(row["command_id_hash"]),
            "payloadHash": str(row["command_payload_hash"]),
            "guidedRecommendationSetId": row.get("guided_recommendation_set_id"),
        }

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthKnowledgeRecommendationActivationService:
    """Persist only a server-revalidated selection from the current QA plan."""

    def __init__(
        self,
        store: OwnerTruthKnowledgeRecommendationActivationStore,
        *,
        enabled: bool = False,
    ) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def accept(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationActivationCommand,
    ) -> OwnerTruthKnowledgeRecommendationActivationResult:
        _assert_owner_context(context)
        if not isinstance(command, OwnerTruthKnowledgeRecommendationActivationCommand):
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "recommendation activation command is required"
            )
        if not self._enabled:
            raise OwnerTruthKnowledgeRecommendationActivationUnavailable(
                "recommendation activation QA contract is disabled"
            )
        with self._request_unit_of_work(
            correlation_id=(
                "owner-truth-knowledge-recommendation-activation-"
                f"{context.vault_id}:{command.expected_candidate_id}"
            ),
            command_id=command.command_id_hash,
        ):
            repository = self._store.owner_truth_knowledge_recommendation_activation_repository()
            replayed = repository.replay(context=context, command=command)
            if replayed is not None:
                return replayed

            try:
                plan = OwnerTruthKnowledgeRecommendationReadService(self._store).plan(
                    context=context
                )
            except OwnerTruthContractError as error:
                raise OwnerTruthKnowledgeRecommendationActivationUnavailable(
                    "current Owner-confirmed recommendation plan is unavailable"
                ) from error
            if (
                plan.state is not OwnerTruthKnowledgeDimensionReadState.READY
                or plan.selection is None
            ):
                raise OwnerTruthKnowledgeRecommendationActivationUnavailable(
                    "current Owner-confirmed recommendation plan is unavailable"
                )
            decision = self._selected_decision(plan.selection.selected, command=command)
            conversation = self._store.owner_truth_conversation_repository()
            try:
                session_id = self._session_id_for_decision(
                    conversation=conversation,
                    context=context,
                    decision=decision,
                )
                session = conversation.get_interview_session(
                    session_id=session_id,
                    context=context,
                )
            except OwnerTruthConversationAccessDenied as error:
                raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(str(error)) from error
            except OwnerTruthContractError as error:
                raise OwnerTruthKnowledgeRecommendationActivationStale(
                    "server-planned recommendation no longer has current conversation authority"
                ) from error
            if (
                session.thread_id != decision.thread_id
                or session.row_version != command.expected_session_version
                or session.state is not InterviewSessionState.ACTIVE
                or session.boundary is not InterviewBoundary.OPEN
                or session.authority_epoch != plan.dimension_read.authority_epoch
            ):
                raise OwnerTruthKnowledgeRecommendationActivationStale(
                    "server-planned recommendation no longer binds the current active open interview session"
                )

            orchestration = OwnerTruthInterviewSessionOrchestrationService(
                conversation_service=OwnerTruthConversationService(conversation)
            ).decide(
                session_id=session.session_id,
                context=context,
                signals=InterviewSessionOrchestrationSignals(
                    topic_id="serverPlannedRecommendation",
                    accepted_broaden_recommendation=(command.slot is RecommendationSlot.BREADTH),
                ),
            )
            expected_action = (
                InterviewAction.BROADEN
                if command.slot is RecommendationSlot.BREADTH
                else InterviewAction.LISTEN
            )
            if orchestration.decision.action is not expected_action:
                raise OwnerTruthKnowledgeRecommendationActivationStale(
                    "current interview policy no longer permits the accepted recommendation"
                )
            record = {
                "activationId": str(
                    uuid5(_ACTIVATION_NAMESPACE, f"{context.vault_id}:{command.command_id_hash}")
                ),
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "actorSubjectId": context.actor_subject_id,
                "authorityEpoch": plan.dimension_read.authority_epoch,
                "candidateId": decision.candidate_id,
                "slot": decision.slot.value,
                "nextAction": orchestration.decision.action.value,
                "threadId": decision.thread_id,
                "sessionId": session.session_id,
                "expectedSessionVersion": command.expected_session_version,
                "targetDimension": decision.target_dimension.value,
                "missingFacet": decision.missing_facet,
                "selectionPolicyVersion": decision.policy_version,
                "orchestrationPolicyVersion": "owner-truth-interview-orchestration-v1",
                "evidenceRefCount": len(decision.evidence_refs),
                "evidenceRefDigest": _digest(
                    {"evidenceRefs": sorted(decision.evidence_refs)}
                ),
                "reasonCode": orchestration.decision.reason_code,
                "commandIdHash": command.command_id_hash,
                "payloadHash": command.payload_hash,
                "guidedRecommendationSetId": command.guided_recommendation_set_id,
                "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION,
                "uiSchemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_UI_SCHEMA_VERSION,
            }
            return repository.record(context=context, record=record)

    @staticmethod
    def _selected_decision(
        decisions: tuple[RecommendationDecision, ...],
        *,
        command: OwnerTruthKnowledgeRecommendationActivationCommand,
    ) -> RecommendationDecision:
        matches = tuple(
            decision
            for decision in decisions
            if decision.candidate_id == command.expected_candidate_id and decision.slot is command.slot
        )
        if len(matches) != 1:
            raise OwnerTruthKnowledgeRecommendationActivationStale(
                "server-planned recommendation is no longer selected"
            )
        return matches[0]

    @staticmethod
    def _session_id_for_decision(
        *,
        conversation: Any,
        context: OwnerTruthCommandContext,
        decision: RecommendationDecision,
    ) -> str:
        thread = conversation.get_interview_thread_authority(
            thread_id=decision.thread_id,
            context=context,
        )
        if thread.session_id is None:
            raise OwnerTruthKnowledgeRecommendationActivationStale(
                "server-planned recommendation has no current interview session"
            )
        return thread.session_id

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


def knowledge_recommendation_activation_summary(
    result: OwnerTruthKnowledgeRecommendationActivationResult,
) -> dict[str, Any]:
    if not isinstance(result, OwnerTruthKnowledgeRecommendationActivationResult):
        raise OwnerTruthKnowledgeRecommendationActivationError(
            "recommendation activation result is required"
        )
    return {
        "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION,
        "status": result.outcome,
        "activationId": result.activation_id,
        "candidateId": result.candidate_id,
        "slot": result.slot.value,
        "nextAction": result.next_action.value,
        "threadId": result.thread_id,
        "sessionId": result.session_id,
        "expectedSessionVersion": result.expected_session_version,
        "targetDimension": result.target_dimension.value,
        "missingFacet": result.missing_facet,
        "authorityEpoch": result.authority_epoch,
        "evidenceRefCount": result.evidence_ref_count,
        "reasonCode": result.reason_code,
    }


__all__ = [
    "InMemoryOwnerTruthKnowledgeRecommendationActivationRepository",
    "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_UI_SCHEMA_VERSION",
    "OwnerTruthKnowledgeRecommendationActivationAccessDenied",
    "OwnerTruthKnowledgeRecommendationActivationCommand",
    "OwnerTruthKnowledgeRecommendationActivationConflict",
    "OwnerTruthKnowledgeRecommendationActivationError",
    "OwnerTruthKnowledgeRecommendationActivationRepository",
    "OwnerTruthKnowledgeRecommendationActivationResult",
    "OwnerTruthKnowledgeRecommendationActivationService",
    "OwnerTruthKnowledgeRecommendationActivationStale",
    "OwnerTruthKnowledgeRecommendationActivationUnavailable",
    "PostgresOwnerTruthKnowledgeRecommendationActivationRepository",
    "knowledge_recommendation_activation_summary",
]

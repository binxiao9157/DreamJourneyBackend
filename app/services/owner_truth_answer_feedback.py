"""QA-only Owner feedback receipts for cited Owner Truth answers.

The feedback boundary is deliberately narrower than public Owner QA.  It
persists an immutable, value-free acknowledgement of whether an Owner found a
previously recorded answer helpful.  It never stores question, answer or
memory text, and it never turns a stale citation into a quality signal.

This is a G0 prerequisite for later product metrics, not a claim that WTMR or
public feedback collection is live.  Conversation chronology, cohort filtering
and metric aggregation remain separate release-gated work.
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

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


OWNER_TRUTH_ANSWER_FEEDBACK_SCHEMA_VERSION = "owner-truth-answer-feedback-v1"
OWNER_TRUTH_ANSWER_CITATION_READ_SCHEMA_VERSION = "owner-truth-answer-citation-read-v1"
_FEEDBACK_NAMESPACE = UUID("4c3d5d21-332e-495f-b413-1f6f391cb6fb")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ELIGIBILITY_REASONS = {
    "eligible",
    "notHelpful",
    "noCitations",
    "projectionUnavailable",
    "citationNotCurrent",
    "rightsRevisionChanged",
    "rightsRevoked",
}
_CITATION_CURRENTNESS_REASONS = {
    "current",
    "citationNotCurrent",
    "projectionUnavailable",
    "projectionInputsChanged",
    "rightsRevisionChanged",
    "rightsRevoked",
}


class OwnerTruthAnswerFeedbackError(OwnerTruthMemoryProjectionError):
    """An Owner feedback receipt cannot be safely recorded."""


class OwnerTruthAnswerFeedbackConflict(OwnerTruthAnswerFeedbackError):
    """A feedback command or answer was replayed with different meaning."""


class OwnerTruthAnswerFeedbackNotFound(OwnerTruthAnswerFeedbackError):
    """The requested answer is not owned by the current Owner Vault."""


class OwnerTruthAnswerFeedbackUnavailable(OwnerTruthAnswerFeedbackError):
    """The default-off feedback boundary is disabled."""


class OwnerTruthAnswerCitationReadNotFound(OwnerTruthAnswerFeedbackError):
    """The requested Answer/Citation receipt is outside the Owner Vault."""


class OwnerTruthAnswerCitationReadUnavailable(OwnerTruthAnswerFeedbackError):
    """The default-off Answer/Citation read boundary is disabled."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthAnswerFeedbackError("answer feedback values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerTruthAnswerFeedbackError(f"{field} must be text")
    normalized = value.strip()
    if not normalized:
        raise OwnerTruthAnswerFeedbackError(f"{field} must be nonblank")
    return normalized


def _opaque_identifier(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthAnswerFeedbackError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthAnswerFeedbackError(f"{field} must be a UUID") from exc


def _hash(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthAnswerFeedbackError(f"{field} must be a sha256 digest")
    return normalized


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthAnswerFeedbackError(f"{field} must be non-negative")
    return value


def _optional_nonnegative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field=field)


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthAnswerFeedbackError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthMemoryProjectionAccessDenied(
            "only the Vault Owner may record answer feedback"
        )


@dataclass(frozen=True)
class OwnerTruthAnswerFeedbackCommand:
    """One explicit, idempotent Owner answer-feedback action.

    Free-text rationale is intentionally not accepted in this G0 receipt.  It
    would require its own Source/retention/purpose boundary before it could be
    stored or used for model evaluation.
    """

    command_id: str
    answer_id: str
    helpful: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _opaque_identifier(self.command_id, field="command_id"))
        object.__setattr__(self, "answer_id", _uuid(self.answer_id, field="answer_id"))
        if not isinstance(self.helpful, bool):
            raise OwnerTruthAnswerFeedbackError("helpful must be boolean")

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "schemaVersion": OWNER_TRUTH_ANSWER_FEEDBACK_SCHEMA_VERSION,
                "answerId": self.answer_id,
                "helpful": self.helpful,
            }
        )


@dataclass(frozen=True)
class OwnerTruthAnswerFeedbackResult:
    outcome: str
    feedback_id: str
    answer_id: str
    helpful: bool
    citation_count: int
    eligible_citation_count: int
    metric_eligible: bool
    eligibility_reason: str
    authority_epoch: int

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthAnswerFeedbackError("answer feedback outcome is not supported")
        object.__setattr__(self, "feedback_id", _uuid(self.feedback_id, field="feedback_id"))
        object.__setattr__(self, "answer_id", _uuid(self.answer_id, field="answer_id"))
        if not isinstance(self.helpful, bool) or not isinstance(self.metric_eligible, bool):
            raise OwnerTruthAnswerFeedbackError("feedback booleans are invalid")
        citation_count = _nonnegative_int(self.citation_count, field="citation_count")
        eligible_count = _nonnegative_int(
            self.eligible_citation_count,
            field="eligible_citation_count",
        )
        authority_epoch = _nonnegative_int(self.authority_epoch, field="authority_epoch")
        reason = _nonblank_text(self.eligibility_reason, field="eligibility_reason")
        if reason not in _ELIGIBILITY_REASONS:
            raise OwnerTruthAnswerFeedbackError("feedback eligibility reason is unsupported")
        if eligible_count > citation_count:
            raise OwnerTruthAnswerFeedbackError("eligible citations exceed cited answer citations")
        if self.metric_eligible != (
            self.helpful and citation_count > 0 and eligible_count == citation_count and reason == "eligible"
        ):
            raise OwnerTruthAnswerFeedbackError("feedback metric eligibility is inconsistent")
        object.__setattr__(self, "citation_count", citation_count)
        object.__setattr__(self, "eligible_citation_count", eligible_count)
        object.__setattr__(self, "authority_epoch", authority_epoch)
        object.__setattr__(self, "eligibility_reason", reason)


@dataclass(frozen=True)
class OwnerTruthAnswerCitationReadResult:
    """Value-free currentness view for one immutable Answer/Citation receipt."""

    answer_id: str
    context_hash: str
    context_version: str
    answer_authority_epoch: int | None
    projection_authority_epoch: int
    recorded_projection_checkpoint: str | None
    current_projection_checkpoint: str | None
    projection_state: str
    citations: tuple[Mapping[str, Any], ...]
    fallbacks: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "answer_id", _uuid(self.answer_id, field="answer_id"))
        object.__setattr__(self, "context_hash", _hash(self.context_hash, field="context_hash"))
        object.__setattr__(
            self,
            "context_version",
            _nonblank_text(self.context_version, field="context_version"),
        )
        object.__setattr__(
            self,
            "answer_authority_epoch",
            _optional_nonnegative_int(
                self.answer_authority_epoch,
                field="answer_authority_epoch",
            ),
        )
        object.__setattr__(
            self,
            "projection_authority_epoch",
            _nonnegative_int(
                self.projection_authority_epoch,
                field="projection_authority_epoch",
            ),
        )
        object.__setattr__(
            self,
            "recorded_projection_checkpoint",
            None
            if self.recorded_projection_checkpoint is None
            else _hash(
                self.recorded_projection_checkpoint,
                field="recorded_projection_checkpoint",
            ),
        )
        object.__setattr__(
            self,
            "current_projection_checkpoint",
            None
            if self.current_projection_checkpoint is None
            else _hash(
                self.current_projection_checkpoint,
                field="current_projection_checkpoint",
            ),
        )
        state = _nonblank_text(self.projection_state, field="projection_state")
        if state not in {"ready", "rebuilding"}:
            raise OwnerTruthAnswerFeedbackError("citation read projection state is unsupported")
        object.__setattr__(self, "projection_state", state)
        if not isinstance(self.citations, tuple):
            raise OwnerTruthAnswerFeedbackError("citation read citations must be a tuple")
        normalized_citations: list[dict[str, Any]] = []
        for citation in self.citations:
            if not isinstance(citation, Mapping):
                raise OwnerTruthAnswerFeedbackError("citation read citation is invalid")
            normalized = _citation_read_row(
                citation,
                current=bool(citation.get("current")),
                currentness=str(citation.get("currentness") or ""),
            )
            if normalized["current"] != (normalized["currentness"] == "current"):
                raise OwnerTruthAnswerFeedbackError("citation currentness is inconsistent")
            normalized_citations.append(normalized)
        normalized_citations.sort(key=lambda item: (item["position"], item["citationId"]))
        object.__setattr__(self, "citations", tuple(normalized_citations))
        if not isinstance(self.fallbacks, tuple):
            raise OwnerTruthAnswerFeedbackError("citation read fallbacks must be a tuple")
        object.__setattr__(
            self,
            "fallbacks",
            tuple(_nonblank_text(item, field="fallback") for item in self.fallbacks),
        )

    @property
    def citation_count(self) -> int:
        return len(self.citations)

    @property
    def current_citation_count(self) -> int:
        return sum(1 for citation in self.citations if citation["current"])


class OwnerTruthAnswerFeedbackStore(Protocol):
    def owner_truth_answer_citation_repository(self) -> Any:
        ...

    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_answer_feedback_repository(self) -> Any:
        ...


def _citation_signature(citation: Mapping[str, Any]) -> tuple[str, str, int, str, int, str]:
    fields = citation.get("citation") if isinstance(citation, Mapping) else None
    if not isinstance(fields, Mapping):
        raise OwnerTruthAnswerFeedbackError("answer citation is invalid")
    return (
        _uuid(fields.get("memoryId"), field="citation.memoryId"),
        _uuid(fields.get("memoryVersionId"), field="citation.memoryVersionId"),
        _nonnegative_int(fields.get("memoryVersion"), field="citation.memoryVersion"),
        _uuid(fields.get("sourceId"), field="citation.sourceId"),
        _nonnegative_int(fields.get("sourceVersion"), field="citation.sourceVersion"),
        _hash(fields.get("contentHash"), field="citation.contentHash"),
    )


def _projection_signatures(snapshot: Mapping[str, Any]) -> set[tuple[str, str, int, str, int, str]] | None:
    if not isinstance(snapshot, Mapping):
        raise OwnerTruthAnswerFeedbackError("memory projection snapshot is invalid")
    if _nonblank_text(snapshot.get("state"), field="projection.state") != "ready":
        return None
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        raise OwnerTruthAnswerFeedbackError("memory projection entries are invalid")
    signatures: set[tuple[str, str, int, str, int, str]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OwnerTruthAnswerFeedbackError("memory projection entry is invalid")
        citation = entry.get("citation")
        if not isinstance(citation, Mapping):
            raise OwnerTruthAnswerFeedbackError("memory projection citation is invalid")
        signatures.add(
            (
                _uuid(citation.get("memoryId"), field="projection.citation.memoryId"),
                _uuid(citation.get("memoryVersionId"), field="projection.citation.memoryVersionId"),
                _nonnegative_int(entry.get("memoryVersion"), field="projection.memoryVersion"),
                _uuid(citation.get("sourceId"), field="projection.citation.sourceId"),
                _nonnegative_int(citation.get("sourceVersion"), field="projection.citation.sourceVersion"),
                _hash(citation.get("contentHash"), field="projection.citation.contentHash"),
            )
        )
    return signatures


def _projection_currentness_reason(snapshot: Mapping[str, Any]) -> str:
    """Return a value-free reason for why a Projection cannot prove currentness."""

    if not isinstance(snapshot, Mapping):
        raise OwnerTruthAnswerFeedbackError("memory projection snapshot is invalid")
    state = _nonblank_text(snapshot.get("state"), field="projection.state")
    if state == "ready":
        return "current"
    reason = str(snapshot.get("rebuildReason") or "").strip()
    if reason in _CITATION_CURRENTNESS_REASONS:
        return reason
    return "projectionUnavailable"


def _citation_read_row(
    citation: Mapping[str, Any],
    *,
    current: bool,
    currentness: str,
) -> dict[str, Any]:
    """Normalize a typed citation without accidentally returning answer content."""

    if currentness not in _CITATION_CURRENTNESS_REASONS:
        raise OwnerTruthAnswerFeedbackError("citation currentness reason is unsupported")
    fields = citation.get("citation") if isinstance(citation, Mapping) else None
    if not isinstance(fields, Mapping):
        raise OwnerTruthAnswerFeedbackError("answer citation is invalid")
    return {
        "citationId": _uuid(citation.get("citationId"), field="citation.citationId"),
        "position": _nonnegative_int(citation.get("position"), field="citation.position"),
        "recordedResolution": _nonblank_text(
            citation.get("recordedResolution") or citation.get("resolution"),
            field="citation.resolution",
        ),
        "current": bool(current),
        "currentness": currentness,
        "citation": {
            "vaultId": _nonblank_text(fields.get("vaultId"), field="citation.vaultId"),
            "memoryId": _uuid(fields.get("memoryId"), field="citation.memoryId"),
            "memoryVersionId": _uuid(
                fields.get("memoryVersionId"),
                field="citation.memoryVersionId",
            ),
            "memoryVersion": _nonnegative_int(
                fields.get("memoryVersion"),
                field="citation.memoryVersion",
            ),
            "sourceId": _uuid(fields.get("sourceId"), field="citation.sourceId"),
            "sourceVersion": _nonnegative_int(
                fields.get("sourceVersion"),
                field="citation.sourceVersion",
            ),
            "contentHash": _hash(fields.get("contentHash"), field="citation.contentHash"),
        },
    }


def _result_from_record(record: Mapping[str, Any], *, outcome: str) -> OwnerTruthAnswerFeedbackResult:
    return OwnerTruthAnswerFeedbackResult(
        outcome=outcome,
        feedback_id=str(record.get("feedbackId") or ""),
        answer_id=str(record.get("answerId") or ""),
        helpful=record.get("helpful"),
        citation_count=record.get("citationCount"),
        eligible_citation_count=record.get("eligibleCitationCount"),
        metric_eligible=record.get("metricEligible"),
        eligibility_reason=str(record.get("eligibilityReason") or ""),
        authority_epoch=record.get("authorityEpoch"),
    )


class InMemoryOwnerTruthAnswerFeedbackRepository:
    """Semantic double for append-only, one-feedback-per-answer receipts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._records_by_answer: dict[tuple[str, str], dict[str, Any]] = {}

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthAnswerFeedbackResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        command_key = (context.vault_id, _hash(normalized.get("commandIdHash"), field="commandIdHash"))
        answer_id = _uuid(normalized.get("answerId"), field="answerId")
        answer_key = (context.vault_id, answer_id)
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is not None:
                if existing.get("payloadHash") != payload_hash:
                    raise OwnerTruthAnswerFeedbackConflict(
                        "commandId cannot be reused with different answer feedback"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            answer_existing = self._records_by_answer.get(answer_key)
            if answer_existing is not None:
                raise OwnerTruthAnswerFeedbackConflict(
                    "answer feedback is already recorded and cannot be overwritten"
                )
            self._records_by_command[command_key] = normalized
            self._records_by_answer[answer_key] = normalized
            return _result_from_record(normalized, outcome="created")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"records": deepcopy(list(self._records_by_command.values()))}


class PostgresOwnerTruthAnswerFeedbackRepository:
    """Postgres receipt writer bound to the active request Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthAnswerFeedbackResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        answer_id = _uuid(normalized.get("answerId"), field="answerId")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-answer-feedback:{context.vault_id}:{answer_id}",),
            )
            cursor.execute(
                """
                SELECT id, command_id_hash, command_payload_hash, answer_id, helpful,
                    citation_count, eligible_citation_count, metric_eligible,
                    eligibility_reason, authority_epoch
                FROM owner_truth.answer_feedback
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != payload_hash:
                    raise OwnerTruthAnswerFeedbackConflict(
                        "commandId cannot be reused with different answer feedback"
                    )
                return self._result_from_row(existing, outcome="deduplicated")
            cursor.execute(
                """
                SELECT id
                FROM owner_truth.answer_feedback
                WHERE vault_id = %s AND answer_id = %s
                FOR UPDATE
                """,
                (context.vault_id, answer_id),
            )
            if cursor.fetchone() is not None:
                raise OwnerTruthAnswerFeedbackConflict(
                    "answer feedback is already recorded and cannot be overwritten"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.answer_feedback (
                    id, vault_id, owner_subject_id, command_id_hash,
                    command_payload_hash, answer_id, helpful, citation_count,
                    eligible_citation_count, metric_eligible, eligibility_reason,
                    authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _uuid(normalized.get("feedbackId"), field="feedbackId"),
                    context.vault_id,
                    context.owner_subject_id,
                    command_hash,
                    payload_hash,
                    answer_id,
                    bool(normalized.get("helpful")),
                    _nonnegative_int(normalized.get("citationCount"), field="citationCount"),
                    _nonnegative_int(
                        normalized.get("eligibleCitationCount"),
                        field="eligibleCitationCount",
                    ),
                    bool(normalized.get("metricEligible")),
                    _nonblank_text(normalized.get("eligibilityReason"), field="eligibilityReason"),
                    _nonnegative_int(normalized.get("authorityEpoch"), field="authorityEpoch"),
                ),
            )
            return _result_from_record(normalized, outcome="created")

    @staticmethod
    def _result_from_row(row: Mapping[str, Any], *, outcome: str) -> OwnerTruthAnswerFeedbackResult:
        return OwnerTruthAnswerFeedbackResult(
            outcome=outcome,
            feedback_id=str(row["id"]),
            answer_id=str(row["answer_id"]),
            helpful=bool(row["helpful"]),
            citation_count=int(row["citation_count"]),
            eligible_citation_count=int(row["eligible_citation_count"]),
            metric_eligible=bool(row["metric_eligible"]),
            eligibility_reason=str(row["eligibility_reason"]),
            authority_epoch=int(row["authority_epoch"]),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthAnswerFeedbackService:
    """Record one feedback receipt after re-reading current Owner Projection."""

    def __init__(self, store: OwnerTruthAnswerFeedbackStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthAnswerFeedbackCommand,
    ) -> OwnerTruthAnswerFeedbackResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise OwnerTruthAnswerFeedbackUnavailable("Owner answer feedback is disabled")
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-answer-feedback-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            answer = self._store.owner_truth_answer_citation_repository().find_answer(
                context=context,
                answer_id=command.answer_id,
            )
            if answer is None:
                raise OwnerTruthAnswerFeedbackNotFound("answer is not available in this Owner Vault")
            record = self._record_input(context=context, command=command, answer=answer)
            return self._store.owner_truth_answer_feedback_repository().record(
                context=context,
                record=record,
            )

    def _record_input(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthAnswerFeedbackCommand,
        answer: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(answer, Mapping):
            raise OwnerTruthAnswerFeedbackError("answer receipt is invalid")
        if _uuid(answer.get("answerId"), field="answer.answerId") != command.answer_id:
            raise OwnerTruthAnswerFeedbackNotFound("answer receipt does not match requested answer")
        if _nonblank_text(answer.get("vaultId"), field="answer.vaultId") != context.vault_id:
            raise OwnerTruthMemoryProjectionAccessDenied("answer belongs to another Vault")
        if _nonblank_text(answer.get("ownerSubjectId"), field="answer.ownerSubjectId") != context.owner_subject_id:
            raise OwnerTruthMemoryProjectionAccessDenied("answer belongs to another Owner")
        answer_authority_epoch = _optional_nonnegative_int(
            answer.get("authorityEpoch"),
            field="answer.authorityEpoch",
        )
        citations = answer.get("citations")
        if not isinstance(citations, list):
            raise OwnerTruthAnswerFeedbackError("answer citations are invalid")
        citation_signatures = {_citation_signature(item) for item in citations}
        if len(citation_signatures) != len(citations):
            raise OwnerTruthAnswerFeedbackError("answer citations must be unique")

        projection = OwnerTruthMemoryProjectionService(self._store).read(context=context)
        projection_signatures = _projection_signatures(projection)
        projection_authority_epoch = _nonnegative_int(
            projection.get("authorityEpoch"),
            field="projection.authorityEpoch",
        )
        eligible_count = (
            0
            if projection_signatures is None
            or answer_authority_epoch is None
            or answer_authority_epoch != projection_authority_epoch
            else sum(signature in projection_signatures for signature in citation_signatures)
        )
        if not citations:
            reason = "noCitations"
        elif projection_signatures is None:
            currentness_reason = _projection_currentness_reason(projection)
            reason = (
                currentness_reason
                if currentness_reason in {"rightsRevisionChanged", "rightsRevoked"}
                else "projectionUnavailable"
            )
        elif answer_authority_epoch is None or answer_authority_epoch != projection_authority_epoch:
            reason = "citationNotCurrent"
        elif eligible_count != len(citations):
            reason = "citationNotCurrent"
        elif not command.helpful:
            reason = "notHelpful"
        else:
            reason = "eligible"
        metric_eligible = reason == "eligible"
        return {
            "feedbackId": str(
                uuid5(_FEEDBACK_NAMESPACE, f"{context.vault_id}:{command.command_id_hash}")
            ),
            "vaultId": context.vault_id,
            "ownerSubjectId": context.owner_subject_id,
            "commandIdHash": command.command_id_hash,
            "payloadHash": command.payload_hash,
            "answerId": command.answer_id,
            "helpful": command.helpful,
            "citationCount": len(citations),
            "eligibleCitationCount": eligible_count,
            "metricEligible": metric_eligible,
            "eligibilityReason": reason,
            # Feedback binds to the current Vault epoch.  A no-citation answer
            # can legitimately have no original Projection epoch, but it can
            # still be retained as a non-metric Owner acknowledgement.
            "authorityEpoch": projection_authority_epoch,
        }

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


class OwnerTruthAnswerCitationReadService:
    """Resolve an immutable Answer receipt into a currentness-only QA view.

    The response deliberately carries typed identifiers and hashes only.  It
    allows the Owner QA lane to distinguish a missing citation from a stale
    projection without replaying the question, answer, or projected memory
    text into a new read surface.
    """

    def __init__(self, store: OwnerTruthAnswerFeedbackStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        answer_id: str,
    ) -> OwnerTruthAnswerCitationReadResult:
        _assert_owner_context(context)
        normalized_answer_id = _uuid(answer_id, field="answer_id")
        if not self._enabled:
            raise OwnerTruthAnswerCitationReadUnavailable("Owner answer citation read is disabled")
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-answer-citation-read-{normalized_answer_id}",
            command_id=f"ownerTruthAnswerCitationRead:{normalized_answer_id}",
        ):
            answer = self._store.owner_truth_answer_citation_repository().find_answer(
                context=context,
                answer_id=normalized_answer_id,
            )
            if answer is None:
                raise OwnerTruthAnswerCitationReadNotFound(
                    "answer is not available in this Owner Vault"
                )
            return self._result_from_answer(context=context, answer=answer)

    def _result_from_answer(
        self,
        *,
        context: OwnerTruthCommandContext,
        answer: Mapping[str, Any],
    ) -> OwnerTruthAnswerCitationReadResult:
        if not isinstance(answer, Mapping):
            raise OwnerTruthAnswerFeedbackError("answer receipt is invalid")
        answer_id = _uuid(answer.get("answerId"), field="answer.answerId")
        if _nonblank_text(answer.get("vaultId"), field="answer.vaultId") != context.vault_id:
            raise OwnerTruthMemoryProjectionAccessDenied("answer belongs to another Vault")
        if (
            _nonblank_text(answer.get("ownerSubjectId"), field="answer.ownerSubjectId")
            != context.owner_subject_id
        ):
            raise OwnerTruthMemoryProjectionAccessDenied("answer belongs to another Owner")
        citations = answer.get("citations")
        if not isinstance(citations, list):
            raise OwnerTruthAnswerFeedbackError("answer citations are invalid")
        answer_authority_epoch = _optional_nonnegative_int(
            answer.get("authorityEpoch"),
            field="answer.authorityEpoch",
        )
        projection = OwnerTruthMemoryProjectionService(self._store).read(context=context)
        projection_signatures = _projection_signatures(projection)
        projection_state = _nonblank_text(projection.get("state"), field="projection.state")
        projection_authority_epoch = _nonnegative_int(
            projection.get("authorityEpoch"),
            field="projection.authorityEpoch",
        )
        projection_checkpoint = (
            None
            if projection.get("checkpoint") is None
            else _hash(projection.get("checkpoint"), field="projection.checkpoint")
        )
        unavailable_reason = _projection_currentness_reason(projection)
        rows: list[dict[str, Any]] = []
        for citation in citations:
            signature = _citation_signature(citation)
            if projection_signatures is None:
                currentness = unavailable_reason
                current = False
            elif answer_authority_epoch is None or answer_authority_epoch != projection_authority_epoch:
                currentness = "citationNotCurrent"
                current = False
            elif signature in projection_signatures:
                currentness = "current"
                current = True
            else:
                currentness = "citationNotCurrent"
                current = False
            rows.append(
                _citation_read_row(
                    citation,
                    current=current,
                    currentness=currentness,
                )
            )
        fallbacks = answer.get("fallbacks")
        if not isinstance(fallbacks, (list, tuple)):
            raise OwnerTruthAnswerFeedbackError("answer fallbacks are invalid")
        return OwnerTruthAnswerCitationReadResult(
            answer_id=answer_id,
            context_hash=_hash(answer.get("contextHash"), field="answer.contextHash"),
            context_version=_nonblank_text(
                answer.get("contextVersion"),
                field="answer.contextVersion",
            ),
            answer_authority_epoch=answer_authority_epoch,
            projection_authority_epoch=projection_authority_epoch,
            recorded_projection_checkpoint=(
                None
                if answer.get("projectionCheckpoint") is None
                else _hash(
                    answer.get("projectionCheckpoint"),
                    field="answer.projectionCheckpoint",
                )
            ),
            current_projection_checkpoint=projection_checkpoint,
            projection_state=projection_state,
            citations=tuple(rows),
            fallbacks=tuple(_nonblank_text(item, field="fallback") for item in fallbacks),
        )

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


def answer_feedback_summary(result: OwnerTruthAnswerFeedbackResult) -> dict[str, Any]:
    """Return only receipt and eligibility metadata, never private answer text."""

    if not isinstance(result, OwnerTruthAnswerFeedbackResult):
        raise OwnerTruthAnswerFeedbackError("answer feedback result is required")
    return {
        "schemaVersion": OWNER_TRUTH_ANSWER_FEEDBACK_SCHEMA_VERSION,
        "outcome": result.outcome,
        "feedbackId": result.feedback_id,
        "answerId": result.answer_id,
        "helpful": result.helpful,
        "citationCount": result.citation_count,
        "eligibleCitationCount": result.eligible_citation_count,
        "metricEligible": result.metric_eligible,
        "eligibilityReason": result.eligibility_reason,
        "authorityEpoch": result.authority_epoch,
    }


def answer_citation_read_summary(result: OwnerTruthAnswerCitationReadResult) -> dict[str, Any]:
    """Return a currentness-only answer citation read without raw content."""

    if not isinstance(result, OwnerTruthAnswerCitationReadResult):
        raise OwnerTruthAnswerFeedbackError("answer citation read result is required")
    return {
        "schemaVersion": OWNER_TRUTH_ANSWER_CITATION_READ_SCHEMA_VERSION,
        "answerId": result.answer_id,
        "contextHash": result.context_hash,
        "contextVersion": result.context_version,
        "answerAuthorityEpoch": result.answer_authority_epoch,
        "projectionAuthorityEpoch": result.projection_authority_epoch,
        "recordedProjectionCheckpoint": result.recorded_projection_checkpoint,
        "currentProjectionCheckpoint": result.current_projection_checkpoint,
        "projectionState": result.projection_state,
        "citationCount": result.citation_count,
        "currentCitationCount": result.current_citation_count,
        "citations": [deepcopy(dict(item)) for item in result.citations],
        "fallbacks": list(result.fallbacks),
    }


__all__ = [
    "InMemoryOwnerTruthAnswerFeedbackRepository",
    "OWNER_TRUTH_ANSWER_CITATION_READ_SCHEMA_VERSION",
    "OWNER_TRUTH_ANSWER_FEEDBACK_SCHEMA_VERSION",
    "OwnerTruthAnswerCitationReadNotFound",
    "OwnerTruthAnswerCitationReadResult",
    "OwnerTruthAnswerCitationReadService",
    "OwnerTruthAnswerCitationReadUnavailable",
    "OwnerTruthAnswerFeedbackCommand",
    "OwnerTruthAnswerFeedbackConflict",
    "OwnerTruthAnswerFeedbackError",
    "OwnerTruthAnswerFeedbackNotFound",
    "OwnerTruthAnswerFeedbackResult",
    "OwnerTruthAnswerFeedbackService",
    "OwnerTruthAnswerFeedbackUnavailable",
    "PostgresOwnerTruthAnswerFeedbackRepository",
    "answer_citation_read_summary",
    "answer_feedback_summary",
]

"""QA-only Owner confirmation receipts for M0-B knowledge dimensions.

This boundary is deliberately separate from Candidate decisions and MemoryVersion
corrections.  A receipt contains no memory text, no generated label, and no
provider output.  It only binds an Owner's explicit dimension/facet selection
to one *current* MemoryVersion content hash.  The MemoryVersion remains
immutable; a later replacement automatically stops matching this receipt.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, ContextManager, Iterable, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.knowledge_recommendations import (
    KnowledgeDimension,
    knowledge_dimension_facets,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION = (
    "owner-truth-knowledge-dimension-confirmation-v1"
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION = (
    "knowledge-dimension-review-v1"
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD = "ownerExplicitSelection"

_CONFIRMATION_NAMESPACE = UUID("349d38c4-0646-4296-980f-c01a2b168fd6")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class OwnerTruthKnowledgeDimensionConfirmationError(OwnerTruthMemoryProjectionError):
    """A knowledge-dimension confirmation cannot be persisted safely."""


class OwnerTruthKnowledgeDimensionConfirmationAccessDenied(
    OwnerTruthKnowledgeDimensionConfirmationError,
    OwnerTruthMemoryProjectionAccessDenied,
):
    """The caller is not the active Vault Owner."""


class OwnerTruthKnowledgeDimensionConfirmationConflict(
    OwnerTruthKnowledgeDimensionConfirmationError,
):
    """A command or dimension receipt conflicts with immutable history."""


class OwnerTruthKnowledgeDimensionConfirmationStaleMemory(
    OwnerTruthKnowledgeDimensionConfirmationConflict,
):
    """The selected MemoryVersion is no longer the current verified version."""


class OwnerTruthKnowledgeDimensionConfirmationUnavailable(
    OwnerTruthKnowledgeDimensionConfirmationError,
):
    """The default-off QA lane or its projection checkpoint is unavailable."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthKnowledgeDimensionConfirmationError(
            "knowledge dimension confirmation values must be JSON serializable"
        ) from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerTruthKnowledgeDimensionConfirmationError(f"{field} must be text")
    normalized = value.strip()
    if not normalized:
        raise OwnerTruthKnowledgeDimensionConfirmationError(f"{field} must be nonblank")
    return normalized


def _opaque_identifier(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthKnowledgeDimensionConfirmationError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _uuid(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthKnowledgeDimensionConfirmationError(f"{field} must be a UUID") from exc


def _hash(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthKnowledgeDimensionConfirmationError(f"{field} must be a sha256 digest")
    return normalized


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthKnowledgeDimensionConfirmationError(f"{field} must be a non-negative integer")
    return value


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthKnowledgeDimensionConfirmationError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthKnowledgeDimensionConfirmationAccessDenied(
            "only the Vault Owner may confirm a knowledge dimension"
        )


def _normalize_dimension_and_facets(
    *,
    dimension: KnowledgeDimension | str,
    covered_facets: Iterable[object],
) -> tuple[KnowledgeDimension, tuple[str, ...]]:
    try:
        normalized_dimension = KnowledgeDimension(dimension)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthKnowledgeDimensionConfirmationError("dimension is not supported") from exc
    values = tuple(str(item or "").strip() for item in covered_facets)
    if not values:
        raise OwnerTruthKnowledgeDimensionConfirmationError("covered_facets must not be empty")
    if len(values) != len(set(values)):
        raise OwnerTruthKnowledgeDimensionConfirmationError("covered_facets must not contain duplicates")
    permitted = knowledge_dimension_facets(normalized_dimension)
    if any(item not in permitted for item in values):
        raise OwnerTruthKnowledgeDimensionConfirmationError(
            "covered_facets contain unsupported values"
        )
    # Persist stable policy order rather than caller order, so retries and
    # otherwise equivalent selection payloads have one canonical hash.
    return normalized_dimension, tuple(item for item in permitted if item in values)


@dataclass(frozen=True)
class OwnerTruthKnowledgeDimensionConfirmationCommand:
    """One Owner's explicit, idempotent dimension/facet selection."""

    command_id: str
    expected_content_hash: str
    dimension: KnowledgeDimension | str
    covered_facets: tuple[str, ...]
    confirmation_method: str = OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD
    ui_schema_version: str = OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _opaque_identifier(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "expected_content_hash",
            _hash(self.expected_content_hash, field="expected_content_hash"),
        )
        dimension, facets = _normalize_dimension_and_facets(
            dimension=self.dimension,
            covered_facets=self.covered_facets,
        )
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "covered_facets", facets)
        if self.confirmation_method != OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD:
            raise OwnerTruthKnowledgeDimensionConfirmationError(
                "confirmation_method must be ownerExplicitSelection"
            )
        if self.ui_schema_version != OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION:
            raise OwnerTruthKnowledgeDimensionConfirmationError(
                "ui_schema_version is not supported"
            )

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "schemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION,
                "expectedContentHash": self.expected_content_hash,
                "dimension": self.dimension.value,
                "coveredFacets": list(self.covered_facets),
                "confirmationMethod": self.confirmation_method,
                "uiSchemaVersion": self.ui_schema_version,
            }
        )


@dataclass(frozen=True)
class OwnerTruthKnowledgeDimensionConfirmationResult:
    outcome: str
    confirmation_id: str
    command_id_hash: str
    memory_id: str
    memory_version_id: str
    bound_content_hash: str
    dimension: KnowledgeDimension
    covered_facets: tuple[str, ...]
    authority_epoch: int


class OwnerTruthKnowledgeDimensionConfirmationStore(Protocol):
    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_knowledge_dimension_confirmation_repository(self) -> Any:
        ...


def _record_from_projection(
    *,
    context: OwnerTruthCommandContext,
    command: OwnerTruthKnowledgeDimensionConfirmationCommand,
    memory_version_id: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a confirmation command to one ready, current projection entry.

    This function intentionally never examines entry content.  The relevant
    content hash and authority metadata come from the existing typed
    projection/citation boundary only.
    """

    requested_version_id = _uuid(memory_version_id, field="memory_version_id")
    if not isinstance(snapshot, Mapping):
        raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
            "memory projection snapshot is unavailable"
        )
    if (
        str(snapshot.get("state") or "") != "ready"
        or str(snapshot.get("vaultId") or "") != context.vault_id
        or str(snapshot.get("ownerSubjectId") or "") != context.owner_subject_id
        or not str(snapshot.get("checkpoint") or "").strip()
    ):
        raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
            "memory projection checkpoint is not ready for this Owner Vault"
        )
    authority_epoch = _nonnegative_int(snapshot.get("authorityEpoch"), field="authorityEpoch")
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
            "memory projection entries are unavailable"
        )

    matches: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
                "memory projection contains an invalid entry"
            )
        citation = entry.get("citation")
        if not isinstance(citation, Mapping):
            raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
                "memory projection entry is missing a typed citation"
            )
        if str(citation.get("memoryVersionId") or "") == requested_version_id:
            matches.append(entry)
    if len(matches) != 1:
        raise OwnerTruthKnowledgeDimensionConfirmationStaleMemory(
            "selected MemoryVersion is not current in the projection"
        )

    entry = matches[0]
    citation = entry.get("citation")
    assert isinstance(citation, Mapping)  # narrowed above
    memory_id = _uuid(citation.get("memoryId"), field="citation.memoryId")
    bound_content_hash = _hash(citation.get("contentHash"), field="citation.contentHash")
    if bound_content_hash != command.expected_content_hash:
        raise OwnerTruthKnowledgeDimensionConfirmationStaleMemory(
            "expected_content_hash does not match the current MemoryVersion"
        )
    if (
        str(entry.get("visibility") or "") != "owner"
        or str(entry.get("memoryKind") or "") != "knowledge"
        or str(entry.get("sensitivity") or "") != "standard"
        or str(entry.get("perspectiveType") or "") == "inferred"
        or str(entry.get("epistemicStatus") or "") == "inferred"
    ):
        raise OwnerTruthKnowledgeDimensionConfirmationStaleMemory(
            "selected MemoryVersion is not eligible for knowledge dimension confirmation"
        )

    return {
        "confirmationId": str(
            uuid5(_CONFIRMATION_NAMESPACE, f"{context.vault_id}:{command.command_id_hash}")
        ),
        "vaultId": context.vault_id,
        "ownerSubjectId": context.owner_subject_id,
        "actorSubjectId": context.actor_subject_id,
        "authorityEpoch": authority_epoch,
        "memoryId": memory_id,
        "memoryVersionId": requested_version_id,
        "boundContentHash": bound_content_hash,
        "dimension": command.dimension.value,
        "coveredFacets": list(command.covered_facets),
        "confirmationMethod": command.confirmation_method,
        "commandIdHash": command.command_id_hash,
        "payloadHash": command.payload_hash,
        "schemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION,
        "uiSchemaVersion": command.ui_schema_version,
    }


def _result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthKnowledgeDimensionConfirmationResult:
    dimension, facets = _normalize_dimension_and_facets(
        dimension=record.get("dimension"),
        covered_facets=tuple(record.get("coveredFacets") or ()),
    )
    return OwnerTruthKnowledgeDimensionConfirmationResult(
        outcome=outcome,
        confirmation_id=_uuid(record.get("confirmationId"), field="confirmationId"),
        command_id_hash=_hash(record.get("commandIdHash"), field="commandIdHash"),
        memory_id=_uuid(record.get("memoryId"), field="memoryId"),
        memory_version_id=_uuid(record.get("memoryVersionId"), field="memoryVersionId"),
        bound_content_hash=_hash(record.get("boundContentHash"), field="boundContentHash"),
        dimension=dimension,
        covered_facets=facets,
        authority_epoch=_nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
    )


class InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository:
    """Semantic double for immutable, current-version confirmation receipts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._records_by_dimension: dict[tuple[str, str, str], dict[str, Any]] = {}

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeDimensionConfirmationResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        if (
            str(normalized.get("vaultId") or "") != context.vault_id
            or str(normalized.get("ownerSubjectId") or "") != context.owner_subject_id
            or str(normalized.get("actorSubjectId") or "") != context.owner_subject_id
        ):
            raise OwnerTruthKnowledgeDimensionConfirmationAccessDenied(
                "confirmation receipt does not match Owner context"
            )
        _result_from_record(normalized, outcome="created")
        command_key = (context.vault_id, str(normalized["commandIdHash"]))
        dimension_key = (
            context.vault_id,
            str(normalized["memoryVersionId"]),
            str(normalized["dimension"]),
        )
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is not None:
                if str(existing.get("payloadHash") or "") != str(normalized.get("payloadHash") or ""):
                    raise OwnerTruthKnowledgeDimensionConfirmationConflict(
                        "commandId cannot be reused with different confirmation meaning"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            if dimension_key in self._records_by_dimension:
                raise OwnerTruthKnowledgeDimensionConfirmationConflict(
                    "MemoryVersion dimension already has an immutable confirmation receipt"
                )
            self._records_by_command[command_key] = normalized
            self._records_by_dimension[dimension_key] = normalized
            return _result_from_record(normalized, outcome="created")

    def list_for_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_version_ids: Iterable[str],
    ) -> tuple[dict[str, Any], ...]:
        _assert_owner_context(context)
        version_ids = {_uuid(item, field="memory_version_id") for item in memory_version_ids}
        with self._lock:
            rows = [
                deepcopy(record)
                for record in self._records_by_command.values()
                if str(record.get("vaultId") or "") == context.vault_id
                and str(record.get("ownerSubjectId") or "") == context.owner_subject_id
                and str(record.get("memoryVersionId") or "") in version_ids
            ]
        return tuple(
            sorted(
                rows,
                key=lambda item: (
                    str(item.get("memoryVersionId") or ""),
                    str(item.get("dimension") or ""),
                    str(item.get("confirmationId") or ""),
                ),
            )
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"records": deepcopy(list(self._records_by_command.values()))}


class PostgresOwnerTruthKnowledgeDimensionConfirmationRepository:
    """Postgres receipt persistence bound to one request Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeDimensionConfirmationResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-knowledge-dimension-command:"
                    f"{context.vault_id}:{normalized['commandIdHash']}",
                ),
            )
            cursor.execute(
                """
                SELECT id, command_id_hash, command_payload_hash, memory_id,
                    memory_version_id, bound_content_hash, authority_epoch,
                    dimension, covered_facets
                FROM owner_truth.knowledge_dimension_confirmation_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, normalized["commandIdHash"]),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != str(normalized["payloadHash"]):
                    raise OwnerTruthKnowledgeDimensionConfirmationConflict(
                        "commandId cannot be reused with different confirmation meaning"
                    )
                return self._result_from_row(existing, outcome="deduplicated")

            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-knowledge-dimension-target:"
                    f"{context.vault_id}:{normalized['memoryVersionId']}:{normalized['dimension']}",
                ),
            )
            self._assert_current_eligible_memory(cursor, context=context, record=normalized)
            cursor.execute(
                """
                SELECT id
                FROM owner_truth.knowledge_dimension_confirmation_receipts
                WHERE vault_id = %s
                  AND memory_version_id = %s
                  AND dimension = %s
                FOR UPDATE
                """,
                (
                    context.vault_id,
                    normalized["memoryVersionId"],
                    normalized["dimension"],
                ),
            )
            if cursor.fetchone() is not None:
                raise OwnerTruthKnowledgeDimensionConfirmationConflict(
                    "MemoryVersion dimension already has an immutable confirmation receipt"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.knowledge_dimension_confirmation_receipts (
                    id, vault_id, memory_id, memory_version_id, bound_content_hash,
                    owner_subject_id, actor_subject_id, authority_epoch,
                    dimension, covered_facets, confirmation_method,
                    command_id_hash, command_payload_hash, schema_version,
                    ui_schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                self._adapt_params(
                    (
                        normalized["confirmationId"],
                        normalized["vaultId"],
                        normalized["memoryId"],
                        normalized["memoryVersionId"],
                        normalized["boundContentHash"],
                        normalized["ownerSubjectId"],
                        normalized["actorSubjectId"],
                        normalized["authorityEpoch"],
                        normalized["dimension"],
                        normalized["coveredFacets"],
                        normalized["confirmationMethod"],
                        normalized["commandIdHash"],
                        normalized["payloadHash"],
                        normalized["schemaVersion"],
                        normalized["uiSchemaVersion"],
                    )
                ),
            )
            return _result_from_record(normalized, outcome="created")

    def list_for_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_version_ids: Iterable[str],
    ) -> tuple[dict[str, Any], ...]:
        _assert_owner_context(context)
        version_ids = tuple(sorted({_uuid(item, field="memory_version_id") for item in memory_version_ids}))
        if not version_ids:
            return ()
        placeholders = ", ".join("%s" for _ in version_ids)
        with self._cursor() as cursor:
            self._assert_active_vault(cursor, context=context, authority_epoch=None)
            cursor.execute(
                f"""
                SELECT id, command_id_hash, command_payload_hash, memory_id,
                    memory_version_id, bound_content_hash, owner_subject_id,
                    actor_subject_id, authority_epoch, dimension, covered_facets,
                    confirmation_method, schema_version, ui_schema_version
                FROM owner_truth.knowledge_dimension_confirmation_receipts
                WHERE vault_id = %s
                  AND owner_subject_id = %s
                  AND memory_version_id IN ({placeholders})
                ORDER BY memory_version_id ASC, dimension ASC, id ASC
                """,
                (context.vault_id, context.owner_subject_id, *version_ids),
            )
            rows = cursor.fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def _assert_current_eligible_memory(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> None:
        self._assert_active_vault(
            cursor,
            context=context,
            authority_epoch=_nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
        )
        cursor.execute(
            """
            SELECT memory.id AS memory_id, memory.owner_subject_id,
                memory.memory_kind, memory.perspective_type, memory.epistemic_status,
                memory.sensitivity, memory.status, memory.authority_epoch,
                version.memory_id AS version_memory_id, version.is_current,
                version.content_hash
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id
             AND version.memory_id = memory.id
            WHERE memory.vault_id = %s AND version.id = %s
            FOR SHARE
            """,
            (context.vault_id, record["memoryVersionId"]),
        )
        row = cursor.fetchone()
        if (
            row is None
            or str(row["memory_id"]) != str(record["memoryId"])
            or str(row["version_memory_id"]) != str(record["memoryId"])
            or str(row["owner_subject_id"]) != context.owner_subject_id
            or str(row["memory_kind"]) != "knowledge"
            or str(row["sensitivity"]) != "standard"
            or str(row["perspective_type"]) == "inferred"
            or str(row["epistemic_status"]) == "inferred"
            or str(row["status"]) != "active"
            or int(row["authority_epoch"]) != int(record["authorityEpoch"])
            or row["is_current"] is not True
            or str(row["content_hash"]) != str(record["boundContentHash"])
        ):
            raise OwnerTruthKnowledgeDimensionConfirmationStaleMemory(
                "selected MemoryVersion is no longer current and eligible"
            )

    @staticmethod
    def _assert_active_vault(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int | None,
    ) -> None:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            FOR SHARE
            """,
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != context.owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthKnowledgeDimensionConfirmationAccessDenied(
                "Vault is not active for this Owner"
            )
        if authority_epoch is not None and int(vault["authority_epoch"]) != authority_epoch:
            raise OwnerTruthKnowledgeDimensionConfirmationStaleMemory(
                "confirmation authority epoch is stale"
            )

    def _result_from_row(
        self,
        row: Mapping[str, Any],
        *,
        outcome: str,
    ) -> OwnerTruthKnowledgeDimensionConfirmationResult:
        return _result_from_record(self._row_to_record(row), outcome=outcome)

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> dict[str, Any]:
        facets = row["covered_facets"]
        if isinstance(facets, str):
            facets = json.loads(facets)
        return {
            "confirmationId": str(row["id"]),
            "commandIdHash": str(row["command_id_hash"]),
            "payloadHash": str(row["command_payload_hash"]),
            "memoryId": str(row["memory_id"]),
            "memoryVersionId": str(row["memory_version_id"]),
            "boundContentHash": str(row["bound_content_hash"]),
            "ownerSubjectId": str(row.get("owner_subject_id") or ""),
            "actorSubjectId": str(row.get("actor_subject_id") or ""),
            "authorityEpoch": int(row["authority_epoch"]),
            "dimension": str(row["dimension"]),
            "coveredFacets": list(facets or []),
            "confirmationMethod": str(row.get("confirmation_method") or ""),
            "schemaVersion": str(row.get("schema_version") or ""),
            "uiSchemaVersion": str(row.get("ui_schema_version") or ""),
        }

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (Mapping, list, tuple))
                else value
                for value in values
            )
        return tuple(Jsonb(value) if isinstance(value, (Mapping, list, tuple)) else value for value in values)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthKnowledgeDimensionConfirmationService:
    """Default-off service that records an Owner's explicit classification."""

    def __init__(
        self,
        store: OwnerTruthKnowledgeDimensionConfirmationStore,
        *,
        enabled: bool = False,
    ) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def confirm(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_version_id: str,
        command: OwnerTruthKnowledgeDimensionConfirmationCommand,
    ) -> OwnerTruthKnowledgeDimensionConfirmationResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
                "knowledge dimension confirmation QA contract is disabled"
            )
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-knowledge-dimension-confirmation-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            try:
                snapshot = self._store.owner_truth_memory_projection_repository().read(
                    context=context
                )
            except OwnerTruthMemoryProjectionAccessDenied as error:
                raise OwnerTruthKnowledgeDimensionConfirmationAccessDenied(str(error)) from error
            except OwnerTruthMemoryProjectionError as error:
                raise OwnerTruthKnowledgeDimensionConfirmationUnavailable(
                    "memory projection is unavailable for confirmation"
                ) from error
            record = _record_from_projection(
                context=context,
                command=command,
                memory_version_id=memory_version_id,
                snapshot=snapshot,
            )
            return self._store.owner_truth_knowledge_dimension_confirmation_repository().record(
                context=context,
                record=record,
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


def confirmation_summary(
    result: OwnerTruthKnowledgeDimensionConfirmationResult,
) -> dict[str, Any]:
    """Return only the value-free receipt fields safe for a QA response."""

    if not isinstance(result, OwnerTruthKnowledgeDimensionConfirmationResult):
        raise OwnerTruthKnowledgeDimensionConfirmationError("confirmation result is required")
    return {
        "schemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION,
        "status": result.outcome,
        "confirmationId": result.confirmation_id,
        "memoryId": result.memory_id,
        "memoryVersionId": result.memory_version_id,
        "boundContentHash": result.bound_content_hash,
        "dimension": result.dimension.value,
        "coveredFacets": list(result.covered_facets),
        "authorityEpoch": result.authority_epoch,
    }


__all__ = [
    "InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION",
    "OwnerTruthKnowledgeDimensionConfirmationAccessDenied",
    "OwnerTruthKnowledgeDimensionConfirmationCommand",
    "OwnerTruthKnowledgeDimensionConfirmationConflict",
    "OwnerTruthKnowledgeDimensionConfirmationError",
    "OwnerTruthKnowledgeDimensionConfirmationResult",
    "OwnerTruthKnowledgeDimensionConfirmationService",
    "OwnerTruthKnowledgeDimensionConfirmationStaleMemory",
    "OwnerTruthKnowledgeDimensionConfirmationUnavailable",
    "PostgresOwnerTruthKnowledgeDimensionConfirmationRepository",
    "confirmation_summary",
]

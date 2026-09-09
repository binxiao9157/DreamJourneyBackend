"""Deterministic, reviewable changesets for confirmed Owner Truth memories.

This module deliberately sits *before* persistence.  A model may propose a
typed Candidate, but only this conservative comparer decides whether the
Candidate adds a new fact, enriches evidence for an existing one, changes a
time-bound fact, conflicts with it, or contains no cross-session personal
fact at all.  The result is therefore safe to show in review and safe for a
repository to apply atomically after compare-and-swap checks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

from .candidate_decisions import OwnerTruthCandidateSnapshot
from .contracts import MemoryKind, OwnerTruthContractError, require_nonblank, require_uuid
from .ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    canonicalize_memory_payload,
    enrich_memory_payload_v5,
    reextract_owner_corrected_memory_payload,
    validate_memory_payload,
)


OWNER_TRUTH_MEMORY_CHANGESET_SCHEMA_VERSION = "owner-truth-memory-changeset-v1"
OWNER_TRUTH_MEMORY_CHANGESET_PROPOSAL_SCHEMA_VERSION = (
    "owner-truth-memory-changeset-proposal-v1"
)
_CHANGESET_NAMESPACE = UUID("a2d76eb8-9aeb-4b08-920e-d424033ed05d")
_PROPOSAL_NAMESPACE = UUID("47a18232-ca03-4b99-b567-a9ce7a55b8cf")
_WHITESPACE = re.compile(r"\s+")
_PERSONAL_QUESTION = re.compile(
    r"(?:吗|么|呢|是不是|是否|能否|请问|告诉我|想知道|什么)$|[?？]$"
)
_TIME_DATE = re.compile(r"(?<!\d)(?P<year>\d{4})(?:-(?P<month>\d{1,2})(?:-(?P<day>\d{1,2}))?)?(?!\d)")
_UNKNOWN_TIME_VALUES = frozenset({"", "unknown", "未知", "不详", "none", "null"})
_STRENGTH_NEUTRALISER = re.compile(r"(?:最喜欢|最爱|最钟爱|非常喜欢|特别喜欢|很喜欢)")


class OwnerTruthMemoryChangeSetError(OwnerTruthContractError):
    """A candidate cannot safely be compared with the current fact set."""


class OwnerTruthMemoryChangeOperationKind(str, Enum):
    ADD = "add"
    ADD_EVIDENCE = "addEvidence"
    REFINE = "refine"
    TEMPORAL_CHANGE = "temporalChange"
    CORRECT = "correct"
    DISPUTE = "dispute"
    DUPLICATE = "duplicate"
    NO_PERSONAL_FACT = "noPersonalFact"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryChangeSetError("changeset values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _copy_mapping(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthMemoryChangeSetError(f"{field} must be an object")
    copied = json.loads(_canonical_json(dict(value)))
    if not isinstance(copied, dict):  # pragma: no cover - defensive JSON invariant
        raise OwnerTruthMemoryChangeSetError(f"{field} must be an object")
    return copied


def _json_copy(value: Any, *, field: str) -> Any:
    """Return a canonical JSON-safe copy for an Owner-visible proposal."""

    try:
        return json.loads(_canonical_json(value))
    except OwnerTruthMemoryChangeSetError as exc:
        raise OwnerTruthMemoryChangeSetError(f"{field} must be JSON serializable") from exc


def _normal_text(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip().casefold()


def _visible_text(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip()


def _entity_label(value: Any) -> str:
    if isinstance(value, Mapping):
        return _normal_text(value.get("label") or value.get("category"))
    return _normal_text(value)


def _source_ref_key(value: Mapping[str, Any]) -> tuple[str, int, str, str]:
    source_id = _visible_text(value.get("sourceId"))
    try:
        source_version = int(value.get("sourceVersion"))
    except (TypeError, ValueError):
        source_version = 0
    span = value.get("span") if isinstance(value.get("span"), Mapping) else {}
    return (
        source_id,
        source_version,
        _visible_text(span.get("start")),
        _visible_text(span.get("end")),
    )


def _evidence_keys(values: Iterable[Mapping[str, Any]]) -> frozenset[tuple[str, int, str, str]]:
    return frozenset(_source_ref_key(value) for value in values if isinstance(value, Mapping))


def _merge_evidence_refs(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge source spans deterministically without inflating evidence strength."""

    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            copied = _copy_mapping(item, field="evidence reference")
            merged.setdefault(_canonical_json(copied), copied)
    return [merged[key] for key in sorted(merged)]


def _provenance_evidence_refs(content: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    provenance = content.get("provenance")
    values = provenance.get("evidenceRefs") if isinstance(provenance, Mapping) else ()
    return values if isinstance(values, (list, tuple)) else ()


def _is_unspecified_semantic_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().casefold() in {"unknown", "未知"}
    if isinstance(value, (list, tuple, Mapping)):
        return not value
    return False


def merge_lossless_refinement_content(
    *,
    base_content: Mapping[str, Any],
    incoming_content: Mapping[str, Any],
    kind: MemoryKind,
) -> dict[str, Any]:
    """Materialize the exact non-destructive result used by ``refine``.

    The function is shared by proposal rendering and activation so the owner
    never reviews a best-effort approximation of the version that will be
    written.  Existing confirmed values win unless they were genuinely
    unspecified; lists are a stable union and provenance spans are deduped.
    """

    def merge(base: Any, incoming: Any) -> Any:
        if isinstance(base, Mapping) and isinstance(incoming, Mapping):
            result = {key: merge(base.get(key), value) for key, value in incoming.items()}
            for key, value in base.items():
                result.setdefault(key, _json_copy(value, field="base nested value"))
            return result
        if isinstance(base, list) and isinstance(incoming, list):
            result = _json_copy(base, field="base list")
            seen = {_canonical_json(value) for value in result}
            for value in incoming:
                key = _canonical_json(value)
                if key not in seen:
                    result.append(_json_copy(value, field="incoming list value"))
                    seen.add(key)
            return result
        if _is_unspecified_semantic_value(base) and not _is_unspecified_semantic_value(incoming):
            return _json_copy(incoming, field="incoming scalar")
        return _json_copy(base, field="base scalar")

    selected = merge(
        _copy_mapping(base_content, field="base content"),
        _copy_mapping(incoming_content, field="incoming content"),
    )
    if not isinstance(selected, Mapping):  # pragma: no cover - merge invariant
        raise OwnerTruthMemoryChangeSetError("refinement content must be an object")
    payload = dict(selected)
    provenance = payload.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    provenance["evidenceRefs"] = _merge_evidence_refs(
        _provenance_evidence_refs(base_content),
        _provenance_evidence_refs(incoming_content),
    )
    payload["provenance"] = provenance
    return canonicalize_memory_payload(
        kind=kind,
        payload=payload,
        schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
    )


def _typed_content(
    *,
    kind: MemoryKind,
    content: Mapping[str, Any],
    schema_version: str,
) -> dict[str, Any]:
    """Read old payloads safely while comparing only the V5 semantic envelope."""

    canonical = canonicalize_memory_payload(
        kind=kind,
        payload=content,
        schema_version=schema_version,
    )
    if schema_version == OWNER_TRUTH_SCHEMA_VERSION_V5:
        return canonical
    return enrich_memory_payload_v5(kind=kind, payload=canonical)


def _qualifiers(content: Mapping[str, Any]) -> Mapping[str, Any]:
    value = content.get("qualifiers")
    return value if isinstance(value, Mapping) else {}


def _time_key(content: Mapping[str, Any]) -> tuple[str, str, str, str]:
    raw = _qualifiers(content).get("validTime")
    value = raw if isinstance(raw, Mapping) else {}
    return (
        _normal_text(value.get("start")),
        _normal_text(value.get("end")),
        _normal_text(value.get("precision")),
        _normal_text(value.get("expression")),
    )


def _meaningful_time_key(content: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Return only an asserted time scope, never V5's default ``unknown``."""

    start, end, precision, expression = _time_key(content)
    if all(value in _UNKNOWN_TIME_VALUES for value in (start, end, expression)) and (
        precision in _UNKNOWN_TIME_VALUES
    ):
        return "", "", "", ""
    return start, end, precision, expression


def _parse_time_bound(value: str, *, upper: bool) -> date | None:
    match = _TIME_DATE.search(value)
    if match is None:
        return None
    year = int(match.group("year"))
    month_raw = match.group("month")
    day_raw = match.group("day")
    month = int(month_raw) if month_raw else (12 if upper else 1)
    if not 1 <= month <= 12:
        return None
    if day_raw:
        day = int(day_raw)
    elif upper:
        month_ends = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        day = month_ends[month - 1]
    else:
        day = 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _time_interval(content: Mapping[str, Any]) -> tuple[date | None, date | None, bool]:
    """Parse an asserted interval conservatively for conflict classification."""

    start, end, _precision, expression = _meaningful_time_key(content)
    if not any((start, end, expression)):
        return None, None, False
    lower = _parse_time_bound(start or expression, upper=False)
    upper = _parse_time_bound(end or expression, upper=True)
    if lower is not None and upper is not None and upper < lower:
        return None, None, True
    return lower, upper, True


def _time_relation(
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
) -> str:
    """Classify only what we can prove: absent, overlap, disjoint or unknown."""

    candidate_start, candidate_end, candidate_asserted = _time_interval(candidate)
    target_start, target_end, target_asserted = _time_interval(target)
    if not candidate_asserted and not target_asserted:
        return "absent"
    if not candidate_asserted or not target_asserted:
        return "unknown"
    if None in {candidate_start, candidate_end, target_start, target_end}:
        return "unknown"
    if candidate_end < target_start or target_end < candidate_start:
        return "disjoint"
    return "overlap"


def _scope_key(content: Mapping[str, Any]) -> tuple[str, str, str, str]:
    qualifiers = _qualifiers(content)
    return (
        _normal_text(qualifiers.get("currentApplicability")),
        _entity_label(qualifiers.get("place")),
        _normal_text(qualifiers.get("scenario")),
        "|".join(_meaningful_time_key(content)),
    )


def _has_meaningful_scope(scope: tuple[str, str, str, str]) -> bool:
    """Avoid treating the V5 ``unknown`` default as a real time change."""

    current_applicability, place, scenario, valid_time = scope
    return bool(
        (current_applicability and current_applicability != "unknown")
        or place
        or scenario
        or valid_time
    )


def _assertion_key(content: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """A narrow comparable identity; it never uses opaque entity IDs."""

    fact_type = _normal_text(content.get("factType"))
    predicate = _normal_text(content.get("predicate"))
    object_label = _entity_label(content.get("object"))
    statement = _normal_text(content.get("statement"))
    # Some safely migrated V1–V4 facts have no object.  Statement is then the
    # least surprising stable comparison value; when an object exists it takes
    # precedence to tolerate harmless sentence rephrasing.
    value = object_label or statement
    subject = _normal_text(content.get("claimSubjectId") or content.get("memorySubjectId"))
    return subject, fact_type, predicate, value


def _polarity(content: Mapping[str, Any]) -> str:
    return _normal_text(_qualifiers(content).get("polarity")) or "unknown"


def _fact_shape_json(content: Mapping[str, Any]) -> str:
    """Compare the reviewed assertion without source-specific provenance.

    Provenance is evidence about an assertion, not a changed assertion.  It
    therefore must not turn a same fact from a second source into a content
    refinement; that case has its explicit ``addEvidence`` operation.
    """

    copied = _copy_mapping(content, field="typed fact")
    copied.pop("provenance", None)
    return _canonical_json(copied)


def _semantic_value_is_unspecified(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return _normal_text(value) in _UNKNOWN_TIME_VALUES
    if isinstance(value, (list, tuple, Mapping)):
        return not value
    return False


def _strength_neutral_statement(value: Any) -> str:
    return _STRENGTH_NEUTRALISER.sub("喜欢", _normal_text(value))


def _is_non_expansive_restatement(
    *,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
) -> bool:
    """Detect a weaker wording of the same fact without treating it as update.

    Extractors frequently omit a prior degree qualifier.  A later ordinary
    ``喜欢`` is evidence for the same preference, not an instruction to erase a
    reviewed ``最喜欢``.  This deliberately accepts only a narrow lexical
    normalisation; uncertain changes go through a visible dispute instead.
    """

    if _strength_neutral_statement(candidate.get("statement")) != _strength_neutral_statement(
        target.get("statement")
    ):
        return False
    candidate_qualifiers = _qualifiers(candidate)
    target_qualifiers = _qualifiers(target)
    target_superlative = target_qualifiers.get("superlativeAsserted") is True
    candidate_superlative = candidate_qualifiers.get("superlativeAsserted") is True
    return target_superlative and not candidate_superlative


def _is_lossless_refinement(
    *,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
) -> bool:
    """Allow only a strict additive enrichment of already-confirmed content.

    A ChangeSet is a write-time protection boundary.  If a proposed value
    replaces any non-empty reviewed value, it is not a refinement merely
    because its predicate/object happen to match.
    """

    changed = False

    def compare(candidate_value: Any, target_value: Any) -> bool:
        nonlocal changed
        if isinstance(target_value, Mapping):
            if not isinstance(candidate_value, Mapping):
                return _semantic_value_is_unspecified(candidate_value)
            for key, existing in target_value.items():
                proposed = candidate_value.get(key)
                if not compare(proposed, existing):
                    return False
            for key, proposed in candidate_value.items():
                if key not in target_value and not _semantic_value_is_unspecified(proposed):
                    changed = True
            return True
        if isinstance(target_value, (list, tuple)):
            if not isinstance(candidate_value, (list, tuple)):
                return _semantic_value_is_unspecified(candidate_value)
            target_json = {_canonical_json(item) for item in target_value}
            candidate_json = {_canonical_json(item) for item in candidate_value}
            if not target_json.issubset(candidate_json):
                return False
            if candidate_json != target_json:
                changed = True
            return True
        if _semantic_value_is_unspecified(target_value):
            if not _semantic_value_is_unspecified(candidate_value):
                changed = True
            return True
        # Schema defaults can represent omitted degree wording.  They must not
        # make a weaker later proposal overwrite an explicit superlative.
        if target_value is True and candidate_value is False:
            return True
        if _semantic_value_is_unspecified(candidate_value):
            return True
        return candidate_value == target_value

    target_value = _copy_mapping(target, field="target typed fact")
    candidate_value = _copy_mapping(candidate, field="candidate typed fact")
    for value in (target_value, candidate_value):
        value.pop("provenance", None)
        value.pop("semantic", None)
    if not compare(candidate_value, target_value):
        return False
    return changed


def _explicit_correction(candidate: OwnerTruthCandidateSnapshot) -> str | None:
    payload = candidate.payload
    for field in ("correctionOfMemoryVersionId", "targetMemoryVersionId"):
        value = _visible_text(payload.get(field))
        if value:
            return value
    review_mode = _normal_text(payload.get("reviewMode"))
    intent = _normal_text(candidate.content.get("correctionIntent"))
    if review_mode == "correction" or intent in {"correct", "replace", "supersede"}:
        return "explicit"
    return None


def _is_personal_question(content: Mapping[str, Any]) -> bool:
    statement = _visible_text(content.get("statement"))
    if not statement:
        return True
    return bool(_PERSONAL_QUESTION.search(statement))


@dataclass(frozen=True)
class OwnerTruthCurrentFormalMemory:
    """Current, confirmed record supplied by the repository inside its UoW."""

    memory_id: str
    memory_version_id: str
    vault_id: str
    owner_subject_id: str
    version_number: int
    memory_kind: MemoryKind
    content_schema_version: str
    content: Mapping[str, Any]
    evidence_refs: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", require_uuid(self.memory_id, field="memory_id"))
        object.__setattr__(
            self,
            "memory_version_id",
            require_uuid(self.memory_version_id, field="memory_version_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        if self.version_number < 1:
            raise OwnerTruthMemoryChangeSetError("current memory version_number must be positive")
        try:
            object.__setattr__(self, "memory_kind", MemoryKind(self.memory_kind))
        except ValueError as exc:
            raise OwnerTruthMemoryChangeSetError("current memory kind is unsupported") from exc
        object.__setattr__(
            self,
            "content_schema_version",
            require_nonblank(self.content_schema_version, field="content_schema_version"),
        )
        object.__setattr__(self, "content", _copy_mapping(self.content, field="content"))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(_copy_mapping(item, field="evidence_refs item") for item in self.evidence_refs),
        )

    @property
    def typed_content(self) -> dict[str, Any]:
        return _typed_content(
            kind=self.memory_kind,
            content=self.content,
            schema_version=self.content_schema_version,
        )


@dataclass(frozen=True)
class OwnerTruthMemoryChangeOperation:
    kind: OwnerTruthMemoryChangeOperationKind
    candidate_id: str
    target_memory_id: str | None
    target_memory_version_id: str | None
    target_memory_version: int | None
    reason: str
    candidate_assertion_key: tuple[str, str, str, str]
    changed_fields: tuple[str, ...] = ()
    added_evidence_count: int = 0

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", OwnerTruthMemoryChangeOperationKind(self.kind))
        except ValueError as exc:
            raise OwnerTruthMemoryChangeSetError("changeset operation is unsupported") from exc
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        if self.target_memory_id is None:
            if self.target_memory_version_id is not None or self.target_memory_version is not None:
                raise OwnerTruthMemoryChangeSetError("target version requires a target memory")
        else:
            object.__setattr__(
                self, "target_memory_id", require_uuid(self.target_memory_id, field="target_memory_id")
            )
            object.__setattr__(
                self,
                "target_memory_version_id",
                require_uuid(self.target_memory_version_id or "", field="target_memory_version_id"),
            )
            if not isinstance(self.target_memory_version, int) or self.target_memory_version < 1:
                raise OwnerTruthMemoryChangeSetError("target_memory_version must be positive")
        object.__setattr__(self, "reason", require_nonblank(self.reason, field="reason"))
        if len(self.candidate_assertion_key) != 4:
            raise OwnerTruthMemoryChangeSetError("candidate_assertion_key is malformed")
        if self.added_evidence_count < 0:
            raise OwnerTruthMemoryChangeSetError("added_evidence_count must not be negative")


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSet:
    change_set_id: str
    vault_id: str
    owner_subject_id: str
    candidate_id: str
    base_memory_revision: int
    operations: tuple[OwnerTruthMemoryChangeOperation, ...]
    dependencies: tuple[tuple[int, int], ...] = ()
    schema_version: str = OWNER_TRUTH_MEMORY_CHANGESET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "change_set_id", require_uuid(self.change_set_id, field="change_set_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id")
        )
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        if self.base_memory_revision < 0:
            raise OwnerTruthMemoryChangeSetError("base_memory_revision must not be negative")
        if not self.operations:
            raise OwnerTruthMemoryChangeSetError("changeset requires at least one operation")
        for operation in self.operations:
            if not isinstance(operation, OwnerTruthMemoryChangeOperation):
                raise OwnerTruthMemoryChangeSetError("changeset operation is malformed")
        normalized_dependencies: list[tuple[int, int]] = []
        seen_dependencies: set[tuple[int, int]] = set()
        for dependency in self.dependencies:
            if not isinstance(dependency, tuple) or len(dependency) != 2:
                raise OwnerTruthMemoryChangeSetError("changeset dependency is malformed")
            before, after = dependency
            if (
                not isinstance(before, int)
                or isinstance(before, bool)
                or not isinstance(after, int)
                or isinstance(after, bool)
                or before < 0
                or after < 0
                or before >= len(self.operations)
                or after >= len(self.operations)
                or before == after
            ):
                raise OwnerTruthMemoryChangeSetError("changeset dependency is out of range")
            if (before, after) in seen_dependencies:
                continue
            seen_dependencies.add((before, after))
            normalized_dependencies.append((before, after))
        object.__setattr__(self, "dependencies", tuple(sorted(normalized_dependencies)))
        if self.schema_version != OWNER_TRUTH_MEMORY_CHANGESET_SCHEMA_VERSION:
            raise OwnerTruthMemoryChangeSetError("changeset schema version is unsupported")

    @property
    def operation(self) -> OwnerTruthMemoryChangeOperation:
        if len(self.operations) != 1:
            raise OwnerTruthMemoryChangeSetError(
                "a multi-operation changeset must be applied as an atomic review group"
            )
        return self.operations[0]


def _operation_payload(
    operation: OwnerTruthMemoryChangeOperation,
    *,
    operation_index: int,
) -> dict[str, Any]:
    return {
        "operationIndex": operation_index,
        "operationKind": operation.kind.value,
        "candidateId": operation.candidate_id,
        "targetMemoryId": operation.target_memory_id,
        "targetMemoryVersionId": operation.target_memory_version_id,
        "targetMemoryVersion": operation.target_memory_version,
        "reason": operation.reason,
        "candidateAssertionKey": list(operation.candidate_assertion_key),
        "changedFields": list(operation.changed_fields),
        "addedEvidenceCount": operation.added_evidence_count,
    }


def _path_value(content: Mapping[str, Any] | None, path: str) -> Any:
    if content is None:
        return None
    if path == "content":
        return _json_copy(content, field="proposal content")
    value: Any = content
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return _json_copy(value, field="proposal field value")


def _proposal_operation_payload(
    operation: OwnerTruthMemoryChangeOperation,
    *,
    operation_index: int,
    candidate_content: Mapping[str, Any],
    candidate_evidence_refs: Iterable[Mapping[str, Any]],
    target: OwnerTruthCurrentFormalMemory | None,
) -> dict[str, Any]:
    """Render the exact fact transition an Owner is asked to confirm.

    It intentionally includes target/version metadata and fact values in the
    digest.  This is an owner-only preview, never a public projection.
    """

    payload = _operation_payload(operation, operation_index=operation_index)
    before_content = None if target is None else target.typed_content
    before_refs = () if target is None else target.evidence_refs
    if operation.kind in {
        OwnerTruthMemoryChangeOperationKind.ADD,
        OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE,
        OwnerTruthMemoryChangeOperationKind.DISPUTE,
        OwnerTruthMemoryChangeOperationKind.CORRECT,
    }:
        after_content: Mapping[str, Any] | None = candidate_content
    elif operation.kind is OwnerTruthMemoryChangeOperationKind.REFINE:
        if target is None:  # pragma: no cover - operation constructor guards this
            raise OwnerTruthMemoryChangeSetError("refinement proposal has no current target")
        after_content = merge_lossless_refinement_content(
            base_content=target.typed_content,
            incoming_content=candidate_content,
            kind=target.memory_kind,
        )
    else:
        after_content = before_content

    field_diffs = []
    for field in operation.changed_fields:
        if field == "evidenceRefs":
            continue
        field_diffs.append(
            {
                "path": field,
                "before": _path_value(before_content, field),
                "after": _path_value(after_content, field),
            }
        )
    payload["factDiff"] = {
        "before": None if before_content is None else _json_copy(before_content, field="before fact"),
        "after": None if after_content is None else _json_copy(after_content, field="after fact"),
        "candidate": _json_copy(candidate_content, field="candidate fact"),
        "changedFields": field_diffs,
        "evidence": {
            "beforeCount": len(_merge_evidence_refs(before_refs)),
            "candidateCount": len(_merge_evidence_refs(candidate_evidence_refs)),
            "afterCount": len(_merge_evidence_refs(before_refs, candidate_evidence_refs)),
            "addedCount": operation.added_evidence_count,
        },
    }
    payload["anticipatedActivation"] = {
        OwnerTruthMemoryChangeOperationKind.ADD: "create",
        OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE: "createRelated",
        OwnerTruthMemoryChangeOperationKind.DISPUTE: "createContradictory",
        OwnerTruthMemoryChangeOperationKind.ADD_EVIDENCE: "revise",
        OwnerTruthMemoryChangeOperationKind.REFINE: "revise",
        OwnerTruthMemoryChangeOperationKind.CORRECT: "revise",
        OwnerTruthMemoryChangeOperationKind.DUPLICATE: "noNewVersion",
        OwnerTruthMemoryChangeOperationKind.NO_PERSONAL_FACT: "notApplicable",
    }[operation.kind]
    return payload


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetProposal:
    """Immutable pre-review preview of a ChangeSet against one formal revision.

    A proposal is deliberately distinct from the post-review activation audit.
    The former tells an Owner what their decision *would* change; the latter
    proves what was applied after compare-and-swap succeeds.
    """

    proposal_id: str
    proposal_hash: str
    candidate_content_hash: str
    candidate_row_version: int
    change_set: OwnerTruthMemoryChangeSet
    rendered_operations: tuple[Mapping[str, Any], ...]
    schema_version: str = OWNER_TRUTH_MEMORY_CHANGESET_PROPOSAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", require_uuid(self.proposal_id, field="proposal_id"))
        for field in ("proposal_hash", "candidate_content_hash"):
            value = _visible_text(getattr(self, field)).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise OwnerTruthMemoryChangeSetError(f"{field} must be a SHA-256 digest")
            object.__setattr__(self, field, value)
        if (
            not isinstance(self.candidate_row_version, int)
            or isinstance(self.candidate_row_version, bool)
            or self.candidate_row_version < 1
        ):
            raise OwnerTruthMemoryChangeSetError("candidate_row_version must be positive")
        if not isinstance(self.change_set, OwnerTruthMemoryChangeSet):
            raise OwnerTruthMemoryChangeSetError("proposal requires a changeset")
        if len(self.rendered_operations) != len(self.change_set.operations):
            raise OwnerTruthMemoryChangeSetError(
                "proposal rendered operations must match the ChangeSet"
            )
        normalized_operations = []
        for index, operation in enumerate(self.rendered_operations):
            if not isinstance(operation, Mapping):
                raise OwnerTruthMemoryChangeSetError("proposal rendered operation is malformed")
            copied = _json_copy(operation, field="proposal rendered operation")
            if not isinstance(copied, dict) or copied.get("operationIndex") != index:
                raise OwnerTruthMemoryChangeSetError("proposal rendered operation index is invalid")
            normalized_operations.append(copied)
        object.__setattr__(self, "rendered_operations", tuple(normalized_operations))
        if self.schema_version != OWNER_TRUTH_MEMORY_CHANGESET_PROPOSAL_SCHEMA_VERSION:
            raise OwnerTruthMemoryChangeSetError("changeset proposal schema version is unsupported")

    def payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "proposalId": self.proposal_id,
            "proposalHash": self.proposal_hash,
            "candidateContentHash": self.candidate_content_hash,
            "candidateVersion": self.candidate_row_version,
            "changeSetId": self.change_set.change_set_id,
            "baseMemoryRevision": self.change_set.base_memory_revision,
            "operations": [dict(operation) for operation in self.rendered_operations],
            "dependencies": [
                {"beforeOperationIndex": before, "afterOperationIndex": after}
                for before, after in self.change_set.dependencies
            ],
        }


def _operation(
    *,
    kind: OwnerTruthMemoryChangeOperationKind,
    candidate: OwnerTruthCandidateSnapshot,
    candidate_content: Mapping[str, Any],
    reason: str,
    target: OwnerTruthCurrentFormalMemory | None = None,
    changed_fields: Iterable[str] = (),
    added_evidence_count: int = 0,
) -> OwnerTruthMemoryChangeOperation:
    return OwnerTruthMemoryChangeOperation(
        kind=kind,
        candidate_id=candidate.candidate_id,
        target_memory_id=None if target is None else target.memory_id,
        target_memory_version_id=None if target is None else target.memory_version_id,
        target_memory_version=None if target is None else target.version_number,
        reason=reason,
        candidate_assertion_key=_assertion_key(candidate_content),
        changed_fields=tuple(sorted(set(changed_fields))),
        added_evidence_count=added_evidence_count,
    )


def build_memory_changeset(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    current_memories: Iterable[OwnerTruthCurrentFormalMemory],
    base_memory_revision: int,
) -> OwnerTruthMemoryChangeSet:
    """Compare one reviewed candidate with current authoritative facts.

    The matcher is intentionally conservative: no textual similarity is
    enough to merge two people, two time periods, or two polarities.  A later
    retrieval layer may nominate candidates, but this function remains the
    write-time authority and returns a visible operation instead of silently
    applying a semantic guess.
    """

    if not isinstance(candidate, OwnerTruthCandidateSnapshot):
        raise OwnerTruthMemoryChangeSetError("candidate snapshot is required")
    if base_memory_revision < 0:
        raise OwnerTruthMemoryChangeSetError("base_memory_revision must not be negative")
    candidate_content = _typed_content(
        kind=candidate.memory_kind,
        content=candidate.content,
        schema_version=candidate.content_schema_version,
    )
    current = tuple(current_memories)
    for item in current:
        if not isinstance(item, OwnerTruthCurrentFormalMemory):
            raise OwnerTruthMemoryChangeSetError("current_memories must contain current formal memories")
        if item.vault_id != candidate.vault_id or item.owner_subject_id != candidate.owner_subject_id:
            raise OwnerTruthMemoryChangeSetError("current memory crosses candidate authority boundary")

    explicit_correction = _explicit_correction(candidate)
    candidate_key = _assertion_key(candidate_content)
    candidate_scope = _scope_key(candidate_content)
    candidate_polarity = _polarity(candidate_content)
    candidate_evidence = _evidence_keys(candidate.source_refs)
    typed_current = [(item, item.typed_content) for item in current]

    target_by_version = next(
        (
            item
            for item, _content in typed_current
            if explicit_correction
            and explicit_correction != "explicit"
            and item.memory_kind is candidate.memory_kind
            and item.memory_version_id == explicit_correction
        ),
        None,
    )
    if target_by_version is not None:
        operation = _operation(
            kind=OwnerTruthMemoryChangeOperationKind.CORRECT,
            candidate=candidate,
            candidate_content=candidate_content,
            target=target_by_version,
            reason="explicitCorrectionTarget",
            changed_fields=("content", "evidenceRefs"),
            added_evidence_count=len(candidate_evidence),
        )
    elif _is_personal_question(candidate_content):
        operation = _operation(
            kind=OwnerTruthMemoryChangeOperationKind.NO_PERSONAL_FACT,
            candidate=candidate,
            candidate_content=candidate_content,
            reason="questionOrEmptyStatement",
        )
    else:
        comparable = [
            (item, content)
            for item, content in typed_current
            if item.memory_kind is candidate.memory_kind
            and _assertion_key(content) == candidate_key
        ]
        # ``claimSubjectId`` must never be guessed. If the candidate omits it,
        # an existing explicitly different subject is not comparable.
        candidate_subject = candidate_key[0]
        comparable = [
            (item, content)
            for item, content in comparable
            if not candidate_subject or not _assertion_key(content)[0] or _assertion_key(content)[0] == candidate_subject
        ]
        if explicit_correction == "explicit" and comparable:
            target = comparable[0][0]
            operation = _operation(
                kind=OwnerTruthMemoryChangeOperationKind.CORRECT,
                candidate=candidate,
                candidate_content=candidate_content,
                target=target,
                reason="explicitCorrectionIntent",
                changed_fields=("content", "evidenceRefs"),
                added_evidence_count=len(candidate_evidence - _evidence_keys(target.evidence_refs)),
            )
        elif not comparable:
            operation = _operation(
                kind=OwnerTruthMemoryChangeOperationKind.ADD,
                candidate=candidate,
                candidate_content=candidate_content,
                reason="noComparableCurrentFact",
                added_evidence_count=len(candidate_evidence),
            )
        else:
            target, target_content = comparable[0]
            target_evidence = _evidence_keys(target.evidence_refs)
            new_evidence = candidate_evidence - target_evidence
            target_scope = _scope_key(target_content)
            target_polarity = _polarity(target_content)
            temporal_relation = _time_relation(candidate_content, target_content)
            scopes_differ = candidate_scope != target_scope
            scope_is_meaningful = (
                _has_meaningful_scope(candidate_scope)
                or _has_meaningful_scope(target_scope)
            )
            polarity_conflicts = (
                candidate_polarity != "unknown"
                and target_polarity != "unknown"
                and candidate_polarity != target_polarity
            )
            same_non_temporal_scope = candidate_scope[:3] == target_scope[:3]
            if polarity_conflicts and same_non_temporal_scope and temporal_relation != "disjoint":
                # Different calendar strings do not make a contradiction safe:
                # overlapping, open-ended, or unparseable periods require a
                # visible dispute/clarification instead of parallel facts.
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.DISPUTE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason=(
                        "polarityConflictsAtOverlappingTime"
                        if temporal_relation == "overlap"
                        else "polarityConflictsAtUncertainTime"
                    ),
                    changed_fields=("qualifiers.polarity", "qualifiers.validTime"),
                    added_evidence_count=len(new_evidence),
                )
            elif scopes_differ and scope_is_meaningful:
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason=(
                        "disjointTimeOrScenarioDiffers"
                        if temporal_relation == "disjoint"
                        else "timeOrScenarioDiffers"
                    ),
                    changed_fields=("qualifiers",),
                    added_evidence_count=len(new_evidence),
                )
            elif polarity_conflicts:
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.DISPUTE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="polarityConflictsAtSameScope",
                    changed_fields=("qualifiers.polarity",),
                    added_evidence_count=len(new_evidence),
                )
            elif not new_evidence and _fact_shape_json(candidate_content) == _fact_shape_json(target_content):
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.DUPLICATE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="sameAssertionAndEvidence",
                )
            elif new_evidence and _fact_shape_json(candidate_content) == _fact_shape_json(target_content):
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.ADD_EVIDENCE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="sameAssertionNewEvidence",
                    changed_fields=("evidenceRefs",),
                    added_evidence_count=len(new_evidence),
                )
            elif _is_non_expansive_restatement(
                candidate=candidate_content,
                target=target_content,
            ):
                operation = _operation(
                    kind=(
                        OwnerTruthMemoryChangeOperationKind.ADD_EVIDENCE
                        if new_evidence
                        else OwnerTruthMemoryChangeOperationKind.DUPLICATE
                    ),
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="weakerRestatementCannotEraseConfirmedQualifier",
                    changed_fields=("evidenceRefs",) if new_evidence else (),
                    added_evidence_count=len(new_evidence),
                )
            elif candidate_polarity == target_polarity and _is_lossless_refinement(
                candidate=candidate_content,
                target=target_content,
            ):
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.REFINE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="sameAssertionRicherReviewedDetail",
                    changed_fields=("content", "evidenceRefs"),
                    added_evidence_count=len(new_evidence),
                )
            elif candidate_polarity == target_polarity:
                # Same predicate is not enough to let a new extractor replace
                # a reviewed value.  The owner must see an explicit dispute or
                # create a correction rather than silently taking the last
                # model payload.
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.DISPUTE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="sameAssertionWouldReplaceConfirmedDetail",
                    changed_fields=("content",),
                    added_evidence_count=len(new_evidence),
                )
            else:
                operation = _operation(
                    kind=OwnerTruthMemoryChangeOperationKind.DISPUTE,
                    candidate=candidate,
                    candidate_content=candidate_content,
                    target=target,
                    reason="sameAssertionCannotSafelyMerge",
                    added_evidence_count=len(new_evidence),
                )

    change_set_id = str(
        uuid5(
            _CHANGESET_NAMESPACE,
            _canonical_json(
                {
                    "candidateId": candidate.candidate_id,
                    "candidateHash": candidate.content_hash,
                    "baseMemoryRevision": base_memory_revision,
                    "operation": operation.kind.value,
                    "targetMemoryVersionId": operation.target_memory_version_id,
                }
            ),
        )
    )
    return OwnerTruthMemoryChangeSet(
        change_set_id=change_set_id,
        vault_id=candidate.vault_id,
        owner_subject_id=candidate.owner_subject_id,
        candidate_id=candidate.candidate_id,
        base_memory_revision=base_memory_revision,
        operations=(operation,),
    )


def candidate_with_resolved_content(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    content: Mapping[str, Any] | None = None,
    content_schema_version: str | None = None,
) -> OwnerTruthCandidateSnapshot:
    """Build a non-persisted Candidate view for previewing an Owner correction.

    The processor-owned Candidate row is never changed.  This short-lived
    view is used only to compute the exact proposal the Owner will later bind
    to.  Its hash is the same canonical content hash that a correction receipt
    records, so a preview cannot be replayed for different text.
    """

    if not isinstance(candidate, OwnerTruthCandidateSnapshot):
        raise OwnerTruthMemoryChangeSetError("candidate snapshot is required")
    if content is None:
        return candidate
    schema_version = _visible_text(content_schema_version or candidate.content_schema_version)
    if not schema_version:
        raise OwnerTruthMemoryChangeSetError("resolved content schema version is required")
    if (
        schema_version == OWNER_TRUTH_SCHEMA_VERSION_V5
        and candidate.content_schema_version == OWNER_TRUTH_SCHEMA_VERSION_V5
    ):
        normalized_content = reextract_owner_corrected_memory_payload(
            kind=candidate.memory_kind,
            source_payload=candidate.content,
            corrected_payload=content,
        )
    else:
        normalized_content = canonicalize_memory_payload(
            kind=candidate.memory_kind,
            payload=content,
            schema_version=schema_version,
        )
    validation = validate_memory_payload(
        kind=candidate.memory_kind,
        payload=normalized_content,
        schema_version=schema_version,
    )
    if not validation.accepted:
        raise OwnerTruthMemoryChangeSetError(
            f"resolved candidate content is not admitted: {validation.code}"
        )
    payload = dict(candidate.payload)
    payload["content"] = normalized_content
    payload["contentSchemaVersion"] = schema_version
    return replace(
        candidate,
        content_hash=_digest(normalized_content),
        content_schema_version=schema_version,
        payload=payload,
    )


def build_memory_changeset_proposal(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    current_memories: Iterable[OwnerTruthCurrentFormalMemory],
    base_memory_revision: int,
    resolved_content: Mapping[str, Any] | None = None,
    resolved_content_schema_version: str | None = None,
) -> OwnerTruthMemoryChangeSetProposal:
    """Freeze an Owner-visible ChangeSet proposal before terminal review.

    ``base_memory_revision`` and target MemoryVersion IDs are deliberately in
    the digest.  A later review must either bind this exact proposal or fetch a
    replacement preview after another decision changes the formal memory set.
    """

    preview_candidate = candidate_with_resolved_content(
        candidate=candidate,
        content=resolved_content,
        content_schema_version=resolved_content_schema_version,
    )
    current = tuple(current_memories)
    change_set = build_memory_changeset(
        candidate=preview_candidate,
        current_memories=current,
        base_memory_revision=base_memory_revision,
    )
    current_by_version_id = {
        memory.memory_version_id: memory
        for memory in current
    }
    rendered_operations = tuple(
        _proposal_operation_payload(
            operation,
            operation_index=index,
            candidate_content=_typed_content(
                kind=preview_candidate.memory_kind,
                content=preview_candidate.content,
                schema_version=preview_candidate.content_schema_version,
            ),
            candidate_evidence_refs=preview_candidate.source_refs,
            target=current_by_version_id.get(operation.target_memory_version_id or ""),
        )
        for index, operation in enumerate(change_set.operations)
    )
    payload_without_hash = {
        "schemaVersion": OWNER_TRUTH_MEMORY_CHANGESET_PROPOSAL_SCHEMA_VERSION,
        "candidateId": preview_candidate.candidate_id,
        "candidateContentHash": preview_candidate.content_hash,
        "candidateVersion": preview_candidate.row_version,
        "changeSetId": change_set.change_set_id,
        "baseMemoryRevision": change_set.base_memory_revision,
        "operations": [dict(operation) for operation in rendered_operations],
        "dependencies": [
            {"beforeOperationIndex": before, "afterOperationIndex": after}
            for before, after in change_set.dependencies
        ],
    }
    proposal_hash = _digest(payload_without_hash)
    proposal_id = str(
        uuid5(
            _PROPOSAL_NAMESPACE,
            _canonical_json(
                {
                    "candidateId": preview_candidate.candidate_id,
                    "candidateContentHash": preview_candidate.content_hash,
                    "candidateVersion": preview_candidate.row_version,
                    "baseMemoryRevision": change_set.base_memory_revision,
                    "proposalHash": proposal_hash,
                }
            ),
        )
    )
    return OwnerTruthMemoryChangeSetProposal(
        proposal_id=proposal_id,
        proposal_hash=proposal_hash,
        candidate_content_hash=preview_candidate.content_hash,
        candidate_row_version=preview_candidate.row_version,
        change_set=change_set,
        rendered_operations=rendered_operations,
    )


__all__ = [
    "OWNER_TRUTH_MEMORY_CHANGESET_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_CHANGESET_PROPOSAL_SCHEMA_VERSION",
    "OwnerTruthCurrentFormalMemory",
    "OwnerTruthMemoryChangeOperation",
    "OwnerTruthMemoryChangeOperationKind",
    "OwnerTruthMemoryChangeSet",
    "OwnerTruthMemoryChangeSetError",
    "OwnerTruthMemoryChangeSetProposal",
    "build_memory_changeset",
    "build_memory_changeset_proposal",
    "candidate_with_resolved_content",
    "merge_lossless_refinement_content",
]

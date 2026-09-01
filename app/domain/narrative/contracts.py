"""Storage-independent contracts for the V1 narrative writing system.

These types freeze the wire vocabulary shared with iOS. They do not create a
second source of biographical truth: only confirmed Owner Truth MemoryVersion
records may populate a future MemorySnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


NARRATIVE_COMMAND_SCHEMA_VERSION = "narrative-command-v1"
NARRATIVE_ERROR_SCHEMA_VERSION = "narrative-error-v1"
NARRATIVE_FIXTURE_SCHEMA_VERSION = "narrative-contract-fixture-v1"


class NarrativeContractError(ValueError):
    """Raised when a value cannot participate in the narrative contract."""


class BookProjectType(str, Enum):
    SELF_AUTOBIOGRAPHY = "selfAutobiography"
    TA_STORY = "taStory"


class NarrativeNarratorType(str, Enum):
    SELF_FIRST_PERSON = "selfFirstPerson"
    THIRD_PERSON_BIOGRAPHY = "thirdPersonBiography"
    CONTROLLER_WITNESS = "controllerWitness"


class BookProjectState(str, Enum):
    NOT_STARTED = "notStarted"
    CHECKING_READINESS = "checkingReadiness"
    NEEDS_MORE_MEMORY = "needsMoreMemory"
    READY_FOR_CONFIRMATION = "readyForConfirmation"
    GENERATING_AUDITIONS = "generatingAuditions"
    AUDITIONS_READY = "auditionsReady"
    GENERATING_GOLDEN_SAMPLE = "generatingGoldenSample"
    GOLDEN_SAMPLE_REVIEW = "goldenSampleReview"
    TONE_CONFIRMED = "toneConfirmed"
    OUTLINE_REVIEW = "outlineReview"
    WRITING = "writing"
    UPDATE_AVAILABLE = "updateAvailable"
    PAUSED = "paused"
    DISPUTED = "disputed"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"
    DELETED = "deleted"


class NarrativeArtifactType(str, Enum):
    WRITING_AUDITION = "writingAudition"
    GOLDEN_SAMPLE = "goldenSample"
    NARRATIVE_STYLE_PROFILE = "narrativeStyleProfile"
    WRITING_CONSTITUTION = "writingConstitution"
    OUTLINE = "outline"
    CHAPTER = "chapter"


class NarrativeArtifactState(str, Enum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "readyForReview"
    CONFIRMED = "confirmed"
    FINAL = "final"
    STALE = "stale"
    SUPERSEDED = "superseded"


class NarrativeCommandType(str, Enum):
    CONFIRM_SETUP = "confirmSetup"
    GENERATE_AUDITIONS = "generateAuditions"
    SELECT_AUDITION = "selectAudition"
    GENERATE_GOLDEN_SAMPLE = "generateGoldenSample"
    SUBMIT_ARTIFACT_FEEDBACK = "submitArtifactFeedback"
    CONFIRM_GOLDEN_SAMPLE = "confirmGoldenSample"
    GENERATE_OUTLINE = "generateOutline"
    REVISE_OUTLINE = "reviseOutline"
    CONFIRM_OUTLINE = "confirmOutline"
    GENERATE_CHAPTER = "generateChapter"
    REVISE_CHAPTER = "reviseChapter"
    FINALIZE_CHAPTER = "finalizeChapter"
    EDIT_ARTIFACT = "editArtifact"
    RESTORE_ARTIFACT_VERSION = "restoreArtifactVersion"
    ADOPT_MEMORY_UPDATE = "adoptMemoryUpdate"
    IGNORE_MEMORY_UPDATE = "ignoreMemoryUpdate"
    PAUSE_PROJECT = "pauseProject"
    RESUME_PROJECT = "resumeProject"
    ARCHIVE_PROJECT = "archiveProject"


class NarrativeJobState(str, Enum):
    QUEUED = "queued"
    SNAPSHOTTING = "snapshotting"
    RETRIEVING = "retrieving"
    PLANNING = "planning"
    DRAFTING = "drafting"
    VALIDATING_FACTS = "validatingFacts"
    EDITING_STYLE = "editingStyle"
    FINAL_VALIDATION = "finalValidation"
    READY_FOR_REVIEW = "readyForReview"
    NEEDS_ECHO = "needsEcho"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class NarrativeErrorCode(str, Enum):
    JOB_ACCEPTED = "job_accepted"
    RELEASE_POLICY_DENIED = "release_policy_denied"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    RESOURCE_NOT_FOUND = "resource_not_found"
    PROJECT_VERSION_CONFLICT = "project_version_conflict"
    INVALID_STATE = "invalid_state"
    MEMORY_SNAPSHOT_STALE = "memory_snapshot_stale"
    CONTRACT_INVALID = "contract_invalid"
    UNSUPPORTED_FACT_DETECTED = "unsupported_fact_detected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


def require_nonblank(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise NarrativeContractError(f"{field} must be non-empty")
    return normalized


def require_uuid(value: str, *, field: str) -> str:
    normalized = require_nonblank(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise NarrativeContractError(f"{field} must be a UUID") from exc


def require_timestamp(value: str, *, field: str) -> str:
    normalized = require_nonblank(value, field=field)
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NarrativeContractError(f"{field} must be an ISO-8601 timestamp") from exc
    return normalized


@dataclass(frozen=True)
class NarrativeCommandEnvelope:
    command_id: str
    command_type: NarrativeCommandType
    expected_project_version: int
    confirmed: bool
    payload: Mapping[str, Any]
    schema_version: str = NARRATIVE_COMMAND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_uuid(self.command_id, field="command_id"))
        if self.schema_version != NARRATIVE_COMMAND_SCHEMA_VERSION:
            raise NarrativeContractError("unsupported narrative command schema_version")
        if self.expected_project_version < 0:
            raise NarrativeContractError("expected_project_version must not be negative")
        if not isinstance(self.confirmed, bool):
            raise NarrativeContractError("confirmed must be a boolean")
        if not isinstance(self.payload, Mapping):
            raise NarrativeContractError("payload must be an object")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NarrativeCommandEnvelope":
        try:
            return cls(
                schema_version=value["schemaVersion"],
                command_id=value["commandId"],
                command_type=NarrativeCommandType(value["commandType"]),
                expected_project_version=value["expectedProjectVersion"],
                confirmed=value["confirmed"],
                payload=value["payload"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, NarrativeContractError):
                raise
            raise NarrativeContractError("invalid narrative command envelope") from exc


@dataclass(frozen=True)
class NarrativeErrorEnvelope:
    error_code: NarrativeErrorCode
    message: str
    retryable: bool
    current_project_version: int | None = None
    schema_version: str = NARRATIVE_ERROR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NARRATIVE_ERROR_SCHEMA_VERSION:
            raise NarrativeContractError("unsupported narrative error schema_version")
        object.__setattr__(self, "message", require_nonblank(self.message, field="message"))
        if not isinstance(self.retryable, bool):
            raise NarrativeContractError("retryable must be a boolean")
        if self.current_project_version is not None and self.current_project_version < 0:
            raise NarrativeContractError("current_project_version must not be negative")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NarrativeErrorEnvelope":
        try:
            return cls(
                schema_version=value["schemaVersion"],
                error_code=NarrativeErrorCode(value["errorCode"]),
                message=value["message"],
                retryable=value["retryable"],
                current_project_version=value.get("currentProjectVersion"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, NarrativeContractError):
                raise
            raise NarrativeContractError("invalid narrative error envelope") from exc


@dataclass(frozen=True)
class NarrativeScope:
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    subject_persona_id: str
    authority_epoch: int = 0
    authorization_capture: Any | None = None

    def __post_init__(self) -> None:
        for field in ("vault_id", "owner_subject_id", "actor_subject_id", "subject_persona_id"):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))
        if self.authority_epoch < 0:
            raise NarrativeContractError("authority_epoch must not be negative")


@dataclass(frozen=True)
class NarrativeMemoryRef:
    memory_id: str
    memory_version_id: str
    content_hash: str
    content: Mapping[str, Any]
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", require_uuid(self.memory_id, field="memory_id"))
        object.__setattr__(
            self,
            "memory_version_id",
            require_uuid(self.memory_version_id, field="memory_version_id"),
        )
        if len(self.content_hash) != 64:
            raise NarrativeContractError("content_hash must be SHA-256")
        if not isinstance(self.content, Mapping):
            raise NarrativeContractError("content must be an object")

    def public_contract(self, *, include_content: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "memoryId": self.memory_id,
            "memoryVersionId": self.memory_version_id,
            "contentHash": self.content_hash,
            "memoryKind": self.memory_kind,
            "perspectiveType": self.perspective_type,
            "epistemicStatus": self.epistemic_status,
            "sensitivity": self.sensitivity,
        }
        if include_content:
            value["content"] = dict(self.content)
        return value


@dataclass(frozen=True)
class NarrativeProjectRecord:
    project_id: str
    scope: NarrativeScope
    project_type: BookProjectType
    narrator_type: NarrativeNarratorType
    title: str
    state: BookProjectState
    project_version: int
    privacy_state: str
    created_at: str
    updated_at: str
    current_memory_snapshot_id: str | None = None
    current_golden_sample_id: str | None = None
    current_constitution_id: str | None = None
    current_outline_id: str | None = None
    ignored_memory_fingerprint: str | None = None
    writing_context: Mapping[str, Any] = field(default_factory=dict)
    paused_from_state: BookProjectState | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", require_uuid(self.project_id, field="project_id"))
        object.__setattr__(self, "title", require_nonblank(self.title, field="title"))
        if self.project_version < 0:
            raise NarrativeContractError("project_version must not be negative")
        if self.privacy_state != "private":
            raise NarrativeContractError("narrative projects must remain private in M0")
        require_timestamp(self.created_at, field="created_at")
        require_timestamp(self.updated_at, field="updated_at")
        for field in (
            "current_memory_snapshot_id",
            "current_golden_sample_id",
            "current_constitution_id",
            "current_outline_id",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, require_uuid(value, field=field))
        if self.ignored_memory_fingerprint is not None:
            if len(self.ignored_memory_fingerprint) != 64:
                raise NarrativeContractError("ignored_memory_fingerprint must be SHA-256")
        if not isinstance(self.writing_context, Mapping):
            raise NarrativeContractError("writing_context must be an object")
        if self.state is BookProjectState.PAUSED and self.paused_from_state is None:
            raise NarrativeContractError("paused project requires paused_from_state")
        if self.paused_from_state is BookProjectState.PAUSED:
            raise NarrativeContractError("paused_from_state cannot be paused")

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": "narrative-project-v1",
            "projectId": self.project_id,
            "vaultId": self.scope.vault_id,
            "subjectPersonaId": self.scope.subject_persona_id,
            "projectType": self.project_type.value,
            "narratorType": self.narrator_type.value,
            "title": self.title,
            "state": self.state.value,
            "projectVersion": self.project_version,
            "privacyState": self.privacy_state,
            "currentMemorySnapshotId": self.current_memory_snapshot_id,
            "currentGoldenSampleId": self.current_golden_sample_id,
            "currentConstitutionId": self.current_constitution_id,
            "currentOutlineId": self.current_outline_id,
            "ignoredMemoryFingerprint": self.ignored_memory_fingerprint,
            "writingContext": dict(self.writing_context),
            "pausedFromState": self.paused_from_state.value if self.paused_from_state else None,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class NarrativeSnapshotRecord:
    snapshot_id: str
    project_id: str
    vault_id: str
    authority_epoch: int
    memory_refs: tuple[NarrativeMemoryRef, ...]
    source_fingerprint: str
    snapshot_hash: str
    created_by: str
    created_at: str
    writing_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", require_uuid(self.snapshot_id, field="snapshot_id"))
        object.__setattr__(self, "project_id", require_uuid(self.project_id, field="project_id"))
        if not self.memory_refs:
            raise NarrativeContractError("snapshot requires at least one MemoryVersion")
        if len(self.source_fingerprint) != 64 or len(self.snapshot_hash) != 64:
            raise NarrativeContractError("snapshot hashes must be SHA-256")
        if not isinstance(self.writing_context, Mapping):
            raise NarrativeContractError("writing_context must be an object")
        require_timestamp(self.created_at, field="created_at")

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": "narrative-memory-snapshot-v1",
            "snapshotId": self.snapshot_id,
            "projectId": self.project_id,
            "vaultId": self.vault_id,
            "authorityEpoch": self.authority_epoch,
            "memoryVersionRefs": [item.public_contract() for item in self.memory_refs],
            "sourceFingerprint": self.source_fingerprint,
            "snapshotHash": self.snapshot_hash,
            "writingContext": dict(self.writing_context),
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class NarrativeSelectionManifestRecord:
    manifest_id: str
    project_id: str
    job_id: str
    memory_snapshot_id: str
    selected_memory_version_ids: tuple[str, ...]
    selection_hash: str
    created_at: str
    model_id: str | None = None
    prompt_version: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("manifest_id", "project_id", "job_id", "memory_snapshot_id"):
            object.__setattr__(
                self,
                field_name,
                require_uuid(getattr(self, field_name), field=field_name),
            )
        normalized_ids = tuple(
            require_uuid(value, field="selected_memory_version_id")
            for value in self.selected_memory_version_ids
        )
        if not 1 <= len(normalized_ids) <= 3:
            raise NarrativeContractError("selection manifest requires one to three MemoryVersions")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise NarrativeContractError("selection manifest MemoryVersions must be unique")
        object.__setattr__(self, "selected_memory_version_ids", normalized_ids)
        if len(self.selection_hash) != 64:
            raise NarrativeContractError("selection_hash must be SHA-256")
        require_timestamp(self.created_at, field="created_at")

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": "narrative-selection-manifest-v1",
            "selectionManifestId": self.manifest_id,
            "projectId": self.project_id,
            "jobId": self.job_id,
            "memorySnapshotId": self.memory_snapshot_id,
            "selectedMemoryVersionIds": list(self.selected_memory_version_ids),
            "selectionHash": self.selection_hash,
            "modelId": self.model_id,
            "promptVersion": self.prompt_version,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class NarrativeArtifactRecord:
    artifact_version_id: str
    project_id: str
    artifact_type: NarrativeArtifactType
    artifact_key: str
    version_number: int
    memory_snapshot_id: str
    state: NarrativeArtifactState
    content_text: str | None
    payload: Mapping[str, Any]
    content_hash: str
    origin: str
    created_at: str
    parent_version_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    pipeline_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_version_id",
            require_uuid(self.artifact_version_id, field="artifact_version_id"),
        )
        object.__setattr__(self, "project_id", require_uuid(self.project_id, field="project_id"))
        object.__setattr__(
            self,
            "memory_snapshot_id",
            require_uuid(self.memory_snapshot_id, field="memory_snapshot_id"),
        )
        object.__setattr__(self, "artifact_key", require_nonblank(self.artifact_key, field="artifact_key"))
        if self.version_number < 1:
            raise NarrativeContractError("version_number must be positive")
        if self.origin not in {"generated", "userEdited"}:
            raise NarrativeContractError("origin is unsupported")
        if len(self.content_hash) != 64:
            raise NarrativeContractError("content_hash must be SHA-256")
        require_timestamp(self.created_at, field="created_at")

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": "narrative-artifact-v1",
            "artifactVersionId": self.artifact_version_id,
            "projectId": self.project_id,
            "artifactType": self.artifact_type.value,
            "artifactKey": self.artifact_key,
            "versionNumber": self.version_number,
            "parentVersionId": self.parent_version_id,
            "memorySnapshotId": self.memory_snapshot_id,
            "state": self.state.value,
            "contentText": self.content_text,
            "payload": dict(self.payload),
            "contentHash": self.content_hash,
            "origin": self.origin,
            "modelId": self.model_id,
            "promptVersion": self.prompt_version,
            "pipelineVersion": self.pipeline_version,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class NarrativeJobRecord:
    job_id: str
    project_id: str
    job_type: str
    state: NarrativeJobState
    memory_snapshot_id: str
    command_id: str
    idempotency_key: str
    expected_project_version: int
    progress_stage: str
    attempt_count: int
    max_attempts: int
    created_at: str
    input_payload: Mapping[str, Any] = None
    error_code: str | None = None
    retryable: bool = False
    finished_at: str | None = None

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": "narrative-job-v1",
            "jobId": self.job_id,
            "projectId": self.project_id,
            "jobType": self.job_type,
            "state": self.state.value,
            "memorySnapshotId": self.memory_snapshot_id,
            "progressStage": self.progress_stage,
            "attemptCount": self.attempt_count,
            "maxAttempts": self.max_attempts,
            "errorCode": self.error_code,
            "retryable": self.retryable,
            "createdAt": self.created_at,
            "finishedAt": self.finished_at,
        }

    def __post_init__(self) -> None:
        if self.input_payload is None:
            object.__setattr__(self, "input_payload", {})
        elif not isinstance(self.input_payload, Mapping):
            raise NarrativeContractError("input_payload must be an object")


__all__ = [
    "BookProjectState",
    "BookProjectType",
    "NARRATIVE_COMMAND_SCHEMA_VERSION",
    "NARRATIVE_ERROR_SCHEMA_VERSION",
    "NARRATIVE_FIXTURE_SCHEMA_VERSION",
    "NarrativeArtifactState",
    "NarrativeArtifactType",
    "NarrativeCommandEnvelope",
    "NarrativeCommandType",
    "NarrativeContractError",
    "NarrativeErrorCode",
    "NarrativeErrorEnvelope",
    "NarrativeJobState",
    "NarrativeJobRecord",
    "NarrativeMemoryRef",
    "NarrativeNarratorType",
    "NarrativeArtifactRecord",
    "NarrativeProjectRecord",
    "NarrativeScope",
    "NarrativeSelectionManifestRecord",
    "NarrativeSnapshotRecord",
    "require_timestamp",
    "require_uuid",
]

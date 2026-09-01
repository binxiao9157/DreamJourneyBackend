"""Versioned narrative commands and provider-neutral generation processing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.domain.narrative.contracts import (
    BookProjectState,
    NarrativeArtifactRecord,
    NarrativeArtifactState,
    NarrativeArtifactType,
    NarrativeCommandEnvelope,
    NarrativeCommandType,
    NarrativeJobRecord,
    NarrativeJobState,
    NarrativeNarratorType,
    NarrativeProjectRecord,
    NarrativeScope,
    NarrativeSelectionManifestRecord,
)
from app.domain.narrative.fact_guard import (
    FactGuardRejected,
    FactLedgerEntry,
    NarrativeClaim,
    validate_claims,
)
from app.domain.narrative.state_machine import JOB_TRANSITIONS, PROJECT_TRANSITIONS
from app.domain.owner_truth.contracts import SourceKind
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.owner_truth_source import build_source_created_effect_intent
from app.services.narrative_project import (
    NarrativeProjectConflict,
    NarrativeProjectService,
    NarrativeRepository,
)


PROMPT_VERSION = "narrative-writing-v3-progressive-auditions"
PIPELINE_VERSION = "selection-manifest-progressive-artifact-repair-v3"
AUDITION_KEYS = ("documentary", "warmReflection", "thoughtfulMemoir")
GENERATION_STAGES = ("storyPlan", "factualDraft", "literaryRender", "antiAIEdit")


class NarrativeGenerationError(ValueError):
    pass


class NarrativeProviderUnavailable(NarrativeGenerationError):
    pass


class NarrativeJobCancelled(NarrativeGenerationError):
    pass


class NarrativeProvider(Protocol):
    model_id: str

    def generate(
        self,
        *,
        job_type: str,
        project: NarrativeProjectRecord,
        memory_facts: Sequence[Mapping[str, Any]],
        input_payload: Mapping[str, Any],
        supporting_artifacts: Sequence[Mapping[str, Any]],
        writing_context: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class StagedNarrativeProvider(Protocol):
    model_id: str

    def generate_stage(
        self,
        *,
        stage: str,
        job_type: str,
        project: NarrativeProjectRecord,
        context: Mapping[str, Any],
        previous_output: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class DisabledNarrativeProvider:
    """Fail-closed default until a reviewed server-side provider is configured."""

    model_id = "disabled"

    def generate(self, **_: Any) -> Mapping[str, Any]:
        raise NarrativeProviderUnavailable("narrative provider is not configured")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


class NarrativeCommandService:
    """Validates commands, records decisions and enqueues generation work."""

    def __init__(self, repository: NarrativeRepository, formal_memory_store: Any) -> None:
        self.repository = repository
        self.formal_memory_store = formal_memory_store
        self.projects = NarrativeProjectService(repository, formal_memory_store)

    def execute(
        self, *, scope: NarrativeScope, project_id: str, command: NarrativeCommandEnvelope
    ) -> Mapping[str, Any]:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        if project.project_version != command.expected_project_version:
            raise NarrativeProjectConflict(
                "project_version_conflict",
                current_project_version=project.project_version,
            )
        if not command.confirmed:
            raise NarrativeGenerationError("explicit confirmation is required")
        kind = command.command_type
        if kind is NarrativeCommandType.CONFIRM_SETUP:
            writing_context = self._validated_setup_context(command.payload)
            narrator = NarrativeNarratorType(
                command.payload.get("narratorType", project.narrator_type.value)
            )
            saved, snapshot = self.projects.confirm_setup(
                scope=scope,
                project_id=project_id,
                expected_version=command.expected_project_version,
                selected_memory_version_ids=command.payload.get("selectedMemoryVersionIds", ()),
                narrator_type=narrator,
                writing_context=writing_context,
            )
            self._decision(saved, command, scope)
            return self._accepted(self._queue(saved, command, "auditions", snapshot.snapshot_id))
        if kind in {
            NarrativeCommandType.GENERATE_AUDITIONS,
            NarrativeCommandType.GENERATE_GOLDEN_SAMPLE,
            NarrativeCommandType.GENERATE_OUTLINE,
            NarrativeCommandType.GENERATE_CHAPTER,
            NarrativeCommandType.REVISE_OUTLINE,
            NarrativeCommandType.REVISE_CHAPTER,
        }:
            return self._start_generation(project, scope, command)
        if kind is NarrativeCommandType.SELECT_AUDITION:
            selected = self._required_artifact(
                project,
                command.payload.get("artifactVersionId"),
                NarrativeArtifactType.WRITING_AUDITION,
            )
            if project.state is not BookProjectState.AUDITIONS_READY:
                raise NarrativeProjectConflict("invalid_state")
            saved = self.repository.save_project(
                replace(project, state=BookProjectState.GENERATING_GOLDEN_SAMPLE),
                expected_version=project.project_version,
            )
            self._decision(saved, command, scope, target=selected.artifact_version_id)
            payload = dict(command.payload)
            payload["selectedAudition"] = selected.public_contract()
            queued = replace(command, payload=payload, expected_project_version=saved.project_version)
            return self._accepted(
                self._queue(saved, queued, "goldenSample", self._snapshot_id(saved))
            )
        if kind is NarrativeCommandType.SUBMIT_ARTIFACT_FEEDBACK:
            target = self._required_artifact(
                project, command.payload.get("artifactVersionId"), None
            )
            if not any(
                str(command.payload.get(key) or "").strip()
                for key in ("factFeedback", "styleFeedback")
            ):
                raise NarrativeGenerationError("factFeedback or styleFeedback is required")
            self._decision(project, command, scope, target=target.artifact_version_id)
            result = dict(self._result(project))
            fact_feedback = str(command.payload.get("factFeedback") or "").strip()
            if fact_feedback:
                result["factCorrection"] = self._create_fact_correction_source(
                    project=project,
                    artifact=target,
                    command=command,
                    scope=scope,
                    text=fact_feedback,
                )
            return result
        if kind is NarrativeCommandType.CONFIRM_GOLDEN_SAMPLE:
            return self._confirm_golden(project, scope, command)
        if kind is NarrativeCommandType.CONFIRM_OUTLINE:
            outline = self._required_artifact(
                project, command.payload.get("artifactVersionId"), NarrativeArtifactType.OUTLINE
            )
            if project.state is not BookProjectState.OUTLINE_REVIEW:
                raise NarrativeProjectConflict("invalid_state")
            saved = self.repository.save_project(
                replace(
                    project,
                    state=BookProjectState.WRITING,
                    current_outline_id=outline.artifact_version_id,
                ),
                expected_version=project.project_version,
            )
            self._decision(saved, command, scope, target=outline.artifact_version_id)
            return self._result(saved)
        if kind is NarrativeCommandType.FINALIZE_CHAPTER:
            return self._finalize_chapter(project, scope, command)
        if kind is NarrativeCommandType.EDIT_ARTIFACT:
            return self._edit_artifact(project, scope, command)
        if kind is NarrativeCommandType.RESTORE_ARTIFACT_VERSION:
            return self._restore_artifact(project, scope, command)
        if kind is NarrativeCommandType.ADOPT_MEMORY_UPDATE:
            saved, snapshot = self.projects.adopt_memory_update(
                scope=scope,
                project_id=project_id,
                expected_version=command.expected_project_version,
            )
            self._decision(saved, command, scope)
            result = dict(self._result(saved))
            result["memorySnapshotId"] = snapshot.snapshot_id
            return result
        if kind is NarrativeCommandType.IGNORE_MEMORY_UPDATE:
            saved = self.projects.ignore_memory_update(
                scope=scope,
                project_id=project_id,
                expected_version=command.expected_project_version,
            )
            self._decision(saved, command, scope)
            return self._result(saved)
        if kind is NarrativeCommandType.PAUSE_PROJECT:
            if not PROJECT_TRANSITIONS.allows(project.state, BookProjectState.PAUSED):
                raise NarrativeProjectConflict("invalid_state")
            saved = self.repository.save_project(
                replace(
                    project,
                    state=BookProjectState.PAUSED,
                    paused_from_state=project.state,
                ),
                expected_version=project.project_version,
            )
            self._decision(saved, command, scope)
            return self._result(saved)
        if kind is NarrativeCommandType.RESUME_PROJECT:
            if project.state is not BookProjectState.PAUSED or project.paused_from_state is None:
                raise NarrativeProjectConflict("invalid_state")
            if not PROJECT_TRANSITIONS.allows(project.state, project.paused_from_state):
                raise NarrativeProjectConflict("invalid_state")
            saved = self.repository.save_project(
                replace(project, state=project.paused_from_state, paused_from_state=None),
                expected_version=project.project_version,
            )
            self._decision(saved, command, scope)
            return self._result(saved)
        if kind is NarrativeCommandType.ARCHIVE_PROJECT:
            saved = self.repository.save_project(
                replace(project, state=BookProjectState.ARCHIVED),
                expected_version=project.project_version,
            )
            self._decision(saved, command, scope)
            return self._result(saved)
        raise NarrativeGenerationError(f"{kind.value} is not available")

    @staticmethod
    def _validated_setup_context(payload: Mapping[str, Any]) -> Mapping[str, str]:
        primary_reader = str(payload.get("primaryReader") or "").strip()
        if primary_reader not in {"self", "family", "descendants"}:
            raise NarrativeGenerationError("primaryReader is required")
        if str(payload.get("privacyScope") or "").strip() != "private":
            raise NarrativeGenerationError("M0 narrative projects must remain private")
        if payload.get("privacyConfirmed") is not True:
            raise NarrativeGenerationError("privacy confirmation is required")
        rules_version = str(payload.get("confirmationRulesVersion") or "").strip()
        if rules_version != "narrative-setup-v1":
            raise NarrativeGenerationError("confirmation rules version is unsupported")
        return {
            "primaryReader": primary_reader,
            "privacyScope": "private",
            "confirmationRulesVersion": rules_version,
        }

    def _start_generation(
        self,
        project: NarrativeProjectRecord,
        scope: NarrativeScope,
        command: NarrativeCommandEnvelope,
    ) -> Mapping[str, Any]:
        mapping = {
            NarrativeCommandType.GENERATE_AUDITIONS: "auditions",
            NarrativeCommandType.GENERATE_GOLDEN_SAMPLE: "goldenSample",
            NarrativeCommandType.GENERATE_OUTLINE: "outline",
            NarrativeCommandType.GENERATE_CHAPTER: "chapter",
            NarrativeCommandType.REVISE_OUTLINE: "reviseArtifact",
            NarrativeCommandType.REVISE_CHAPTER: "reviseArtifact",
        }
        allowed = {
            "auditions": {
                BookProjectState.GENERATING_AUDITIONS,
                BookProjectState.AUDITIONS_READY,
            },
            "goldenSample": {
                BookProjectState.AUDITIONS_READY,
                BookProjectState.GENERATING_GOLDEN_SAMPLE,
                BookProjectState.GOLDEN_SAMPLE_REVIEW,
            },
            "outline": {BookProjectState.TONE_CONFIRMED, BookProjectState.OUTLINE_REVIEW},
            "chapter": {BookProjectState.WRITING, BookProjectState.UPDATE_AVAILABLE},
            "reviseArtifact": {
                BookProjectState.GOLDEN_SAMPLE_REVIEW,
                BookProjectState.OUTLINE_REVIEW,
                BookProjectState.WRITING,
            },
        }
        job_type = mapping[command.command_type]
        if project.state not in allowed[job_type]:
            raise NarrativeProjectConflict("invalid_state")
        if job_type == "auditions" and isinstance(
            command.payload.get("selectedMemoryVersionIds"), list
        ):
            project, snapshot = self.projects.replace_writing_material(
                scope=scope,
                project_id=project.project_id,
                expected_version=command.expected_project_version,
                selected_memory_version_ids=command.payload["selectedMemoryVersionIds"],
            )
            self._decision(project, command, scope)
            queued = replace(command, expected_project_version=project.project_version)
            return self._accepted(
                self._queue(project, queued, job_type, snapshot.snapshot_id)
            )
        if job_type == "chapter" and not str(command.payload.get("chapterKey") or "").strip():
            raise NarrativeGenerationError("chapterKey is required")
        self._decision(project, command, scope)
        return self._accepted(
            self._queue(project, command, job_type, self._snapshot_id(project))
        )

    def _confirm_golden(
        self,
        project: NarrativeProjectRecord,
        scope: NarrativeScope,
        command: NarrativeCommandEnvelope,
    ) -> Mapping[str, Any]:
        golden = self._required_artifact(
            project,
            command.payload.get("artifactVersionId"),
            NarrativeArtifactType.GOLDEN_SAMPLE,
        )
        if project.state is not BookProjectState.GOLDEN_SAMPLE_REVIEW:
            raise NarrativeProjectConflict("invalid_state")
        confirmed = self._copy_artifact(golden, state=NarrativeArtifactState.CONFIRMED)
        style = self._new_artifact(
            project,
            NarrativeArtifactType.NARRATIVE_STYLE_PROFILE,
            "style-profile",
            payload={
                "goldenSampleVersionId": confirmed.artifact_version_id,
                "styleFeedback": str(command.payload.get("styleFeedback") or "").strip(),
                "narratorType": project.narrator_type.value,
                "styleAnchor": True,
            },
            state=NarrativeArtifactState.CONFIRMED,
            origin="userEdited",
        )
        constitution = self._new_artifact(
            project,
            NarrativeArtifactType.WRITING_CONSTITUTION,
            "writing-constitution",
            payload={
                "goldenSampleVersionId": confirmed.artifact_version_id,
                "styleProfileVersionId": style.artifact_version_id,
                "rules": [
                    "只使用正式记忆",
                    "保持事实不确定性",
                    "不补写对白、心理和因果",
                    "避免套话和强行升华",
                ],
            },
            state=NarrativeArtifactState.CONFIRMED,
            origin="userEdited",
        )
        saved = self.repository.save_project(
            replace(
                project,
                state=BookProjectState.TONE_CONFIRMED,
                current_golden_sample_id=confirmed.artifact_version_id,
                current_constitution_id=constitution.artifact_version_id,
            ),
            expected_version=project.project_version,
        )
        self._decision(saved, command, scope, target=confirmed.artifact_version_id)
        result = dict(self._result(saved))
        result["artifacts"] = [
            confirmed.public_contract(),
            style.public_contract(),
            constitution.public_contract(),
        ]
        return result

    def _edit_artifact(
        self,
        project: NarrativeProjectRecord,
        scope: NarrativeScope,
        command: NarrativeCommandEnvelope,
    ) -> Mapping[str, Any]:
        source = self._required_artifact(
            project, command.payload.get("artifactVersionId"), None
        )
        if source.artifact_type not in {
            NarrativeArtifactType.GOLDEN_SAMPLE,
            NarrativeArtifactType.OUTLINE,
            NarrativeArtifactType.CHAPTER,
            NarrativeArtifactType.WRITING_CONSTITUTION,
        }:
            raise NarrativeGenerationError("artifact type cannot be edited")
        allowed_states = {
            NarrativeArtifactType.GOLDEN_SAMPLE: {BookProjectState.GOLDEN_SAMPLE_REVIEW},
            NarrativeArtifactType.OUTLINE: {BookProjectState.OUTLINE_REVIEW},
            NarrativeArtifactType.CHAPTER: {
                BookProjectState.WRITING,
                BookProjectState.UPDATE_AVAILABLE,
            },
            NarrativeArtifactType.WRITING_CONSTITUTION: {
                BookProjectState.TONE_CONFIRMED,
                BookProjectState.OUTLINE_REVIEW,
                BookProjectState.WRITING,
                BookProjectState.UPDATE_AVAILABLE,
            },
        }
        if project.state not in allowed_states[source.artifact_type]:
            raise NarrativeProjectConflict("invalid_state")
        outline_nodes = command.payload.get("nodes")
        text = str(command.payload.get("contentText") or "").strip()
        if source.artifact_type is NarrativeArtifactType.OUTLINE and isinstance(
            outline_nodes, list
        ):
            normalized_nodes = self._validated_outline_nodes(outline_nodes)
            text = text or "\n".join(
                f"{index + 1}. {node['title']}"
                for index, node in enumerate(normalized_nodes)
                if not node["hidden"]
            )
        else:
            normalized_nodes = None
        if not text:
            raise NarrativeGenerationError("contentText is required")
        if len(text) > 120_000:
            raise NarrativeGenerationError("contentText is too large")
        snapshot = self.repository.get_snapshot(
            project_id=project.project_id,
            snapshot_id=source.memory_snapshot_id,
        )
        available = {item.memory_version_id for item in snapshot.memory_refs}
        requested = command.payload.get("memoryVersionIds")
        if isinstance(requested, list):
            memory_ids = [str(value) for value in requested]
        else:
            memory_ids = sorted({
                str(value)
                for claim in source.payload.get("claims", [])
                if isinstance(claim, Mapping)
                for value in claim.get("memoryVersionIds", [])
            })
        if not memory_ids:
            memory_ids = sorted(available)
        if not set(memory_ids).issubset(available):
            raise NarrativeProjectConflict("memory_snapshot_stale")
        payload = dict(source.payload)
        payload.update(
            {
                "title": str(
                    command.payload.get("title") or payload.get("title") or ""
                ).strip(),
                "editNote": str(command.payload.get("editNote") or "").strip(),
            }
        )
        if normalized_nodes is not None:
            node_memory_ids = {
                str(value)
                for node in normalized_nodes
                for value in node["memoryVersionIds"]
            }
            if not node_memory_ids.issubset(available):
                raise NarrativeProjectConflict("memory_snapshot_stale")
            payload["nodes"] = normalized_nodes
            payload["claims"] = [
                {
                    "claimId": node["chapterKey"],
                    "text": node["title"],
                    "memoryVersionIds": node["memoryVersionIds"],
                }
                for node in normalized_nodes
            ]
        else:
            payload.update(
                {
                    "claims": [
                        {
                            "claimId": f"{source.artifact_key}-user-edit",
                            "text": text,
                            "memoryVersionIds": memory_ids,
                        }
                    ],
                    "paragraphs": [
                        {
                            "paragraphId": f"{source.artifact_key}-user-edit",
                            "text": text,
                            "memoryVersionIds": memory_ids,
                        }
                    ],
                }
            )
            if source.artifact_type is NarrativeArtifactType.WRITING_CONSTITUTION:
                rules = [line.strip(" -\t") for line in text.splitlines() if line.strip(" -\t")]
                if not rules:
                    raise NarrativeGenerationError("writing constitution requires rules")
                payload["rules"] = rules
        edited = self._new_artifact(
            project,
            source.artifact_type,
            source.artifact_key,
            payload=payload,
            state=NarrativeArtifactState.READY_FOR_REVIEW,
            origin="userEdited",
            content_text=text,
            parent=source,
        )
        saved = project
        if edited.artifact_type is NarrativeArtifactType.OUTLINE:
            saved = self.repository.save_project(
                replace(project, current_outline_id=edited.artifact_version_id),
                expected_version=project.project_version,
            )
        self._decision(saved, command, scope, target=edited.artifact_version_id)
        result = dict(self._result(saved))
        result["artifact"] = edited.public_contract()
        return result

    @staticmethod
    def _validated_outline_nodes(values: Sequence[Any]) -> list[dict[str, Any]]:
        if not values:
            raise NarrativeGenerationError("outline must contain at least one chapter")
        result: list[dict[str, Any]] = []
        keys: set[str] = set()
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                raise NarrativeGenerationError("outline node must be an object")
            key = str(value.get("chapterKey") or "").strip()
            title = str(value.get("title") or "").strip()
            if not key or not title or key in keys:
                raise NarrativeGenerationError("outline chapter key and title must be unique")
            keys.add(key)
            refs = value.get("memoryVersionIds")
            if not isinstance(refs, list) or not refs:
                raise NarrativeGenerationError("outline node requires formal memory references")
            result.append(
                {
                    "chapterKey": key,
                    "title": title,
                    "order": index + 1,
                    "hidden": bool(value.get("hidden")),
                    "arrangementReason": str(
                        value.get("arrangementReason") or value.get("intent") or ""
                    ).strip(),
                    "materialGap": str(value.get("materialGap") or "").strip(),
                    "memoryVersionIds": sorted({str(item) for item in refs}),
                }
            )
        if all(item["hidden"] for item in result):
            raise NarrativeGenerationError("outline must keep at least one visible chapter")
        return result

    def _restore_artifact(
        self,
        project: NarrativeProjectRecord,
        scope: NarrativeScope,
        command: NarrativeCommandEnvelope,
    ) -> Mapping[str, Any]:
        source = self._required_artifact(
            project, command.payload.get("artifactVersionId"), None, allow_historical=True
        )
        if source.artifact_type not in {
            NarrativeArtifactType.OUTLINE,
            NarrativeArtifactType.CHAPTER,
        }:
            raise NarrativeGenerationError("only outline and chapter versions can be restored")
        if source.artifact_type is NarrativeArtifactType.OUTLINE:
            if project.state is not BookProjectState.OUTLINE_REVIEW:
                raise NarrativeProjectConflict("invalid_state")
        elif project.state not in {BookProjectState.WRITING, BookProjectState.UPDATE_AVAILABLE}:
            raise NarrativeProjectConflict("invalid_state")
        restored = self._new_artifact(
            project,
            source.artifact_type,
            source.artifact_key,
            payload={**dict(source.payload), "restoredFromVersionId": source.artifact_version_id},
            state=NarrativeArtifactState.READY_FOR_REVIEW,
            origin="userEdited",
            content_text=source.content_text,
            parent=source,
        )
        saved = project
        if restored.artifact_type is NarrativeArtifactType.OUTLINE:
            saved = self.repository.save_project(
                replace(project, current_outline_id=restored.artifact_version_id),
                expected_version=project.project_version,
            )
        self._decision(saved, command, scope, target=restored.artifact_version_id)
        result = dict(self._result(saved))
        result["artifact"] = restored.public_contract()
        return result

    def _finalize_chapter(
        self,
        project: NarrativeProjectRecord,
        scope: NarrativeScope,
        command: NarrativeCommandEnvelope,
    ) -> Mapping[str, Any]:
        chapter = self._required_artifact(
            project,
            command.payload.get("artifactVersionId"),
            NarrativeArtifactType.CHAPTER,
        )
        if project.state not in {BookProjectState.WRITING, BookProjectState.UPDATE_AVAILABLE}:
            raise NarrativeProjectConflict("invalid_state")
        final = self._copy_artifact(chapter, state=NarrativeArtifactState.FINAL)
        self._decision(project, command, scope, target=final.artifact_version_id)
        result = dict(self._result(project))
        result["artifact"] = final.public_contract()
        return result

    def _new_artifact(
        self,
        project: NarrativeProjectRecord,
        artifact_type: NarrativeArtifactType,
        key: str,
        *,
        payload: Mapping[str, Any],
        state: NarrativeArtifactState,
        origin: str,
        content_text: str | None = None,
        parent: NarrativeArtifactRecord | None = None,
    ) -> NarrativeArtifactRecord:
        prior = self.repository.list_artifacts(
            project_id=project.project_id, artifact_type=artifact_type
        )
        versions = [item.version_number for item in prior if item.artifact_key == key]
        artifact = NarrativeArtifactRecord(
            artifact_version_id=str(uuid4()),
            project_id=project.project_id,
            artifact_type=artifact_type,
            artifact_key=key,
            version_number=(max(versions) + 1 if versions else 1),
            parent_version_id=(parent.artifact_version_id if parent else None),
            memory_snapshot_id=self._snapshot_id(project),
            state=state,
            content_text=content_text,
            payload=dict(payload),
            content_hash=_digest({"text": content_text, "payload": payload}),
            origin=origin,
            prompt_version=PROMPT_VERSION,
            pipeline_version=PIPELINE_VERSION,
            created_at=_now(),
        )
        return self.repository.append_artifact(artifact)

    def _copy_artifact(
        self, source: NarrativeArtifactRecord, *, state: NarrativeArtifactState
    ) -> NarrativeArtifactRecord:
        project = self.repository.get_project_for_worker(project_id=source.project_id)
        return self._new_artifact(
            project,
            source.artifact_type,
            source.artifact_key,
            payload=source.payload,
            state=state,
            origin="userEdited",
            content_text=source.content_text,
            parent=source,
        )

    def _required_artifact(
        self,
        project: NarrativeProjectRecord,
        artifact_id: Any,
        expected_type: NarrativeArtifactType | None,
        *,
        allow_historical: bool = False,
    ) -> NarrativeArtifactRecord:
        if not str(artifact_id or "").strip():
            raise NarrativeGenerationError("artifactVersionId is required")
        artifact = self.repository.get_artifact(
            project_id=project.project_id, artifact_version_id=str(artifact_id)
        )
        if expected_type is not None and artifact.artifact_type is not expected_type:
            raise NarrativeGenerationError("artifact type does not match command")
        if not allow_historical and artifact.state in {
            NarrativeArtifactState.STALE,
            NarrativeArtifactState.SUPERSEDED,
        }:
            raise NarrativeProjectConflict("memory_snapshot_stale")
        if allow_historical and artifact.state is NarrativeArtifactState.STALE:
            raise NarrativeProjectConflict("memory_snapshot_stale")
        return artifact

    def _queue(
        self,
        project: NarrativeProjectRecord,
        command: NarrativeCommandEnvelope,
        job_type: str,
        snapshot_id: str,
    ) -> NarrativeJobRecord:
        return self.repository.save_job(
            NarrativeJobRecord(
                job_id=str(uuid4()),
                project_id=project.project_id,
                job_type=job_type,
                state=NarrativeJobState.QUEUED,
                memory_snapshot_id=snapshot_id,
                command_id=command.command_id,
                idempotency_key=f"{project.project_id}:{command.command_id}:{job_type}",
                expected_project_version=project.project_version,
                progress_stage="queued",
                attempt_count=0,
                max_attempts=3,
                input_payload=dict(command.payload),
                created_at=_now(),
            )
        )

    def _decision(
        self,
        project: NarrativeProjectRecord,
        command: NarrativeCommandEnvelope,
        scope: NarrativeScope,
        target: str | None = None,
    ) -> None:
        self.repository.record_decision(
            project=project,
            command_id=command.command_id,
            expected_project_version=command.expected_project_version,
            decision_type=command.command_type.value,
            actor_subject_id=scope.actor_subject_id,
            payload=command.payload,
            target_artifact_version_id=target,
        )

    def _create_fact_correction_source(
        self,
        *,
        project: NarrativeProjectRecord,
        artifact: NarrativeArtifactRecord,
        command: NarrativeCommandEnvelope,
        scope: NarrativeScope,
        text: str,
    ) -> Mapping[str, Any]:
        source_id = str(
            uuid5(
                NAMESPACE_URL,
                f"dreamjourney:narrative-fact-feedback:{scope.vault_id}:{command.command_id}",
            )
        )
        context = OwnerTruthCommandContext(
            vault_id=scope.vault_id,
            owner_subject_id=scope.owner_subject_id,
            actor_subject_id=scope.actor_subject_id,
            authorization_capture=scope.authorization_capture,
        )
        source_command = CreateTextSourceCommand(
            command_id=f"narrative-fact-feedback:{command.command_id}",
            source_id=source_id,
            expected_version=0,
            expected_authority_epoch=scope.authority_epoch,
            source_kind=SourceKind.TEXT,
            text=text,
            metadata={
                "entryPoint": "narrativeWritingFactFeedback",
                "projectId": project.project_id,
                "artifactVersionId": artifact.artifact_version_id,
                "subjectPersonaId": scope.subject_persona_id,
                "requiresOwnerReview": True,
            },
        )
        record = source_command.write_record(context=context)
        source = self.formal_memory_store.create_owner_truth_source(record)
        effect = self.formal_memory_store.effect_kernel_repository().accept(
            build_source_created_effect_intent(record=record, source=source)
        )
        return {
            "status": "pendingFormalMemoryReview",
            "sourceId": source.source_id,
            "receiptId": source.receipt_id,
            "effectReceiptId": effect.receipt_id,
        }

    @staticmethod
    def _snapshot_id(project: NarrativeProjectRecord) -> str:
        if project.current_memory_snapshot_id is None:
            raise NarrativeProjectConflict("memory_snapshot_stale")
        return project.current_memory_snapshot_id

    @staticmethod
    def _accepted(job: NarrativeJobRecord) -> Mapping[str, Any]:
        return {
            "schemaVersion": "narrative-command-result-v1",
            "accepted": True,
            "job": job.public_contract(),
        }

    @staticmethod
    def _result(project: NarrativeProjectRecord) -> Mapping[str, Any]:
        return {
            "schemaVersion": "narrative-command-result-v1",
            "project": project.public_contract(),
        }


class NarrativeGenerationProcessor:
    """Runs the multi-stage pipeline and commits only Fact Guard-clean artifacts."""

    def __init__(self, repository: NarrativeRepository, provider: NarrativeProvider) -> None:
        self.repository = repository
        self.provider = provider

    def run_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord:
        job = self.repository.get_job(project_id=project_id, job_id=job_id)
        if job.state is NarrativeJobState.CANCELLED:
            return job
        if job.state is NarrativeJobState.READY_FOR_REVIEW:
            return job
        project = self.repository.get_project_for_worker(project_id=project_id)
        snapshot = self.repository.get_snapshot(
            project_id=project_id, snapshot_id=job.memory_snapshot_id
        )
        try:
            if job.state is NarrativeJobState.QUEUED:
                job = self._stage(job, NarrativeJobState.SNAPSHOTTING)
            elif job.state is not NarrativeJobState.SNAPSHOTTING:
                raise NarrativeGenerationError("job is not runnable")
            if project.current_memory_snapshot_id != snapshot.snapshot_id:
                raise NarrativeProjectConflict("memory_snapshot_stale")
            job = self._stage(job, NarrativeJobState.RETRIEVING)
            facts = [
                {
                    "memoryVersionId": ref.memory_version_id,
                    "contentHash": ref.content_hash,
                    "memoryKind": ref.memory_kind,
                    "epistemicStatus": ref.epistemic_status,
                    "content": dict(ref.content),
                }
                for ref in snapshot.memory_refs
            ]
            support = [
                item.public_contract()
                for item in self.repository.list_artifacts(project_id=project_id)
                if item.state
                not in {NarrativeArtifactState.STALE, NarrativeArtifactState.SUPERSEDED}
            ]
            job = self._stage(job, NarrativeJobState.PLANNING)
            job = self._stage(job, NarrativeJobState.DRAFTING)
            output, selection_manifest = self._provider_output(
                project=project,
                job=job,
                facts=facts,
                supporting_artifacts=support,
                writing_context=snapshot.writing_context,
            )
            job = self._stage(job, NarrativeJobState.VALIDATING_FACTS)
            job = self._stage(job, NarrativeJobState.EDITING_STYLE)
            job = self._stage(job, NarrativeJobState.FINAL_VALIDATION)
            if job.job_type == "auditions":
                if selection_manifest is None:
                    selected_ids = tuple(
                        ref.memory_version_id for ref in snapshot.memory_refs[:3]
                    )
                    selection_manifest = self.repository.save_selection_manifest(
                        NarrativeSelectionManifestRecord(
                            manifest_id=str(uuid4()),
                            project_id=project.project_id,
                            job_id=job.job_id,
                            memory_snapshot_id=job.memory_snapshot_id,
                            selected_memory_version_ids=selected_ids,
                            selection_hash=_digest({
                                "memorySnapshotId": job.memory_snapshot_id,
                                "selectedMemoryVersionIds": list(selected_ids),
                            }),
                            model_id=self.provider.model_id,
                            prompt_version=self._prompt_version,
                            created_at=_now(),
                        )
                    )
                current_project = self.repository.get_project_for_worker(
                    project_id=project_id
                )
                if current_project.project_version != project.project_version:
                    raise NarrativeProjectConflict("project_version_conflict")
                artifacts = self._publish_auditions_progressively(
                    project=project,
                    job=job,
                    refs=snapshot.memory_refs,
                    output=output,
                    selection_manifest=selection_manifest,
                    context={
                        "memoryFacts": [
                            item for item in facts
                            if item["memoryVersionId"]
                            in selection_manifest.selected_memory_version_ids
                        ],
                        "inputPayload": dict(job.input_payload),
                        "supportingArtifacts": support,
                        "writingContext": dict(snapshot.writing_context),
                        "selectionManifest": {
                            "selectionManifestId": selection_manifest.manifest_id,
                            "selectionHash": selection_manifest.selection_hash,
                            "selectedMemoryVersionIds": list(
                                selection_manifest.selected_memory_version_ids
                            ),
                        },
                    },
                )
            else:
                artifacts = self._validate_and_build(
                    project,
                    job,
                    snapshot.memory_refs,
                    output,
                )
            latest = self.repository.get_job(project_id=project_id, job_id=job_id)
            if latest.state is NarrativeJobState.CANCELLED:
                return latest
            completed_job = replace(
                latest,
                state=NarrativeJobState.READY_FOR_REVIEW,
                progress_stage=NarrativeJobState.READY_FOR_REVIEW.value,
                finished_at=_now(),
            )
            return self.repository.publish_generation_result(
                project=self._advanced_project(project, job, artifacts),
                expected_project_version=project.project_version,
                artifacts=(() if job.job_type == "auditions" else artifacts),
                completed_job=completed_job,
            )
        except NarrativeProviderUnavailable:
            return self._failed(job, "provider_unavailable", retryable=True)
        except NarrativeJobCancelled:
            return self.repository.get_job(project_id=project_id, job_id=job_id)
        except FactGuardRejected:
            return self._failed(job, "unsupported_fact_detected", retryable=False)
        except (NarrativeGenerationError, NarrativeProjectConflict) as exc:
            return self._failed(job, str(exc), retryable=False)

    def _provider_output(
        self,
        *,
        project: NarrativeProjectRecord,
        job: NarrativeJobRecord,
        facts: Sequence[Mapping[str, Any]],
        supporting_artifacts: Sequence[Mapping[str, Any]],
        writing_context: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], NarrativeSelectionManifestRecord | None]:
        generate_stage = getattr(self.provider, "generate_stage", None)
        if not callable(generate_stage):
            return (
                self.provider.generate(
                    job_type=job.job_type,
                    project=project,
                    memory_facts=facts,
                    input_payload=job.input_payload,
                    supporting_artifacts=supporting_artifacts,
                    writing_context=dict(writing_context),
                ),
                None,
            )
        context = {
            "memoryFacts": list(facts),
            "inputPayload": dict(job.input_payload),
            "supportingArtifacts": list(supporting_artifacts),
            "writingContext": dict(writing_context),
            "promptVersion": self._prompt_version,
            "pipelineVersion": self._pipeline_version,
        }

        if job.job_type == "auditions":
            manifest = self.repository.get_selection_manifest(
                project_id=project.project_id,
                job_id=job.job_id,
            )
            if manifest is None:
                plan_output = generate_stage(
                    stage="storyPlan",
                    job_type=job.job_type,
                    project=project,
                    context=context,
                    previous_output={},
                )
                selected_ids = self._selected_memory_version_ids(
                    output=plan_output,
                    facts=facts,
                )
                manifest = self.repository.save_selection_manifest(
                    NarrativeSelectionManifestRecord(
                        manifest_id=str(uuid4()),
                        project_id=project.project_id,
                        job_id=job.job_id,
                        memory_snapshot_id=job.memory_snapshot_id,
                        selected_memory_version_ids=selected_ids,
                        selection_hash=_digest({
                            "memorySnapshotId": job.memory_snapshot_id,
                            "selectedMemoryVersionIds": list(selected_ids),
                        }),
                        model_id=self.provider.model_id,
                        prompt_version=self._prompt_version,
                        created_at=_now(),
                    )
                )
                output = dict(plan_output)
            else:
                if manifest.memory_snapshot_id != job.memory_snapshot_id:
                    raise NarrativeProjectConflict("memory_snapshot_stale")
                output = {"plan": {}}

            plan = output.get("plan")
            normalized_plan = dict(plan) if isinstance(plan, Mapping) else {}
            normalized_plan.update({
                "memoryVersionIds": list(manifest.selected_memory_version_ids),
                "selectionManifestId": manifest.manifest_id,
                "selectionHash": manifest.selection_hash,
            })
            output = {**output, "plan": normalized_plan}

            by_id = {
                str(item.get("memoryVersionId") or ""): item
                for item in facts
            }
            try:
                selected_facts = [
                    by_id[memory_version_id]
                    for memory_version_id in manifest.selected_memory_version_ids
                ]
            except KeyError as exc:
                raise NarrativeProjectConflict("memory_snapshot_stale") from exc
            context = {
                **context,
                "memoryFacts": selected_facts,
                "selectionManifest": {
                    "selectionManifestId": manifest.manifest_id,
                    "selectionHash": manifest.selection_hash,
                    "selectedMemoryVersionIds": list(
                        manifest.selected_memory_version_ids
                    ),
                },
            }
            for stage in GENERATION_STAGES[1:]:
                value = generate_stage(
                    stage=stage,
                    job_type=job.job_type,
                    project=project,
                    context=context,
                    previous_output=output,
                )
                if not isinstance(value, Mapping):
                    raise NarrativeGenerationError(
                        f"provider stage {stage} returned an invalid object"
                    )
                output = value
            return output, manifest

        output: Mapping[str, Any] = {}
        for stage in GENERATION_STAGES:
            value = generate_stage(
                stage=stage,
                job_type=job.job_type,
                project=project,
                context=context,
                previous_output=output,
            )
            if not isinstance(value, Mapping):
                raise NarrativeGenerationError(f"provider stage {stage} returned an invalid object")
            output = value
        return output, None

    def _publish_auditions_progressively(
        self,
        *,
        project: NarrativeProjectRecord,
        job: NarrativeJobRecord,
        refs: Sequence[Any],
        output: Mapping[str, Any],
        selection_manifest: NarrativeSelectionManifestRecord,
        context: Mapping[str, Any],
    ) -> tuple[NarrativeArtifactRecord, ...]:
        raw = output.get("artifacts")
        if not isinstance(raw, list):
            raise NarrativeGenerationError("provider output has no artifacts")
        by_key = {
            str(item.get("key") or "").strip(): item
            for item in raw
            if isinstance(item, Mapping)
        }
        existing = {
            item.artifact_key: item
            for item in self.repository.list_artifacts(
                project_id=project.project_id,
                artifact_type=NarrativeArtifactType.WRITING_AUDITION,
            )
            if item.state is NarrativeArtifactState.READY_FOR_REVIEW
            and item.memory_snapshot_id == job.memory_snapshot_id
            and item.payload.get("generationJobId") == job.job_id
        }
        pending: list[tuple[str, Mapping[str, Any], str]] = []

        for key in AUDITION_KEYS:
            if key in existing:
                continue
            candidate = by_key.get(key)
            if candidate is None:
                pending.append((key, {"key": key}, "audition artifact is missing"))
                continue
            try:
                artifact = self._build_single_audition(
                    project=project,
                    job=job,
                    refs=refs,
                    item=candidate,
                    expected_key=key,
                    selection_manifest=selection_manifest,
                )
            except (NarrativeGenerationError, FactGuardRejected) as exc:
                pending.append((key, candidate, str(exc)))
                continue
            existing[key] = self.repository.append_artifact(artifact)
            self._update_audition_progress(job, len(existing))

        repair = getattr(self.provider, "repair_audition_artifact", None)
        for key, candidate, violation in pending:
            if not callable(repair):
                raise NarrativeGenerationError(violation)
            latest_candidate = candidate
            latest_violation = violation
            for _ in range(3):
                latest_candidate = repair(
                    project=project,
                    context=context,
                    expected_key=key,
                    artifact=latest_candidate,
                    violation=latest_violation,
                )
                try:
                    artifact = self._build_single_audition(
                        project=project,
                        job=job,
                        refs=refs,
                        item=latest_candidate,
                        expected_key=key,
                        selection_manifest=selection_manifest,
                    )
                except (NarrativeGenerationError, FactGuardRejected) as exc:
                    latest_violation = str(exc)
                    continue
                existing[key] = self.repository.append_artifact(artifact)
                self._update_audition_progress(job, len(existing))
                break
            else:
                raise NarrativeGenerationError(latest_violation)

        if set(existing) != set(AUDITION_KEYS):
            raise NarrativeGenerationError("audition_generation_incomplete")
        return tuple(existing[key] for key in AUDITION_KEYS)

    def _build_single_audition(
        self,
        *,
        project: NarrativeProjectRecord,
        job: NarrativeJobRecord,
        refs: Sequence[Any],
        item: Mapping[str, Any],
        expected_key: str,
        selection_manifest: NarrativeSelectionManifestRecord,
    ) -> NarrativeArtifactRecord:
        artifacts = self._validate_and_build(
            project,
            job,
            refs,
            {"artifacts": [item]},
            selected_memory_version_ids=selection_manifest.selected_memory_version_ids,
            expected_audition_keys=(expected_key,),
        )
        return artifacts[0]

    def _update_audition_progress(
        self, job: NarrativeJobRecord, completed_count: int
    ) -> NarrativeJobRecord:
        current = self.repository.get_job(
            project_id=job.project_id, job_id=job.job_id
        )
        if current.state is NarrativeJobState.CANCELLED:
            raise NarrativeJobCancelled("job was cancelled")
        return self.repository.update_job(
            replace(
                current,
                progress_stage=f"auditionsReady:{completed_count}/{len(AUDITION_KEYS)}",
            )
        )

    @staticmethod
    def _selected_memory_version_ids(
        *,
        output: Mapping[str, Any],
        facts: Sequence[Mapping[str, Any]],
    ) -> tuple[str, ...]:
        plan = output.get("plan")
        values = plan.get("memoryVersionIds") if isinstance(plan, Mapping) else None
        if not isinstance(values, list):
            raise NarrativeGenerationError("selection_manifest_invalid:missingMemoryVersionIds")
        selected = tuple(str(value or "").strip() for value in values)
        minimum = 2 if len(facts) >= 2 else 1
        if not minimum <= len(selected) <= min(3, len(facts)):
            raise NarrativeGenerationError(
                "selection_manifest_invalid:memoryVersionCount"
            )
        if any(not value for value in selected) or len(set(selected)) != len(selected):
            raise NarrativeGenerationError(
                "selection_manifest_invalid:duplicateOrBlankMemoryVersionId"
            )
        available = {
            str(item.get("memoryVersionId") or "").strip()
            for item in facts
        }
        if not set(selected).issubset(available):
            raise NarrativeGenerationError(
                "selection_manifest_invalid:memoryOutsideSnapshot"
            )
        return selected

    def _validate_and_build(
        self,
        project: NarrativeProjectRecord,
        job: NarrativeJobRecord,
        refs: Sequence[Any],
        output: Mapping[str, Any],
        selected_memory_version_ids: Sequence[str] | None = None,
        expected_audition_keys: Sequence[str] | None = None,
    ) -> list[NarrativeArtifactRecord]:
        raw = output.get("artifacts")
        if not isinstance(raw, list) or not raw:
            raise NarrativeGenerationError("provider output has no artifacts")
        audition_keys = tuple(expected_audition_keys or AUDITION_KEYS)
        if job.job_type == "auditions" and len(raw) != len(audition_keys):
            raise NarrativeGenerationError("audition artifact count is invalid")
        selected_set = (
            frozenset(str(value) for value in selected_memory_version_ids)
            if selected_memory_version_ids is not None
            else None
        )
        ledger = {
            ref.memory_version_id: FactLedgerEntry(
                memory_version_id=ref.memory_version_id,
                content_hash=ref.content_hash,
                text=_canonical(ref.content),
                supports_direct_quote=bool(ref.content.get("quote")),
                supports_psychology_or_causality=(
                    ref.memory_kind in {"emotion", "relationship"}
                    or any(
                        key in ref.content
                        for key in ("emotion", "feeling", "motivation", "cause", "reason")
                    )
                ),
                uncertain=ref.epistemic_status in {"inferred", "uncertain"},
            )
            for ref in refs
            if selected_set is None or ref.memory_version_id in selected_set
        }
        built: list[NarrativeArtifactRecord] = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise NarrativeGenerationError("artifact output must be an object")
            key = str(item.get("key") or "").strip()
            text = str(item.get("text") or "").strip() or None
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            artifact_type = self._artifact_type(job)
            if job.job_type == "auditions" and (
                key != audition_keys[index]
                or text is None
                or not 200 <= _text_length(text) <= 300
            ):
                text_length = _text_length(text or "")
                raise NarrativeGenerationError(
                    "audition_contract_invalid:"
                    f"index={index + 1},keyMatch={key == AUDITION_KEYS[index]},"
                    f"textLength={text_length}"
                )
            if job.job_type == "goldenSample" and (
                text is None or not 500 <= _text_length(text) <= 800
            ):
                raise NarrativeGenerationError(
                    "golden sample must contain 500-800 characters"
                )
            if job.job_type == "chapter":
                expected_key = str(job.input_payload.get("chapterKey") or "").strip()
                if key != expected_key or text is None:
                    raise NarrativeGenerationError(
                        "chapter output must match the requested chapter key and contain text"
                    )
            claims = self._claims(payload, text)
            if job.job_type == "auditions" and selected_set is not None:
                cited_set = frozenset(
                    memory_version_id
                    for claim in claims
                    for memory_version_id in claim.memory_version_ids
                )
                if cited_set != selected_set:
                    raise NarrativeGenerationError(
                        "audition_selection_mismatch:"
                        f"index={index + 1},expectedCount={len(selected_set)},"
                        f"citedCount={len(cited_set)}"
                    )
            validate_claims(
                claims=claims,
                ledger=ledger,
                project_type=project.project_type,
                narrator_type=project.narrator_type,
            )
            prior = self.repository.list_artifacts(
                project_id=project.project_id, artifact_type=artifact_type
            )
            same = [value for value in prior if value.artifact_key == key]
            parent = max(same, key=lambda value: value.version_number) if same else None
            normalized = dict(payload)
            if job.job_type == "auditions" and selected_set is not None:
                manifest = self.repository.get_selection_manifest(
                    project_id=project.project_id,
                    job_id=job.job_id,
                )
                if manifest is None:
                    raise NarrativeGenerationError("selection_manifest_missing")
                normalized["selectionManifestId"] = manifest.manifest_id
                normalized["selectionHash"] = manifest.selection_hash
                normalized["generationJobId"] = job.job_id
            if job.job_type == "outline":
                normalized["nodes"] = NarrativeCommandService._validated_outline_nodes(
                    payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
                )
            if job.job_type == "chapter":
                normalized["title"] = str(
                    payload.get("title") or job.input_payload.get("title") or key
                ).strip()
                try:
                    normalized["order"] = int(
                        payload.get("order") or job.input_payload.get("order") or 1
                    )
                except (TypeError, ValueError) as exc:
                    raise NarrativeGenerationError("chapter order must be an integer") from exc
            normalized["claims"] = [
                {
                    "claimId": claim.claim_id,
                    "text": claim.text,
                    "memoryVersionIds": list(claim.memory_version_ids),
                }
                for claim in claims
            ]
            built.append(
                NarrativeArtifactRecord(
                    artifact_version_id=str(uuid4()),
                    project_id=project.project_id,
                    artifact_type=artifact_type,
                    artifact_key=key,
                    version_number=(parent.version_number + 1 if parent else 1),
                    parent_version_id=(parent.artifact_version_id if parent else None),
                    memory_snapshot_id=job.memory_snapshot_id,
                    state=NarrativeArtifactState.READY_FOR_REVIEW,
                    content_text=text,
                    payload=normalized,
                    content_hash=_digest({"text": text, "payload": normalized}),
                    origin="generated",
                    model_id=self.provider.model_id,
                    prompt_version=self._prompt_version,
                    pipeline_version=self._pipeline_version,
                    created_at=_now(),
                )
            )
        return built

    @property
    def _prompt_version(self) -> str:
        return str(getattr(self.provider, "prompt_version", PROMPT_VERSION))

    @property
    def _pipeline_version(self) -> str:
        return str(getattr(self.provider, "pipeline_version", PIPELINE_VERSION))

    @staticmethod
    def _claims(
        payload: Mapping[str, Any], text: str | None
    ) -> tuple[NarrativeClaim, ...]:
        values = payload.get("paragraphs")
        if not isinstance(values, list):
            values = payload.get("nodes")
        if not isinstance(values, list):
            values = []
        claims = []
        for index, item in enumerate(values):
            if not isinstance(item, Mapping):
                continue
            refs = item.get("memoryVersionIds")
            claims.append(
                NarrativeClaim(
                    claim_id=str(
                        item.get("paragraphId")
                        or item.get("chapterKey")
                        or f"claim-{index + 1}"
                    ),
                    text=str(
                        item.get("text")
                        or item.get("intent")
                        or item.get("title")
                        or ""
                    ).strip(),
                    memory_version_ids=(
                        tuple(str(value) for value in refs)
                        if isinstance(refs, list)
                        else ()
                    ),
                    direct_quote=bool(item.get("directQuote")),
                    uncertain=bool(item.get("uncertain")),
                    psychology_or_causality=bool(item.get("psychologyOrCausality")),
                )
            )
        if not claims and text:
            raise NarrativeGenerationError(
                "text artifact requires paragraph-level memory references"
            )
        return tuple(claims)

    @staticmethod
    def _artifact_type(job: NarrativeJobRecord) -> NarrativeArtifactType:
        if job.job_type == "auditions":
            return NarrativeArtifactType.WRITING_AUDITION
        if job.job_type == "goldenSample":
            return NarrativeArtifactType.GOLDEN_SAMPLE
        if job.job_type == "outline":
            return NarrativeArtifactType.OUTLINE
        if job.job_type == "chapter":
            return NarrativeArtifactType.CHAPTER
        try:
            return NarrativeArtifactType(str(job.input_payload["artifactType"]))
        except (KeyError, ValueError) as exc:
            raise NarrativeGenerationError("revision requires artifactType") from exc

    def _advanced_project(
        self,
        project: NarrativeProjectRecord,
        job: NarrativeJobRecord,
        artifacts: Sequence[NarrativeArtifactRecord],
    ) -> NarrativeProjectRecord:
        if job.job_type == "auditions":
            update = replace(project, state=BookProjectState.AUDITIONS_READY)
        elif job.job_type == "goldenSample":
            update = replace(project, state=BookProjectState.GOLDEN_SAMPLE_REVIEW)
        elif job.job_type == "outline":
            update = replace(
                project,
                state=BookProjectState.OUTLINE_REVIEW,
                current_outline_id=artifacts[0].artifact_version_id,
            )
        else:
            update = project
        return update

    def _stage(
        self, job: NarrativeJobRecord, state: NarrativeJobState, *, finished: bool = False
    ) -> NarrativeJobRecord:
        current = self.repository.get_job(project_id=job.project_id, job_id=job.job_id)
        if current.state is NarrativeJobState.CANCELLED:
            raise NarrativeJobCancelled("job was cancelled")
        JOB_TRANSITIONS.require(current.state, state)
        return self.repository.update_job(
            replace(
                current,
                state=state,
                progress_stage=state.value,
                finished_at=(_now() if finished else None),
            )
        )

    def _failed(
        self, job: NarrativeJobRecord, code: str, *, retryable: bool
    ) -> NarrativeJobRecord:
        current = self.repository.get_job(project_id=job.project_id, job_id=job.job_id)
        if current.state is NarrativeJobState.CANCELLED:
            return current
        return self.repository.update_job(
            replace(
                current,
                state=NarrativeJobState.FAILED,
                progress_stage="failed",
                attempt_count=current.attempt_count + 1,
                error_code=code,
                retryable=retryable,
                finished_at=_now(),
            )
        )


__all__ = [
    "DisabledNarrativeProvider",
    "GENERATION_STAGES",
    "NarrativeCommandService",
    "NarrativeGenerationError",
    "NarrativeGenerationProcessor",
    "NarrativeProvider",
    "NarrativeProviderUnavailable",
    "StagedNarrativeProvider",
    "PIPELINE_VERSION",
    "PROMPT_VERSION",
]

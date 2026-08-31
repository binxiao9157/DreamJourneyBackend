"""Typed HTTP surface for private autobiography and Ta Story projects."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.domain.narrative.contracts import (
    BookProjectType,
    NarrativeArtifactType,
    NarrativeCommandEnvelope,
    NarrativeCommandType,
    NarrativeContractError,
    NarrativeNarratorType,
    NarrativeScope,
)
from app.services.narrative_generation import (
    NarrativeCommandService,
    NarrativeGenerationError,
)
from app.services.narrative_project import (
    NarrativeProjectAccessDenied,
    NarrativeProjectConflict,
    NarrativeProjectError,
    NarrativeProjectNotFound,
    NarrativeProjectService,
    NarrativeReadinessInsufficient,
)
from app.services.narrative_reader import NarrativeReaderError, NarrativeReaderService


class NarrativeProjectCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject_persona_id: str = Field(alias="subjectPersonaId")
    project_type: BookProjectType = Field(alias="projectType")
    narrator_type: NarrativeNarratorType = Field(alias="narratorType")
    title: str


ScopeResolver = Callable[[Request, str], Any]
GenerationAvailability = Callable[[], bool]


def create_narrative_router(
    *,
    store: Any,
    resolve_owner_context: ScopeResolver,
    generation_available: GenerationAvailability | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v2/vaults/{vault_id}/narrative-projects", tags=["narrative"])

    def scope(request: Request, vault_id: str, subject_persona_id: Optional[str]) -> NarrativeScope:
        context = resolve_owner_context(request, vault_id)
        vault_reader = getattr(store, "get_owner_truth_vault", None)
        vault = vault_reader(vault_id) if callable(vault_reader) else None
        authority_epoch = int((vault or {}).get("authorityEpoch") or 0)
        return NarrativeScope(
            vault_id=vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            subject_persona_id=(str(subject_persona_id or context.owner_subject_id).strip()),
            authority_epoch=authority_epoch,
            authorization_capture=context.authorization_capture,
        )

    def repository() -> Any:
        factory = getattr(store, "narrative_repository", None)
        if not callable(factory):
            raise HTTPException(status_code=503, detail=_error("capability_unavailable", "Narrative storage is unavailable", True))
        return factory()

    @router.get("")
    def list_projects(
        request: Request,
        vault_id: str,
        subjectPersonaId: Optional[str] = None,
        projectType: Optional[BookProjectType] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        with _uow(store, f"narrative-list:{vault_id}", None):
            values = repository().list_projects(scope=value_scope)
        if projectType is not None:
            values = tuple(item for item in values if item.project_type is projectType)
        return {"schemaVersion": "narrative-project-list-v1", "projects": [item.public_contract() for item in values]}

    @router.post("", status_code=201)
    def create_project(
        request: Request, vault_id: str, body: NarrativeProjectCreateBody
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, body.subject_persona_id)
        try:
            with _uow(store, f"narrative-create:{vault_id}", None):
                project = NarrativeProjectService(repository(), store).create_or_get(
                    scope=value_scope,
                    project_type=body.project_type,
                    narrator_type=body.narrator_type,
                    title=body.title,
                )
            return project.public_contract()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}")
    def get_project(
        request: Request, vault_id: str, project_id: str, subjectPersonaId: Optional[str] = None
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-get:{project_id}", None):
                repo = repository()
                project = NarrativeProjectService(repo, store).synchronize_memory_status(
                    scope=value_scope, project_id=project_id
                )
                artifacts = repo.list_artifacts(project_id=project_id)
            value = dict(project.public_contract())
            can_generate = generation_available is None or generation_available()
            value["generationAvailable"] = can_generate
            value["availableActions"] = _available_actions(
                project.state.value, generation_available=can_generate
            )
            value["artifacts"] = [item.public_contract() for item in artifacts]
            return value
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}/readiness")
    def readiness(
        request: Request, vault_id: str, project_id: str, subjectPersonaId: Optional[str] = None
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-readiness:{project_id}", None):
                result = NarrativeProjectService(repository(), store).readiness(
                    scope=value_scope, project_id=project_id
                )
            result["generationAvailable"] = (
                generation_available is None or generation_available()
            )
            return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/{project_id}/commands")
    def command(
        request: Request,
        vault_id: str,
        project_id: str,
        body: dict[str, Any],
        subjectPersonaId: Optional[str] = None,
    ) -> Response:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            envelope = NarrativeCommandEnvelope.from_mapping(body)
            if (
                envelope.command_type
                in {
                    NarrativeCommandType.CONFIRM_SETUP,
                    NarrativeCommandType.GENERATE_AUDITIONS,
                    NarrativeCommandType.SELECT_AUDITION,
                    NarrativeCommandType.GENERATE_GOLDEN_SAMPLE,
                    NarrativeCommandType.GENERATE_OUTLINE,
                    NarrativeCommandType.GENERATE_CHAPTER,
                    NarrativeCommandType.REVISE_OUTLINE,
                    NarrativeCommandType.REVISE_CHAPTER,
                }
                and generation_available is not None
                and not generation_available()
            ):
                raise HTTPException(
                    status_code=503,
                    detail=_error(
                        "capability_unavailable",
                        "Narrative generation is temporarily unavailable",
                        True,
                    ),
                )
            with _uow(store, f"narrative-command:{project_id}", envelope.command_id):
                result = NarrativeCommandService(repository(), store).execute(
                    scope=value_scope, project_id=project_id, command=envelope
                )
            status = 202 if result.get("accepted") else 200
            return _json_response(result, status)
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise exc
            raise _http_error(exc) from exc

    @router.get("/{project_id}/jobs/{job_id}")
    def job(
        request: Request, vault_id: str, project_id: str, job_id: str,
        subjectPersonaId: Optional[str] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-job:{job_id}", None):
                repo = repository()
                repo.get_project(scope=value_scope, project_id=project_id)
                return repo.get_job(project_id=project_id, job_id=job_id).public_contract()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/{project_id}/jobs/{job_id}/cancel")
    def cancel_job(
        request: Request, vault_id: str, project_id: str, job_id: str,
        subjectPersonaId: Optional[str] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-job-cancel:{job_id}", None):
                repo = repository()
                repo.get_project(scope=value_scope, project_id=project_id)
                return repo.cancel_job(project_id=project_id, job_id=job_id).public_contract()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}/artifacts")
    def artifacts(
        request: Request, vault_id: str, project_id: str,
        artifactType: Optional[NarrativeArtifactType] = None,
        subjectPersonaId: Optional[str] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-artifacts:{project_id}", None):
                repo = repository()
                repo.get_project(scope=value_scope, project_id=project_id)
                values = repo.list_artifacts(
                    project_id=project_id, artifact_type=artifactType
                )
            return {
                "schemaVersion": "narrative-artifact-list-v1",
                "artifacts": [item.public_contract() for item in values],
            }
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}/artifacts/{artifact_version_id}")
    def artifact(
        request: Request, vault_id: str, project_id: str, artifact_version_id: str,
        subjectPersonaId: Optional[str] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-artifact:{artifact_version_id}", None):
                repo = repository()
                repo.get_project(scope=value_scope, project_id=project_id)
                return repo.get_artifact(
                    project_id=project_id, artifact_version_id=artifact_version_id
                ).public_contract()
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}/reader/manifest")
    def reader_manifest(
        request: Request, vault_id: str, project_id: str, subjectPersonaId: Optional[str] = None
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-reader:{project_id}", None):
                repo = repository()
                NarrativeProjectService(repo, store).synchronize_memory_status(
                    scope=value_scope, project_id=project_id
                )
                return NarrativeReaderService(repo).manifest(
                    scope=value_scope, project_id=project_id
                )
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}/reader/chapters/{chapter_key}")
    def reader_chapter(
        request: Request, vault_id: str, project_id: str, chapter_key: str,
        subjectPersonaId: Optional[str] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-reader:{project_id}:{chapter_key}", None):
                repo = repository()
                NarrativeProjectService(repo, store).synchronize_memory_status(
                    scope=value_scope, project_id=project_id
                )
                return NarrativeReaderService(repo).chapter(
                    scope=value_scope, project_id=project_id, chapter_key=chapter_key
                )
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/{project_id}/export")
    def export(
        request: Request, vault_id: str, project_id: str,
        format: str = Query(default="txt", pattern="^(txt|json)$"),
        subjectPersonaId: Optional[str] = None,
    ) -> Response:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-export:{project_id}", None):
                repo = repository()
                NarrativeProjectService(repo, store).synchronize_memory_status(
                    scope=value_scope, project_id=project_id
                )
                body, media_type = NarrativeReaderService(repo).export(
                    scope=value_scope, project_id=project_id, format=format
                )
            return Response(content=body, media_type=media_type, headers={"Cache-Control": "no-store, private"})
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.delete("/{project_id}")
    def delete_project(
        request: Request, vault_id: str, project_id: str,
        expectedProjectVersion: int = Query(ge=0), subjectPersonaId: Optional[str] = None,
    ) -> Mapping[str, Any]:
        value_scope = scope(request, vault_id, subjectPersonaId)
        try:
            with _uow(store, f"narrative-delete:{project_id}", None):
                deleted = repository().delete_project(
                    scope=value_scope, project_id=project_id,
                    expected_version=expectedProjectVersion,
                )
            return {"schemaVersion": "narrative-delete-v1", "projectId": project_id,
                    "state": deleted.state.value, "formalMemoryDeleted": False}
        except Exception as exc:
            raise _http_error(exc) from exc

    return router


def _uow(store: Any, correlation_id: str, command_id: str | None):
    return store.request_unit_of_work(correlation_id=correlation_id, command_id=command_id)


def _json_response(value: Mapping[str, Any], status_code: int) -> Response:
    import json

    return Response(
        content=json.dumps(value, ensure_ascii=False), status_code=status_code,
        media_type="application/json", headers={"Cache-Control": "no-store"},
    )


def _error(code: str, message: str, retryable: bool, version: int | None = None) -> Mapping[str, Any]:
    value: dict[str, Any] = {
        "schemaVersion": "narrative-error-v1", "errorCode": code,
        "message": message, "retryable": retryable,
    }
    if version is not None:
        value["currentProjectVersion"] = version
    return value


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NarrativeProjectAccessDenied):
        return HTTPException(status_code=403, detail=_error("release_policy_denied", "Access denied", False))
    if isinstance(exc, NarrativeProjectNotFound):
        return HTTPException(status_code=404, detail=_error("resource_not_found", "Resource not found", False))
    if isinstance(exc, NarrativeProjectConflict):
        code = str(exc) if str(exc) in {"project_version_conflict", "invalid_state", "memory_snapshot_stale"} else "invalid_state"
        return HTTPException(
            status_code=409,
            detail=_error(
                code,
                "Project state changed; refresh and retry",
                True,
                getattr(exc, "current_project_version", None),
            ),
        )
    if isinstance(exc, NarrativeReadinessInsufficient):
        return HTTPException(status_code=409, detail=_error("memory_snapshot_stale", str(exc), True))
    if isinstance(exc, (NarrativeContractError, NarrativeGenerationError, NarrativeReaderError, NarrativeProjectError, ValueError)):
        return HTTPException(status_code=422, detail=_error("contract_invalid", str(exc), False))
    return HTTPException(status_code=503, detail=_error("capability_unavailable", "Narrative service is unavailable", True))


def _available_actions(state: str, *, generation_available: bool = True) -> list[str]:
    actions = {
        "notStarted": ["checkReadiness"],
        "needsMoreMemory": ["checkReadiness", "returnToEcho"],
        "readyForConfirmation": ["confirmSetup", "pauseProject"],
        "generatingAuditions": ["viewProgress"],
        "auditionsReady": ["selectAudition", "generateAuditions", "pauseProject"],
        "generatingGoldenSample": ["viewProgress"],
        "goldenSampleReview": ["submitArtifactFeedback", "confirmGoldenSample", "pauseProject"],
        "toneConfirmed": ["generateOutline", "pauseProject"],
        "outlineReview": ["reviseOutline", "editArtifact", "restoreArtifactVersion", "confirmOutline", "pauseProject"],
        "writing": ["generateChapter", "reviseChapter", "editArtifact", "restoreArtifactVersion", "finalizeChapter", "read", "export", "pauseProject", "archiveProject"],
        "updateAvailable": ["adoptMemoryUpdate", "ignoreMemoryUpdate", "editArtifact", "restoreArtifactVersion", "read", "export", "pauseProject", "archiveProject"],
        "paused": ["resumeProject", "archiveProject"],
    }.get(state, [])
    if generation_available:
        return actions
    generation_actions = {
        "confirmSetup",
        "generateAuditions",
        "selectAudition",
        "generateGoldenSample",
        "generateOutline",
        "reviseOutline",
        "generateChapter",
        "reviseChapter",
    }
    return [value for value in actions if value not in generation_actions]


__all__ = ["create_narrative_router"]

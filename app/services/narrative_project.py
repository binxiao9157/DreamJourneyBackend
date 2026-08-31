"""Narrative project persistence, readiness and immutable MemorySnapshot service."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Iterable, Mapping, Protocol
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.domain.narrative.contracts import (
    BookProjectState,
    BookProjectType,
    NarrativeArtifactRecord,
    NarrativeArtifactState,
    NarrativeArtifactType,
    NarrativeContractError,
    NarrativeJobRecord,
    NarrativeJobState,
    NarrativeMemoryRef,
    NarrativeNarratorType,
    NarrativeProjectRecord,
    NarrativeScope,
    NarrativeSnapshotRecord,
)
from app.domain.narrative.state_machine import JOB_TRANSITIONS, PROJECT_TRANSITIONS
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)


class NarrativeProjectError(ValueError):
    pass


class NarrativeProjectNotFound(NarrativeProjectError):
    pass


class NarrativeProjectConflict(NarrativeProjectError):
    def __init__(self, message: str, *, current_project_version: int | None = None) -> None:
        super().__init__(message)
        self.current_project_version = current_project_version


class NarrativeProjectAccessDenied(NarrativeProjectError):
    pass


class NarrativeReadinessInsufficient(NarrativeProjectError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _terminal_job_recovery_state(
    *, job_type: str, project_state: BookProjectState
) -> BookProjectState | None:
    if job_type == "auditions" and project_state is BookProjectState.GENERATING_AUDITIONS:
        return BookProjectState.READY_FOR_CONFIRMATION
    if (
        job_type == "goldenSample"
        and project_state is BookProjectState.GENERATING_GOLDEN_SAMPLE
    ):
        return BookProjectState.AUDITIONS_READY
    return None


class NarrativeRepository(Protocol):
    def list_projects(self, *, scope: NarrativeScope) -> tuple[NarrativeProjectRecord, ...]: ...
    def create_or_get_project(self, project: NarrativeProjectRecord) -> NarrativeProjectRecord: ...
    def get_project(self, *, scope: NarrativeScope, project_id: str) -> NarrativeProjectRecord: ...
    def get_project_for_worker(self, *, project_id: str) -> NarrativeProjectRecord: ...
    def save_project(
        self, project: NarrativeProjectRecord, *, expected_version: int
    ) -> NarrativeProjectRecord: ...
    def delete_project(
        self, *, scope: NarrativeScope, project_id: str, expected_version: int
    ) -> NarrativeProjectRecord: ...
    def save_snapshot(self, snapshot: NarrativeSnapshotRecord) -> NarrativeSnapshotRecord: ...
    def get_snapshot(self, *, project_id: str, snapshot_id: str) -> NarrativeSnapshotRecord: ...
    def append_artifact(self, artifact: NarrativeArtifactRecord) -> NarrativeArtifactRecord: ...
    def append_artifacts(
        self, artifacts: Iterable[NarrativeArtifactRecord]
    ) -> tuple[NarrativeArtifactRecord, ...]: ...
    def publish_generation_result(
        self,
        *,
        project: NarrativeProjectRecord,
        expected_project_version: int,
        artifacts: Iterable[NarrativeArtifactRecord],
        completed_job: NarrativeJobRecord,
    ) -> NarrativeJobRecord: ...
    def list_artifacts(
        self, *, project_id: str, artifact_type: NarrativeArtifactType | None = None
    ) -> tuple[NarrativeArtifactRecord, ...]: ...
    def get_artifact(self, *, project_id: str, artifact_version_id: str) -> NarrativeArtifactRecord: ...
    def mark_artifacts_stale_by_memory_versions(
        self, *, project_id: str, memory_version_ids: Iterable[str]
    ) -> tuple[str, ...]: ...
    def save_job(self, job: NarrativeJobRecord) -> NarrativeJobRecord: ...
    def get_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord: ...
    def update_job(self, job: NarrativeJobRecord) -> NarrativeJobRecord: ...
    def claim_jobs(self, *, limit: int = 1) -> tuple[NarrativeJobRecord, ...]: ...
    def acknowledge_job(self, *, job_id: str) -> None: ...
    def defer_job(self, *, job_id: str, delay_seconds: int) -> None: ...
    def cancel_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord: ...
    def dead_letter_job(self, *, job: NarrativeJobRecord) -> None: ...
    def record_decision(
        self,
        *,
        project: NarrativeProjectRecord,
        command_id: str,
        expected_project_version: int,
        decision_type: str,
        actor_subject_id: str,
        payload: Mapping[str, Any],
        target_artifact_version_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class InMemoryNarrativeRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._projects: dict[str, NarrativeProjectRecord] = {}
        self._snapshots: dict[str, NarrativeSnapshotRecord] = {}
        self._artifacts: dict[str, NarrativeArtifactRecord] = {}
        self._jobs: dict[str, NarrativeJobRecord] = {}
        self._pending_job_ids: set[str] = set()
        self._dead_letter_job_ids: set[str] = set()
        self._decisions: dict[tuple[str, str], Mapping[str, Any]] = {}

    def list_projects(self, *, scope: NarrativeScope) -> tuple[NarrativeProjectRecord, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._projects.values()
                if item.scope.vault_id == scope.vault_id
                and item.scope.owner_subject_id == scope.owner_subject_id
                and item.scope.subject_persona_id == scope.subject_persona_id
                and item.state not in {BookProjectState.DELETED}
            )

    def create_or_get_project(self, project: NarrativeProjectRecord) -> NarrativeProjectRecord:
        with self._lock:
            for existing in self._projects.values():
                if (
                    existing.scope.vault_id == project.scope.vault_id
                    and existing.scope.subject_persona_id == project.scope.subject_persona_id
                    and existing.project_type == project.project_type
                    and existing.state not in {BookProjectState.ARCHIVED, BookProjectState.DELETED}
                ):
                    return existing
            self._projects[project.project_id] = project
            return project

    def get_project(self, *, scope: NarrativeScope, project_id: str) -> NarrativeProjectRecord:
        with self._lock:
            project = self._projects.get(project_id)
            if project is None or project.scope.vault_id != scope.vault_id:
                raise NarrativeProjectNotFound("narrative project was not found")
            if project.scope.owner_subject_id != scope.owner_subject_id:
                raise NarrativeProjectAccessDenied("narrative project access denied")
            return project

    def get_project_for_worker(self, *, project_id: str) -> NarrativeProjectRecord:
        with self._lock:
            project = self._projects.get(project_id)
            if project is None:
                raise NarrativeProjectNotFound("narrative project was not found")
            return project

    def save_project(
        self, project: NarrativeProjectRecord, *, expected_version: int
    ) -> NarrativeProjectRecord:
        with self._lock:
            current = self._projects.get(project.project_id)
            if current is None:
                raise NarrativeProjectNotFound("narrative project was not found")
            if current.project_version != expected_version:
                raise NarrativeProjectConflict(
                    "project_version_conflict",
                    current_project_version=current.project_version,
                )
            PROJECT_TRANSITIONS.require(current.state, project.state)
            saved = replace(project, project_version=expected_version + 1, updated_at=_now())
            self._projects[project.project_id] = saved
            return saved

    def delete_project(
        self, *, scope: NarrativeScope, project_id: str, expected_version: int
    ) -> NarrativeProjectRecord:
        project = self.get_project(scope=scope, project_id=project_id)
        if project.project_version != expected_version:
            raise NarrativeProjectConflict(
                "project_version_conflict",
                current_project_version=project.project_version,
            )
        deleted = replace(
            project,
            state=BookProjectState.DELETED,
            project_version=expected_version + 1,
            updated_at=_now(),
        )
        with self._lock:
            self._projects[project_id] = deleted
        return deleted

    def save_snapshot(self, snapshot: NarrativeSnapshotRecord) -> NarrativeSnapshotRecord:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._snapshots.values()
                    if item.project_id == snapshot.project_id
                    and item.snapshot_hash == snapshot.snapshot_hash
                ),
                None,
            )
            if existing is not None:
                return existing
            self._snapshots[snapshot.snapshot_id] = snapshot
            return snapshot

    def get_snapshot(self, *, project_id: str, snapshot_id: str) -> NarrativeSnapshotRecord:
        with self._lock:
            value = self._snapshots.get(snapshot_id)
            if value is None or value.project_id != project_id:
                raise NarrativeProjectNotFound("memory snapshot was not found")
            return value

    def append_artifact(self, artifact: NarrativeArtifactRecord) -> NarrativeArtifactRecord:
        with self._lock:
            existing = self._artifacts.get(artifact.artifact_version_id)
            if existing is not None:
                if existing != artifact:
                    raise NarrativeProjectConflict("artifact id was reused")
                return existing
            prior = [
                item
                for item in self._artifacts.values()
                if item.project_id == artifact.project_id
                and item.artifact_type == artifact.artifact_type
                and item.artifact_key == artifact.artifact_key
            ]
            if prior and artifact.version_number != max(item.version_number for item in prior) + 1:
                raise NarrativeProjectConflict("artifact version is not append-only")
            self._artifacts[artifact.artifact_version_id] = artifact
            return artifact

    def append_artifacts(
        self, artifacts: Iterable[NarrativeArtifactRecord]
    ) -> tuple[NarrativeArtifactRecord, ...]:
        values = tuple(artifacts)
        with self._lock:
            snapshot = dict(self._artifacts)
            try:
                return tuple(self.append_artifact(item) for item in values)
            except Exception:
                self._artifacts = snapshot
                raise

    def publish_generation_result(
        self,
        *,
        project: NarrativeProjectRecord,
        expected_project_version: int,
        artifacts: Iterable[NarrativeArtifactRecord],
        completed_job: NarrativeJobRecord,
    ) -> NarrativeJobRecord:
        values = tuple(artifacts)
        with self._lock:
            current_project = self._projects.get(project.project_id)
            current_job = self._jobs.get(completed_job.job_id)
            if current_project is None:
                raise NarrativeProjectNotFound("narrative project was not found")
            if current_project.project_version != expected_project_version:
                raise NarrativeProjectConflict(
                    "project_version_conflict",
                    current_project_version=current_project.project_version,
                )
            if current_job is None or current_job.project_id != project.project_id:
                raise NarrativeProjectNotFound("generation job was not found")
            if current_job.state is NarrativeJobState.CANCELLED:
                raise NarrativeProjectConflict("job_cancelled")
            JOB_TRANSITIONS.require(current_job.state, completed_job.state)
            PROJECT_TRANSITIONS.require(current_project.state, project.state)
            artifact_snapshot = dict(self._artifacts)
            project_snapshot = dict(self._projects)
            job_snapshot = dict(self._jobs)
            try:
                self.append_artifacts(values)
                self.save_project(project, expected_version=expected_project_version)
                self.update_job(completed_job)
            except Exception:
                self._artifacts = artifact_snapshot
                self._projects = project_snapshot
                self._jobs = job_snapshot
                raise
            return self._jobs[completed_job.job_id]

    def list_artifacts(
        self, *, project_id: str, artifact_type: NarrativeArtifactType | None = None
    ) -> tuple[NarrativeArtifactRecord, ...]:
        with self._lock:
            values = [
                item
                for item in self._artifacts.values()
                if item.project_id == project_id
                and (artifact_type is None or item.artifact_type == artifact_type)
            ]
            return tuple(sorted(values, key=lambda item: (item.artifact_key, item.version_number)))

    def get_artifact(self, *, project_id: str, artifact_version_id: str) -> NarrativeArtifactRecord:
        with self._lock:
            value = self._artifacts.get(artifact_version_id)
            if value is None or value.project_id != project_id:
                raise NarrativeProjectNotFound("artifact was not found")
            return value

    def mark_artifacts_stale_by_memory_versions(
        self, *, project_id: str, memory_version_ids: Iterable[str]
    ) -> tuple[str, ...]:
        removed = frozenset(str(value) for value in memory_version_ids)
        stale_ids: list[str] = []
        with self._lock:
            for artifact_id, artifact in tuple(self._artifacts.items()):
                claims = artifact.payload.get("claims")
                refs = {
                    str(ref)
                    for claim in claims if isinstance(claim, Mapping)
                    for ref in (claim.get("memoryVersionIds") or [])
                } if isinstance(claims, list) else set()
                if refs.intersection(removed) and artifact.state not in {
                    NarrativeArtifactState.STALE,
                    NarrativeArtifactState.SUPERSEDED,
                }:
                    self._artifacts[artifact_id] = replace(
                        artifact, state=NarrativeArtifactState.STALE
                    )
                    stale_ids.append(artifact_id)
        return tuple(stale_ids)

    def save_job(self, job: NarrativeJobRecord) -> NarrativeJobRecord:
        with self._lock:
            for existing in self._jobs.values():
                if (
                    existing.project_id == job.project_id
                    and existing.command_id == job.command_id
                    and existing.job_type == job.job_type
                ):
                    return existing
            self._jobs[job.job_id] = job
            self._pending_job_ids.add(job.job_id)
            return job

    def get_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord:
        with self._lock:
            value = self._jobs.get(job_id)
            if value is None or value.project_id != project_id:
                raise NarrativeProjectNotFound("generation job was not found")
            return value

    def update_job(self, job: NarrativeJobRecord) -> NarrativeJobRecord:
        with self._lock:
            if job.job_id not in self._jobs:
                raise NarrativeProjectNotFound("generation job was not found")
            self._jobs[job.job_id] = job
            return job

    def claim_jobs(self, *, limit: int = 1) -> tuple[NarrativeJobRecord, ...]:
        with self._lock:
            values = [
                item for item in self._jobs.values()
                if item.job_id in self._pending_job_ids
                and (
                    item.state == NarrativeJobState.QUEUED
                    or (
                        item.state == NarrativeJobState.FAILED
                        and item.retryable
                        and item.attempt_count < item.max_attempts
                    )
                )
            ]
            claimed = tuple(
                replace(item, state=NarrativeJobState.SNAPSHOTTING, progress_stage="snapshotting")
                for item in sorted(values, key=lambda item: item.created_at)[: max(1, limit)]
            )
            for item in claimed:
                self._jobs[item.job_id] = item
            return claimed

    def acknowledge_job(self, *, job_id: str) -> None:
        with self._lock:
            self._pending_job_ids.discard(job_id)

    def defer_job(self, *, job_id: str, delay_seconds: int) -> None:
        if delay_seconds < 0:
            raise NarrativeContractError("delay_seconds must not be negative")
        with self._lock:
            if job_id not in self._jobs:
                raise NarrativeProjectNotFound("generation job was not found")
            self._pending_job_ids.add(job_id)

    def cancel_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.project_id != project_id:
                raise NarrativeProjectNotFound("generation job was not found")
            if job.state in {
                NarrativeJobState.READY_FOR_REVIEW,
                NarrativeJobState.NEEDS_ECHO,
                NarrativeJobState.CANCELLED,
                NarrativeJobState.SUPERSEDED,
            }:
                return job
            cancelled = replace(
                job,
                state=NarrativeJobState.CANCELLED,
                progress_stage="cancelled",
                retryable=False,
                finished_at=_now(),
            )
            self._jobs[job_id] = cancelled
            self._pending_job_ids.discard(job_id)
            project = self._projects.get(project_id)
            if project is not None:
                target = _terminal_job_recovery_state(
                    job_type=job.job_type, project_state=project.state
                )
                if target is not None:
                    self.save_project(
                        replace(project, state=target),
                        expected_version=project.project_version,
                    )
            return cancelled

    def dead_letter_job(self, *, job: NarrativeJobRecord) -> None:
        with self._lock:
            self._dead_letter_job_ids.add(job.job_id)
            self._pending_job_ids.discard(job.job_id)
            project = self._projects.get(job.project_id)
            if project is not None:
                target = _terminal_job_recovery_state(
                    job_type=job.job_type, project_state=project.state
                )
                if target is not None:
                    self.save_project(
                        replace(project, state=target),
                        expected_version=project.project_version,
                    )

    def record_decision(
        self,
        *,
        project: NarrativeProjectRecord,
        command_id: str,
        expected_project_version: int,
        decision_type: str,
        actor_subject_id: str,
        payload: Mapping[str, Any],
        target_artifact_version_id: str | None = None,
    ) -> Mapping[str, Any]:
        key = (project.scope.vault_id, command_id)
        value = {
            "commandId": command_id,
            "decisionType": decision_type,
            "expectedProjectVersion": expected_project_version,
            "actorSubjectId": actor_subject_id,
            "targetArtifactVersionId": target_artifact_version_id,
            "payloadHash": _digest(payload),
        }
        with self._lock:
            existing = self._decisions.get(key)
            if existing is not None and existing != value:
                raise NarrativeProjectConflict("commandId was reused with different content")
            self._decisions[key] = value
            return value


class PostgresNarrativeRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @contextmanager
    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - guarded by production dependencies
            dict_row = None
        with self._connection.cursor(row_factory=dict_row) as cursor:
            yield cursor

    @staticmethod
    def _json(value: Any) -> Any:
        return value if isinstance(value, (dict, list)) else json.loads(value or "{}")

    @staticmethod
    def _project(row: Mapping[str, Any]) -> NarrativeProjectRecord:
        return NarrativeProjectRecord(
            project_id=str(row["id"]),
            scope=NarrativeScope(
                vault_id=str(row["vault_id"]),
                owner_subject_id=str(row["owner_subject_id"]),
                actor_subject_id=str(row["owner_subject_id"]),
                subject_persona_id=str(row["subject_persona_id"]),
                authority_epoch=int(row["authority_epoch"]),
            ),
            project_type=BookProjectType(str(row["project_type"])),
            narrator_type=NarrativeNarratorType(str(row["narrator_type"])),
            title=str(row["title"]),
            state=BookProjectState(str(row["state"])),
            project_version=int(row["optimistic_version"]),
            privacy_state=str(row["privacy_state"]),
            current_memory_snapshot_id=(str(row["current_memory_snapshot_id"]) if row.get("current_memory_snapshot_id") else None),
            current_golden_sample_id=(str(row["current_golden_sample_id"]) if row.get("current_golden_sample_id") else None),
            current_constitution_id=(str(row["current_constitution_id"]) if row.get("current_constitution_id") else None),
            current_outline_id=(str(row["current_outline_id"]) if row.get("current_outline_id") else None),
            ignored_memory_fingerprint=row.get("ignored_memory_fingerprint"),
            writing_context=PostgresNarrativeRepository._json(row.get("writing_context") or {}),
            paused_from_state=(
                BookProjectState(str(row["paused_from_state"]))
                if row.get("paused_from_state") else None
            ),
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
        )

    def list_projects(self, *, scope: NarrativeScope) -> tuple[NarrativeProjectRecord, ...]:
        with self._cursor() as cursor:
            cursor.execute(
                """SELECT * FROM narrative.book_projects
                WHERE vault_id=%s AND owner_subject_id=%s AND subject_persona_id=%s
                  AND state <> 'deleted' ORDER BY updated_at DESC""",
                (scope.vault_id, scope.owner_subject_id, scope.subject_persona_id),
            )
            return tuple(self._project(row) for row in cursor.fetchall())

    def create_or_get_project(self, project: NarrativeProjectRecord) -> NarrativeProjectRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """SELECT * FROM narrative.book_projects
                WHERE vault_id=%s AND subject_persona_id=%s AND project_type=%s
                  AND state NOT IN ('archived','deleted') FOR UPDATE""",
                (project.scope.vault_id, project.scope.subject_persona_id, project.project_type.value),
            )
            existing = cursor.fetchone()
            if existing:
                return self._project(existing)
            cursor.execute(
                """INSERT INTO narrative.book_projects (
                    id,vault_id,owner_subject_id,subject_persona_id,project_type,narrator_type,
                    title,state,privacy_state,writing_context,authority_epoch,
                    optimistic_version,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'private',%s,%s,0,%s,%s) RETURNING *""",
                (
                    project.project_id, project.scope.vault_id, project.scope.owner_subject_id,
                    project.scope.subject_persona_id, project.project_type.value,
                    project.narrator_type.value, project.title, project.state.value,
                    Jsonb(dict(project.writing_context)), project.scope.authority_epoch,
                    project.created_at, project.updated_at,
                ),
            )
            return self._project(cursor.fetchone())

    def get_project(self, *, scope: NarrativeScope, project_id: str) -> NarrativeProjectRecord:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM narrative.book_projects WHERE vault_id=%s AND id=%s AND owner_subject_id=%s",
                (scope.vault_id, project_id, scope.owner_subject_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectNotFound("narrative project was not found")
        return self._project(row)

    def get_project_for_worker(self, *, project_id: str) -> NarrativeProjectRecord:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM narrative.book_projects WHERE id=%s", (project_id,))
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectNotFound("narrative project was not found")
        return self._project(row)

    def save_project(
        self, project: NarrativeProjectRecord, *, expected_version: int
    ) -> NarrativeProjectRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.book_projects SET state=%s,narrator_type=%s,title=%s,
                    current_memory_snapshot_id=%s,current_golden_sample_id=%s,
                    current_constitution_id=%s,current_outline_id=%s,
                    ignored_memory_fingerprint=%s,writing_context=%s,paused_from_state=%s,
                    optimistic_version=optimistic_version+1,updated_at=NOW()
                WHERE vault_id=%s AND id=%s AND optimistic_version=%s RETURNING *""",
                (
                    project.state.value, project.narrator_type.value, project.title,
                    project.current_memory_snapshot_id, project.current_golden_sample_id,
                    project.current_constitution_id, project.current_outline_id,
                    project.ignored_memory_fingerprint, Jsonb(dict(project.writing_context)),
                    project.paused_from_state.value if project.paused_from_state else None,
                    project.scope.vault_id, project.project_id, expected_version,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            with self._cursor() as cursor:
                cursor.execute(
                    "SELECT optimistic_version FROM narrative.book_projects WHERE id=%s",
                    (project.project_id,),
                )
                current = cursor.fetchone()
            raise NarrativeProjectConflict(
                "project_version_conflict",
                current_project_version=(
                    int(current["optimistic_version"]) if current is not None else None
                ),
            )
        return self._project(row)

    def delete_project(
        self, *, scope: NarrativeScope, project_id: str, expected_version: int
    ) -> NarrativeProjectRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.book_projects
                SET state='deleted', optimistic_version=optimistic_version+1,
                    deleted_at=NOW(), updated_at=NOW()
                WHERE vault_id=%s AND owner_subject_id=%s AND id=%s
                  AND optimistic_version=%s AND state <> 'deleted'
                RETURNING *""",
                (scope.vault_id, scope.owner_subject_id, project_id, expected_version),
            )
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectConflict("project_version_conflict")
        return self._project(row)

    def save_snapshot(self, snapshot: NarrativeSnapshotRecord) -> NarrativeSnapshotRecord:
        refs = [item.public_contract(include_content=True) for item in snapshot.memory_refs]
        with self._cursor() as cursor:
            cursor.execute(
                """INSERT INTO narrative.memory_snapshots (
                    id,project_id,vault_id,authority_epoch,memory_version_refs,writing_context,
                    source_fingerprint,snapshot_hash,created_by,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (project_id,snapshot_hash) DO UPDATE SET snapshot_hash=EXCLUDED.snapshot_hash
                RETURNING *""",
                (
                    snapshot.snapshot_id, snapshot.project_id, snapshot.vault_id,
                    snapshot.authority_epoch, Jsonb(refs), Jsonb(dict(snapshot.writing_context)),
                    snapshot.source_fingerprint,
                    snapshot.snapshot_hash, snapshot.created_by, snapshot.created_at,
                ),
            )
            return self._snapshot(cursor.fetchone())

    @classmethod
    def _snapshot(cls, row: Mapping[str, Any]) -> NarrativeSnapshotRecord:
        refs = cls._json(row["memory_version_refs"])
        return NarrativeSnapshotRecord(
            snapshot_id=str(row["id"]), project_id=str(row["project_id"]),
            vault_id=str(row["vault_id"]), authority_epoch=int(row["authority_epoch"]),
            memory_refs=tuple(
                NarrativeMemoryRef(
                    memory_id=item["memoryId"], memory_version_id=item["memoryVersionId"],
                    content_hash=item["contentHash"], content=item.get("content") or {},
                    memory_kind=item["memoryKind"], perspective_type=item["perspectiveType"],
                    epistemic_status=item["epistemicStatus"], sensitivity=item["sensitivity"],
                ) for item in refs
            ),
            source_fingerprint=str(row["source_fingerprint"]), snapshot_hash=str(row["snapshot_hash"]),
            created_by=str(row["created_by"]), created_at=row["created_at"].isoformat(),
            writing_context=cls._json(row.get("writing_context") or {}),
        )

    def get_snapshot(self, *, project_id: str, snapshot_id: str) -> NarrativeSnapshotRecord:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM narrative.memory_snapshots WHERE project_id=%s AND id=%s",
                (project_id, snapshot_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectNotFound("memory snapshot was not found")
        return self._snapshot(row)

    @staticmethod
    def _artifact(row: Mapping[str, Any]) -> NarrativeArtifactRecord:
        return NarrativeArtifactRecord(
            artifact_version_id=str(row["id"]), project_id=str(row["project_id"]),
            artifact_type=NarrativeArtifactType(str(row["artifact_type"])),
            artifact_key=str(row["artifact_key"]), version_number=int(row["version_number"]),
            parent_version_id=(str(row["parent_version_id"]) if row.get("parent_version_id") else None),
            memory_snapshot_id=str(row["memory_snapshot_id"]),
            state=NarrativeArtifactState(str(row["state"])), content_text=row.get("content_text"),
            payload=PostgresNarrativeRepository._json(row["payload"]),
            content_hash=str(row["content_hash"]), origin=str(row["origin"]),
            model_id=row.get("model_id"), prompt_version=row.get("prompt_version"),
            pipeline_version=row.get("pipeline_version"), created_at=row["created_at"].isoformat(),
        )

    def append_artifact(self, artifact: NarrativeArtifactRecord) -> NarrativeArtifactRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.artifact_versions SET is_current=FALSE,state=CASE
                    WHEN state='final' THEN state ELSE 'superseded' END
                WHERE project_id=%s AND artifact_type=%s AND artifact_key=%s AND is_current""",
                (artifact.project_id, artifact.artifact_type.value, artifact.artifact_key),
            )
            cursor.execute(
                """INSERT INTO narrative.artifact_versions (
                    id,project_id,vault_id,artifact_type,artifact_key,version_number,parent_version_id,
                    memory_snapshot_id,state,content_text,payload,schema_version,content_hash,
                    model_id,prompt_version,pipeline_version,origin,is_current,created_at
                ) SELECT %s,%s,vault_id,%s,%s,%s,%s,%s,%s,%s,%s,'narrative-artifact-v1',
                    %s,%s,%s,%s,%s,TRUE,%s FROM narrative.book_projects
                    WHERE id=%s RETURNING *""",
                (
                    artifact.artifact_version_id, artifact.project_id, artifact.artifact_type.value,
                    artifact.artifact_key, artifact.version_number, artifact.parent_version_id,
                    artifact.memory_snapshot_id, artifact.state.value, artifact.content_text,
                    Jsonb(dict(artifact.payload)), artifact.content_hash, artifact.model_id,
                    artifact.prompt_version, artifact.pipeline_version, artifact.origin,
                    artifact.created_at, artifact.project_id,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                self._insert_artifact_memory_refs(cursor, artifact)
        if row is None:
            raise NarrativeProjectNotFound("narrative project was not found")
        return self._artifact(row)

    def _insert_artifact_memory_refs(
        self, cursor: Any, artifact: NarrativeArtifactRecord
    ) -> None:
        cursor.execute(
            "SELECT memory_version_refs FROM narrative.memory_snapshots WHERE id=%s AND project_id=%s",
            (artifact.memory_snapshot_id, artifact.project_id),
        )
        snapshot_row = cursor.fetchone()
        if snapshot_row is None:
            raise NarrativeProjectNotFound("memory snapshot was not found")
        refs = self._json(snapshot_row["memory_version_refs"])
        by_version = {
            str(item.get("memoryVersionId")): item
            for item in refs if isinstance(item, Mapping)
        }
        claims = artifact.payload.get("claims")
        if not isinstance(claims, list):
            return
        anchor_type = (
            "outlineNode"
            if artifact.artifact_type is NarrativeArtifactType.OUTLINE
            else "paragraph"
        )
        for index, claim in enumerate(claims):
            if not isinstance(claim, Mapping):
                continue
            anchor_id = str(claim.get("claimId") or f"claim-{index + 1}")
            claim_hash = _digest({
                "anchorId": anchor_id,
                "text": str(claim.get("text") or ""),
                "memoryVersionIds": claim.get("memoryVersionIds") or [],
            })
            version_ids = claim.get("memoryVersionIds")
            if not isinstance(version_ids, list):
                continue
            for version_id in version_ids:
                ref = by_version.get(str(version_id))
                if ref is None:
                    raise NarrativeProjectConflict("artifact references memory outside snapshot")
                cursor.execute(
                    """INSERT INTO narrative.artifact_memory_refs (
                        artifact_version_id,vault_id,memory_id,memory_version_id,
                        content_hash,anchor_type,anchor_id,claim_hash
                    ) SELECT %s,vault_id,%s,%s,%s,%s,%s,%s
                        FROM narrative.artifact_versions WHERE id=%s
                    ON CONFLICT DO NOTHING""",
                    (
                        artifact.artifact_version_id,
                        ref["memoryId"], ref["memoryVersionId"], ref["contentHash"],
                        anchor_type, anchor_id, claim_hash, artifact.artifact_version_id,
                    ),
                )

    def append_artifacts(
        self, artifacts: Iterable[NarrativeArtifactRecord]
    ) -> tuple[NarrativeArtifactRecord, ...]:
        return tuple(self.append_artifact(item) for item in artifacts)

    def publish_generation_result(
        self,
        *,
        project: NarrativeProjectRecord,
        expected_project_version: int,
        artifacts: Iterable[NarrativeArtifactRecord],
        completed_job: NarrativeJobRecord,
    ) -> NarrativeJobRecord:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM narrative.generation_jobs WHERE project_id=%s AND id=%s FOR UPDATE",
                (project.project_id, completed_job.job_id),
            )
            current_job = cursor.fetchone()
        if current_job is None:
            raise NarrativeProjectNotFound("generation job was not found")
        current_state = NarrativeJobState(str(current_job["state"]))
        if current_state is NarrativeJobState.CANCELLED:
            raise NarrativeProjectConflict("job_cancelled")
        JOB_TRANSITIONS.require(current_state, completed_job.state)
        self.append_artifacts(tuple(artifacts))
        self.save_project(project, expected_version=expected_project_version)
        return self.update_job(completed_job)

    def list_artifacts(
        self, *, project_id: str, artifact_type: NarrativeArtifactType | None = None
    ) -> tuple[NarrativeArtifactRecord, ...]:
        params: list[Any] = [project_id]
        where = "project_id=%s"
        if artifact_type:
            where += " AND artifact_type=%s"
            params.append(artifact_type.value)
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM narrative.artifact_versions WHERE {where} ORDER BY artifact_key,version_number",
                tuple(params),
            )
            return tuple(self._artifact(row) for row in cursor.fetchall())

    def get_artifact(self, *, project_id: str, artifact_version_id: str) -> NarrativeArtifactRecord:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM narrative.artifact_versions WHERE project_id=%s AND id=%s",
                (project_id, artifact_version_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectNotFound("artifact was not found")
        return self._artifact(row)

    def mark_artifacts_stale_by_memory_versions(
        self, *, project_id: str, memory_version_ids: Iterable[str]
    ) -> tuple[str, ...]:
        values = tuple(str(value) for value in memory_version_ids)
        if not values:
            return ()
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.artifact_versions AS artifacts
                    SET state='stale',is_current=FALSE
                    WHERE artifacts.project_id=%s
                      AND artifacts.state NOT IN ('stale','superseded')
                      AND EXISTS (
                        SELECT 1 FROM narrative.artifact_memory_refs AS refs
                        WHERE refs.artifact_version_id=artifacts.id
                          AND refs.memory_version_id=ANY(%s::uuid[])
                      )
                    RETURNING artifacts.id""",
                (project_id, list(values)),
            )
            return tuple(str(row["id"]) for row in cursor.fetchall())

    @staticmethod
    def _job(row: Mapping[str, Any]) -> NarrativeJobRecord:
        return NarrativeJobRecord(
            job_id=str(row["id"]), project_id=str(row["project_id"]), job_type=str(row["job_type"]),
            state=NarrativeJobState(str(row["state"])), memory_snapshot_id=str(row["memory_snapshot_id"]),
            command_id=str(row["command_id"]), idempotency_key=str(row["idempotency_key"]),
            expected_project_version=int(row["expected_project_version"]),
            progress_stage=str(row["progress_stage"]), attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]), error_code=row.get("error_code"),
            retryable=bool(row["retryable"]), created_at=row["created_at"].isoformat(),
            input_payload=PostgresNarrativeRepository._json(row["input_payload"]),
            finished_at=(row["finished_at"].isoformat() if row.get("finished_at") else None),
        )

    def save_job(self, job: NarrativeJobRecord) -> NarrativeJobRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """INSERT INTO narrative.generation_jobs (
                    id,project_id,vault_id,job_type,state,memory_snapshot_id,command_id,
                    idempotency_key,expected_project_version,progress_stage,attempt_count,
                    max_attempts,error_code,retryable,input_payload,created_at
                ) SELECT %s,%s,vault_id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    FROM narrative.book_projects WHERE id=%s
                ON CONFLICT (project_id,command_id,job_type) DO UPDATE SET command_id=EXCLUDED.command_id
                RETURNING *""",
                (
                    job.job_id, job.project_id, job.job_type, job.state.value,
                    job.memory_snapshot_id, job.command_id, job.idempotency_key,
                    job.expected_project_version, job.progress_stage, job.attempt_count,
                    job.max_attempts, job.error_code, job.retryable, Jsonb(dict(job.input_payload)),
                    job.created_at, job.project_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise NarrativeProjectNotFound("narrative project was not found")
            cursor.execute(
                """INSERT INTO narrative.generation_outbox (
                    id,vault_id,job_id,event_key
                ) VALUES (%s,%s,%s,%s) ON CONFLICT (vault_id,job_id) DO NOTHING""",
                (str(uuid4()), row["vault_id"], row["id"], f"narrative-job:{row['id']}"),
            )
            return self._job(row)

    def get_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM narrative.generation_jobs WHERE project_id=%s AND id=%s", (project_id, job_id))
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectNotFound("generation job was not found")
        return self._job(row)

    def update_job(self, job: NarrativeJobRecord) -> NarrativeJobRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.generation_jobs SET state=%s,progress_stage=%s,
                    attempt_count=%s,error_code=%s,retryable=%s,finished_at=%s
                    WHERE id=%s RETURNING *""",
                (job.state.value, job.progress_stage, job.attempt_count, job.error_code,
                 job.retryable, job.finished_at, job.job_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise NarrativeProjectNotFound("generation job was not found")
        return self._job(row)

    def claim_jobs(self, *, limit: int = 1) -> tuple[NarrativeJobRecord, ...]:
        with self._cursor() as cursor:
            cursor.execute(
                """WITH selected AS (
                    SELECT jobs.id FROM narrative.generation_jobs AS jobs
                    JOIN narrative.generation_outbox AS outbox ON outbox.job_id=jobs.id
                    WHERE outbox.state='pending' AND outbox.available_at<=NOW()
                      AND (
                        jobs.state='queued'
                        OR (jobs.state='failed' AND jobs.retryable
                            AND jobs.attempt_count<jobs.max_attempts)
                        OR (jobs.state NOT IN ('readyForReview','needsEcho','failed','cancelled','superseded')
                            AND jobs.lease_expires_at<NOW())
                      )
                    ORDER BY outbox.created_at FOR UPDATE OF outbox SKIP LOCKED LIMIT %s
                )
                UPDATE narrative.generation_jobs AS jobs
                SET state='snapshotting',progress_stage='snapshotting',started_at=NOW(),
                    finished_at=NULL,lease_owner='narrative-worker',
                    lease_expires_at=NOW()+INTERVAL '5 minutes'
                FROM selected WHERE jobs.id=selected.id RETURNING jobs.*""",
                (max(1, limit),),
            )
            return tuple(self._job(row) for row in cursor.fetchall())

    def acknowledge_job(self, *, job_id: str) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.generation_outbox SET state='delivered',delivered_at=NOW()
                    WHERE job_id=%s AND state='pending'""",
                (job_id,),
            )
            cursor.execute(
                "UPDATE narrative.generation_jobs SET lease_owner=NULL,lease_expires_at=NULL WHERE id=%s",
                (job_id,),
            )

    def defer_job(self, *, job_id: str, delay_seconds: int) -> None:
        if delay_seconds < 0:
            raise NarrativeContractError("delay_seconds must not be negative")
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.generation_outbox SET available_at=NOW()+(%s * INTERVAL '1 second'),
                    attempt_count=attempt_count+1 WHERE job_id=%s AND state='pending'""",
                (delay_seconds, job_id),
            )
            cursor.execute(
                "UPDATE narrative.generation_jobs SET lease_owner=NULL,lease_expires_at=NULL WHERE id=%s",
                (job_id,),
            )

    def cancel_job(self, *, project_id: str, job_id: str) -> NarrativeJobRecord:
        with self._cursor() as cursor:
            cursor.execute(
                """UPDATE narrative.generation_jobs
                SET state='cancelled',progress_stage='cancelled',retryable=FALSE,
                    finished_at=NOW(),lease_owner=NULL,lease_expires_at=NULL
                WHERE project_id=%s AND id=%s
                  AND state NOT IN ('readyForReview','needsEcho','cancelled','superseded')
                RETURNING *""",
                (project_id, job_id),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT * FROM narrative.generation_jobs WHERE project_id=%s AND id=%s",
                    (project_id, job_id),
                )
                row = cursor.fetchone()
            if row is None:
                raise NarrativeProjectNotFound("generation job was not found")
            cursor.execute(
                """UPDATE narrative.generation_outbox SET state='delivered',delivered_at=NOW()
                WHERE job_id=%s AND state='pending'""",
                (job_id,),
            )
            cursor.execute("SELECT state FROM narrative.book_projects WHERE id=%s", (project_id,))
            project_row = cursor.fetchone()
            if project_row is None:
                raise NarrativeProjectNotFound("narrative project was not found")
            recovery_state = _terminal_job_recovery_state(
                job_type=str(row["job_type"]),
                project_state=BookProjectState(str(project_row["state"])),
            )
            if recovery_state is not None:
                cursor.execute(
                    """UPDATE narrative.book_projects
                    SET state=%s,optimistic_version=optimistic_version+1,updated_at=NOW()
                    WHERE id=%s""",
                    (recovery_state.value, project_id),
                )
            return self._job(row)

    def dead_letter_job(self, *, job: NarrativeJobRecord) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """INSERT INTO narrative.generation_dead_letters (
                    id,vault_id,job_id,error_code,attempt_count
                ) SELECT %s,vault_id,id,%s,%s FROM narrative.generation_jobs WHERE id=%s
                ON CONFLICT (vault_id,job_id) DO NOTHING""",
                (str(uuid4()), job.error_code or "unknown", job.attempt_count, job.job_id),
            )
            cursor.execute(
                "SELECT state FROM narrative.book_projects WHERE id=%s",
                (job.project_id,),
            )
            project_row = cursor.fetchone()
            if project_row is None:
                raise NarrativeProjectNotFound("narrative project was not found")
            recovery_state = _terminal_job_recovery_state(
                job_type=job.job_type,
                project_state=BookProjectState(str(project_row["state"])),
            )
            if recovery_state is not None:
                cursor.execute(
                    """UPDATE narrative.book_projects
                    SET state=%s,optimistic_version=optimistic_version+1,updated_at=NOW()
                    WHERE id=%s""",
                    (recovery_state.value, job.project_id),
                )

    def record_decision(
        self, *, project: NarrativeProjectRecord, command_id: str,
        expected_project_version: int, decision_type: str, actor_subject_id: str,
        payload: Mapping[str, Any], target_artifact_version_id: str | None = None,
    ) -> Mapping[str, Any]:
        payload_hash = _digest(payload)
        with self._cursor() as cursor:
            cursor.execute(
                """INSERT INTO narrative.project_decisions (
                    id,project_id,vault_id,command_id,expected_project_version,decision_type,
                    actor_subject_id,target_artifact_version_id,payload,payload_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (vault_id,command_id) DO NOTHING
                RETURNING command_id,expected_project_version,decision_type,actor_subject_id,
                    target_artifact_version_id,payload_hash""",
                (str(uuid4()), project.project_id, project.scope.vault_id, command_id,
                 expected_project_version, decision_type, actor_subject_id,
                 target_artifact_version_id, Jsonb(dict(payload)), payload_hash),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """SELECT command_id,expected_project_version,decision_type,actor_subject_id,
                        target_artifact_version_id,payload_hash
                        FROM narrative.project_decisions WHERE vault_id=%s AND command_id=%s""",
                    (project.scope.vault_id, command_id),
                )
                row = cursor.fetchone()
        if row is None or (
            int(row["expected_project_version"]) != expected_project_version
            or str(row["decision_type"]) != decision_type
            or str(row["actor_subject_id"]) != actor_subject_id
            or (str(row["target_artifact_version_id"]) if row.get("target_artifact_version_id") else None)
                != target_artifact_version_id
            or str(row["payload_hash"]) != payload_hash
        ):
            raise NarrativeProjectConflict("commandId was reused with different content")
        return {"commandId": str(row["command_id"]), "decisionType": row["decision_type"], "payloadHash": row["payload_hash"]}


class NarrativeProjectService:
    def __init__(self, repository: NarrativeRepository, formal_memory_store: Any) -> None:
        self.repository = repository
        self.formal_memory_store = formal_memory_store

    @staticmethod
    def _authorize(scope: NarrativeScope, project_type: BookProjectType, narrator: NarrativeNarratorType) -> None:
        if scope.actor_subject_id != scope.owner_subject_id:
            raise NarrativeProjectAccessDenied("only the Vault controller may manage a book project")
        if project_type is BookProjectType.SELF_AUTOBIOGRAPHY:
            if scope.subject_persona_id != scope.owner_subject_id or narrator is not NarrativeNarratorType.SELF_FIRST_PERSON:
                raise NarrativeProjectAccessDenied("Self autobiography requires the Owner first-person scope")
        elif narrator is NarrativeNarratorType.SELF_FIRST_PERSON:
            raise NarrativeProjectAccessDenied("Ta Story cannot impersonate TA in first person")

    def create_or_get(
        self, *, scope: NarrativeScope, project_type: BookProjectType,
        narrator_type: NarrativeNarratorType, title: str,
    ) -> NarrativeProjectRecord:
        self._authorize(scope, project_type, narrator_type)
        instant = _now()
        return self.repository.create_or_get_project(
            NarrativeProjectRecord(
                project_id=str(uuid4()), scope=scope, project_type=project_type,
                narrator_type=narrator_type, title=title, state=BookProjectState.NOT_STARTED,
                project_version=0, privacy_state="private", created_at=instant, updated_at=instant,
            )
        )

    def readiness(self, *, scope: NarrativeScope, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        refs = list(self._current_refs(scope))
        update = self._synchronize_snapshot(project, refs)
        project = update[0]
        clusters = self._story_clusters(refs)
        ready = bool(refs)
        target = BookProjectState.READY_FOR_CONFIRMATION if ready else BookProjectState.NEEDS_MORE_MEMORY
        if project.state in {BookProjectState.NOT_STARTED, BookProjectState.CHECKING_READINESS, BookProjectState.NEEDS_MORE_MEMORY}:
            intermediate = project
            if project.state is BookProjectState.NOT_STARTED:
                intermediate = self.repository.save_project(
                    replace(project, state=BookProjectState.CHECKING_READINESS),
                    expected_version=project.project_version,
                )
            project = self.repository.save_project(
                replace(intermediate, state=target), expected_version=intermediate.project_version
            )
        return {
            "schemaVersion": "narrative-readiness-v1",
            "project": project.public_contract(),
            "ready": ready,
            "availableMemoryCount": len(refs),
            "storyClusters": clusters,
            "memoryUpdate": update[1],
            "gaps": [] if ready else [
                {"code": "needsConcreteEvent", "blocking": True, "echoPrompt": "请讲一段发生过的具体经历。"}
            ],
        }

    def synchronize_memory_status(
        self, *, scope: NarrativeScope, project_id: str
    ) -> NarrativeProjectRecord:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        return self._synchronize_snapshot(project, list(self._current_refs(scope)))[0]

    def adopt_memory_update(
        self, *, scope: NarrativeScope, project_id: str, expected_version: int
    ) -> tuple[NarrativeProjectRecord, NarrativeSnapshotRecord]:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        if project.project_version != expected_version:
            raise NarrativeProjectConflict("project_version_conflict")
        if project.state is not BookProjectState.UPDATE_AVAILABLE:
            raise NarrativeProjectConflict("invalid_state")
        refs = self._current_refs(scope)
        if not refs:
            raise NarrativeReadinessInsufficient("formal memories are unavailable")
        snapshot = self._save_snapshot(project, scope, refs)
        saved = self.repository.save_project(
            replace(
                project,
                state=BookProjectState.WRITING,
                current_memory_snapshot_id=snapshot.snapshot_id,
                ignored_memory_fingerprint=None,
            ),
            expected_version=project.project_version,
        )
        return saved, snapshot

    def ignore_memory_update(
        self, *, scope: NarrativeScope, project_id: str, expected_version: int
    ) -> NarrativeProjectRecord:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        if project.project_version != expected_version:
            raise NarrativeProjectConflict("project_version_conflict")
        if project.state is not BookProjectState.UPDATE_AVAILABLE:
            raise NarrativeProjectConflict("invalid_state")
        refs = self._current_refs(scope)
        current_fingerprint = _digest(sorted(item.memory_version_id for item in refs))
        return self.repository.save_project(
            replace(
                project,
                state=BookProjectState.WRITING,
                ignored_memory_fingerprint=current_fingerprint,
            ),
            expected_version=project.project_version,
        )

    def confirm_setup(
        self, *, scope: NarrativeScope, project_id: str, expected_version: int,
        selected_memory_version_ids: Iterable[str], narrator_type: NarrativeNarratorType,
        writing_context: Mapping[str, Any],
    ) -> tuple[NarrativeProjectRecord, NarrativeSnapshotRecord]:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        self._authorize(scope, project.project_type, narrator_type)
        if project.project_version != expected_version:
            raise NarrativeProjectConflict("project_version_conflict")
        if project.state is not BookProjectState.READY_FOR_CONFIRMATION:
            raise NarrativeProjectConflict("invalid_state")
        selected = frozenset(selected_memory_version_ids)
        memories = self._current_refs(scope)
        refs = tuple(
            item for item in memories
            if not selected or item.memory_version_id in selected
        )
        if not refs or (selected and {item.memory_version_id for item in refs} != selected):
            raise NarrativeReadinessInsufficient("selected formal memories are unavailable")
        snapshot = self._save_snapshot(
            project,
            scope,
            refs,
            writing_context=writing_context,
        )
        saved = self.repository.save_project(
            replace(project, narrator_type=narrator_type,
                    state=BookProjectState.GENERATING_AUDITIONS,
                    current_memory_snapshot_id=snapshot.snapshot_id,
                    writing_context=dict(writing_context)),
            expected_version=expected_version,
        )
        return saved, snapshot

    def replace_writing_material(
        self,
        *,
        scope: NarrativeScope,
        project_id: str,
        expected_version: int,
        selected_memory_version_ids: Iterable[str],
    ) -> tuple[NarrativeProjectRecord, NarrativeSnapshotRecord]:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        if project.project_version != expected_version:
            raise NarrativeProjectConflict(
                "project_version_conflict",
                current_project_version=project.project_version,
            )
        if project.state not in {
            BookProjectState.AUDITIONS_READY,
            BookProjectState.GOLDEN_SAMPLE_REVIEW,
        }:
            raise NarrativeProjectConflict("invalid_state")
        selected = frozenset(str(value) for value in selected_memory_version_ids)
        if not selected:
            raise NarrativeReadinessInsufficient("at least one formal memory is required")
        memories = self._current_refs(scope)
        refs = tuple(item for item in memories if item.memory_version_id in selected)
        if {item.memory_version_id for item in refs} != selected:
            raise NarrativeReadinessInsufficient("selected formal memories are unavailable")
        snapshot = self._save_snapshot(
            project,
            scope,
            refs,
            writing_context=project.writing_context,
        )
        saved = self.repository.save_project(
            replace(
                project,
                state=BookProjectState.GENERATING_AUDITIONS,
                current_memory_snapshot_id=snapshot.snapshot_id,
                current_golden_sample_id=None,
                current_constitution_id=None,
                current_outline_id=None,
            ),
            expected_version=project.project_version,
        )
        return saved, snapshot

    def _current_refs(self, scope: NarrativeScope) -> tuple[NarrativeMemoryRef, ...]:
        context = OwnerTruthCommandContext(
            vault_id=scope.vault_id,
            owner_subject_id=scope.owner_subject_id,
            actor_subject_id=scope.actor_subject_id,
        )
        page = OwnerTruthFormalMemoryService(self.formal_memory_store).list(
            context=context, query=OwnerTruthFormalMemoryQuery(limit=100)
        )
        return tuple(
            self._memory_ref(item)
            for item in page.items
            if item.sensitivity != "restricted"
        )

    def _save_snapshot(
        self,
        project: NarrativeProjectRecord,
        scope: NarrativeScope,
        refs: Iterable[NarrativeMemoryRef],
        writing_context: Mapping[str, Any] | None = None,
    ) -> NarrativeSnapshotRecord:
        values = tuple(refs)
        context = dict(writing_context if writing_context is not None else project.writing_context)
        material = [
            item.public_contract(include_content=True)
            for item in sorted(values, key=lambda value: value.memory_version_id)
        ]
        return self.repository.save_snapshot(
            NarrativeSnapshotRecord(
                snapshot_id=str(uuid4()),
                project_id=project.project_id,
                vault_id=scope.vault_id,
                authority_epoch=scope.authority_epoch,
                memory_refs=values,
                source_fingerprint=_digest(sorted(item.memory_version_id for item in values)),
                snapshot_hash=_digest({"memoryRefs": material, "writingContext": context}),
                created_by=scope.actor_subject_id,
                created_at=_now(),
                writing_context=context,
            )
        )

    def _synchronize_snapshot(
        self,
        project: NarrativeProjectRecord,
        refs: list[NarrativeMemoryRef],
    ) -> tuple[NarrativeProjectRecord, Mapping[str, Any] | None]:
        if project.current_memory_snapshot_id is None:
            return project, None
        snapshot = self.repository.get_snapshot(
            project_id=project.project_id,
            snapshot_id=project.current_memory_snapshot_id,
        )
        old_ids = {item.memory_version_id for item in snapshot.memory_refs}
        current_ids = {item.memory_version_id for item in refs}
        added = sorted(current_ids - old_ids)
        removed = sorted(old_ids - current_ids)
        stale_ids = self.repository.mark_artifacts_stale_by_memory_versions(
            project_id=project.project_id,
            memory_version_ids=removed,
        )
        current_fingerprint = _digest(sorted(current_ids))
        ignored = (
            bool(added)
            and not removed
            and project.ignored_memory_fingerprint == current_fingerprint
        )
        if (added or removed) and not ignored and project.state is BookProjectState.WRITING:
            project = self.repository.save_project(
                replace(project, state=BookProjectState.UPDATE_AVAILABLE),
                expected_version=project.project_version,
            )
        if (not added and not removed) or ignored:
            return project, None
        return project, {
            "addedMemoryVersionIds": added,
            "removedMemoryVersionIds": removed,
            "staleArtifactVersionIds": list(stale_ids),
            "requiresConfirmation": True,
        }

    @staticmethod
    def _memory_ref(memory: Any) -> NarrativeMemoryRef:
        version = memory.current_version
        return NarrativeMemoryRef(
            memory_id=memory.memory_id, memory_version_id=version.version_id,
            content_hash=version.content_hash, content=version.content,
            memory_kind=memory.memory_kind, perspective_type=memory.perspective_type,
            epistemic_status=memory.epistemic_status, sensitivity=memory.sensitivity,
        )

    @staticmethod
    def _story_clusters(refs: list[NarrativeMemoryRef]) -> list[dict[str, Any]]:
        by_kind: dict[str, list[NarrativeMemoryRef]] = {}
        for item in refs:
            by_kind.setdefault(item.memory_kind, []).append(item)
        return [
            {
                "clusterId": _digest([item.memory_version_id for item in values])[:16],
                "title": {"experience": "人生经历", "knowledge": "经验与认识", "emotion": "关系与感受"}.get(kind, "人生片段"),
                "memoryVersionIds": [item.memory_version_id for item in values[:12]],
                "memoryCount": len(values),
                "recommended": index == 0,
            }
            for index, (kind, values) in enumerate(sorted(by_kind.items()))
        ][:3]


__all__ = [
    "InMemoryNarrativeRepository", "NarrativeProjectAccessDenied",
    "NarrativeProjectConflict", "NarrativeProjectError", "NarrativeProjectNotFound",
    "NarrativeProjectService", "NarrativeReadinessInsufficient", "NarrativeRepository",
    "PostgresNarrativeRepository",
]

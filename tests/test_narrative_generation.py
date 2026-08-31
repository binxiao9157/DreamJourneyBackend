from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

from app.domain.narrative.contracts import (
    BookProjectState,
    BookProjectType,
    NarrativeArtifactState,
    NarrativeArtifactType,
    NarrativeJobRecord,
    NarrativeJobState,
    NarrativeMemoryRef,
    NarrativeNarratorType,
    NarrativeProjectRecord,
    NarrativeScope,
    NarrativeSnapshotRecord,
)
from app.services.narrative_generation import NarrativeGenerationProcessor
from app.services.narrative_project import InMemoryNarrativeRepository
from app.services.narrative_reader import NarrativeReaderService


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _fixture(*, epistemic_status="recalled"):
    repo = InMemoryNarrativeRepository()
    scope = NarrativeScope(
        vault_id="vault-1", owner_subject_id="owner-1", actor_subject_id="owner-1",
        subject_persona_id="owner-1", authority_epoch=1,
    )
    project = NarrativeProjectRecord(
        project_id=str(uuid4()), scope=scope,
        project_type=BookProjectType.SELF_AUTOBIOGRAPHY,
        narrator_type=NarrativeNarratorType.SELF_FIRST_PERSON,
        title="我的自传", state=BookProjectState.GENERATING_AUDITIONS,
        project_version=0, privacy_state="private",
        created_at="2026-08-30T00:00:00+00:00", updated_at="2026-08-30T00:00:00+00:00",
    )
    project = repo.create_or_get_project(project)
    content = {"event": "我在北方的一所大学学习计算机。"}
    ref = NarrativeMemoryRef(
        memory_id=str(uuid4()), memory_version_id=str(uuid4()), content_hash=_hash(content),
        content=content, memory_kind="experience", perspective_type="ownerRecalled",
        epistemic_status=epistemic_status, sensitivity="normal",
    )
    snapshot = repo.save_snapshot(NarrativeSnapshotRecord(
        snapshot_id=str(uuid4()), project_id=project.project_id, vault_id="vault-1",
        authority_epoch=1, memory_refs=(ref,), source_fingerprint=_hash([ref.memory_version_id]),
        snapshot_hash=_hash(ref.public_contract(include_content=True)), created_by="owner-1",
        created_at="2026-08-30T00:00:00+00:00",
    ))
    project = repo.save_project(
        replace(project, current_memory_snapshot_id=snapshot.snapshot_id), expected_version=0
    )
    job = repo.save_job(NarrativeJobRecord(
        job_id=str(uuid4()), project_id=project.project_id, job_type="auditions",
        state=NarrativeJobState.QUEUED, memory_snapshot_id=snapshot.snapshot_id,
        command_id=str(uuid4()), idempotency_key="audition-test",
        expected_project_version=project.project_version, progress_stage="queued",
        attempt_count=0, max_attempts=3, input_payload={},
        created_at="2026-08-30T00:00:00+00:00",
    ))
    return repo, scope, project, ref, job


class _Provider:
    model_id = "fixture-writer"

    def __init__(self, memory_version_id, *, invalid=False):
        self.memory_version_id = str(uuid4()) if invalid else memory_version_id

    def generate(self, **_):
        paragraph = "我记得那段学习经历。" * 24
        paragraph = paragraph[:220]
        return {
            "artifacts": [
                {
                    "key": key,
                    "text": paragraph,
                    "payload": {"paragraphs": [{
                        "paragraphId": f"{key}-p1", "text": paragraph,
                        "memoryVersionIds": [self.memory_version_id],
                    }]},
                }
                for key in ("documentary", "warmReflection", "thoughtfulMemoir")
            ]
        }


class _ConflictingProvider(_Provider):
    def __init__(self, repository, project, memory_version_id):
        super().__init__(memory_version_id)
        self.repository = repository
        self.project = project

    def generate(self, **kwargs):
        self.repository.save_project(
            self.project,
            expected_version=self.project.project_version,
        )
        return super().generate(**kwargs)


def test_three_auditions_commit_from_one_snapshot():
    repo, _, project, ref, job = _fixture()
    result = NarrativeGenerationProcessor(repo, _Provider(ref.memory_version_id)).run_job(
        project_id=project.project_id, job_id=job.job_id
    )
    assert result.state is NarrativeJobState.READY_FOR_REVIEW
    artifacts = repo.list_artifacts(
        project_id=project.project_id, artifact_type=NarrativeArtifactType.WRITING_AUDITION
    )
    assert len(artifacts) == 3
    assert {item.memory_snapshot_id for item in artifacts} == {project.current_memory_snapshot_id}
    assert repo.get_project_for_worker(project_id=project.project_id).state is BookProjectState.AUDITIONS_READY


def test_unknown_memory_reference_rejects_entire_audition_group():
    repo, _, project, ref, job = _fixture()
    result = NarrativeGenerationProcessor(repo, _Provider(ref.memory_version_id, invalid=True)).run_job(
        project_id=project.project_id, job_id=job.job_id
    )
    assert result.state is NarrativeJobState.FAILED
    assert result.error_code == "unsupported_fact_detected"
    assert repo.list_artifacts(project_id=project.project_id) == ()


def test_inferred_memory_cannot_be_rendered_as_certain():
    repo, _, project, ref, job = _fixture(epistemic_status="inferred")
    result = NarrativeGenerationProcessor(repo, _Provider(ref.memory_version_id)).run_job(
        project_id=project.project_id, job_id=job.job_id
    )
    assert result.state is NarrativeJobState.FAILED
    assert result.error_code == "unsupported_fact_detected"
    assert repo.list_artifacts(project_id=project.project_id) == ()


def test_project_version_conflict_cannot_leave_partially_published_artifacts():
    repo, _, project, ref, job = _fixture()
    result = NarrativeGenerationProcessor(
        repo, _ConflictingProvider(repo, project, ref.memory_version_id)
    ).run_job(project_id=project.project_id, job_id=job.job_id)
    assert result.state is NarrativeJobState.FAILED
    assert result.error_code == "project_version_conflict"
    assert repo.list_artifacts(project_id=project.project_id) == ()


def test_reader_returns_only_final_or_confirmed_chapters():
    repo, scope, project, ref, _ = _fixture()
    base = dict(
        project_id=project.project_id, artifact_type=NarrativeArtifactType.CHAPTER,
        artifact_key="chapter-1", version_number=1,
        memory_snapshot_id=project.current_memory_snapshot_id, content_text="正文",
        payload={"title": "第一章", "order": 1, "paragraphs": [{"paragraphId": "p1", "text": "正文"}]},
        content_hash=_hash("正文"), origin="generated",
        created_at="2026-08-30T00:00:00+00:00",
    )
    from app.domain.narrative.contracts import NarrativeArtifactRecord

    repo.append_artifact(NarrativeArtifactRecord(
        artifact_version_id=str(uuid4()), state=NarrativeArtifactState.FINAL, **base
    ))
    manifest = NarrativeReaderService(repo).manifest(scope=scope, project_id=project.project_id)
    assert [item["chapterKey"] for item in manifest["chapters"]] == ["chapter-1"]

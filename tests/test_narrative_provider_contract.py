from dataclasses import replace
import unittest

from app.domain.narrative.contracts import NarrativeJobState
from app.services.narrative_generation import (
    GENERATION_STAGES,
    NarrativeGenerationProcessor,
    NarrativeProviderUnavailable,
)
from tests import test_narrative_generation as scenarios


class _UnavailableProvider:
    model_id = "unavailable"

    def generate(self, **_):
        raise NarrativeProviderUnavailable("provider timeout")


class _MalformedProvider:
    model_id = "malformed"

    def generate(self, **_):
        return {"artifacts": "not-a-list"}


class _WrongChapterProvider:
    model_id = "wrong-chapter"

    def __init__(self, memory_version_id):
        self.memory_version_id = memory_version_id

    def generate(self, **_):
        text = "我在北方求学。"
        return {
            "artifacts": [{
                "key": "chapter-not-requested",
                "text": text,
                "payload": {
                    "title": "错误章节",
                    "claims": [{
                        "claimId": "p1",
                        "text": text,
                        "memoryVersionIds": [self.memory_version_id],
                    }],
                },
            }]
        }


class _StagedProvider:
    model_id = "staged-fixture"

    def __init__(self, memory_version_id):
        self.memory_version_id = memory_version_id
        self.stages = []

    def generate_stage(self, *, stage, previous_output, **_):
        self.stages.append(stage)
        if stage != "antiAIEdit":
            return {"stage": stage, "previousStage": previous_output.get("stage")}
        return scenarios._Provider(self.memory_version_id).generate()


class NarrativeProviderContractTests(unittest.TestCase):
    def test_staged_provider_runs_the_declared_pipeline_before_fact_guard(self):
        repo, _, project, ref, job = scenarios._fixture()
        provider = _StagedProvider(ref.memory_version_id)
        result = NarrativeGenerationProcessor(repo, provider).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )
        self.assertEqual(result.state, NarrativeJobState.READY_FOR_REVIEW)
        self.assertEqual(tuple(provider.stages), GENERATION_STAGES)

    def test_provider_timeout_is_retryable_and_publishes_nothing(self):
        repo, _, project, _, job = scenarios._fixture()
        result = NarrativeGenerationProcessor(repo, _UnavailableProvider()).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )
        self.assertEqual(result.state, NarrativeJobState.FAILED)
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_code, "provider_unavailable")
        self.assertEqual(repo.list_artifacts(project_id=project.project_id), ())

    def test_unparseable_output_fails_closed(self):
        repo, _, project, _, job = scenarios._fixture()
        result = NarrativeGenerationProcessor(repo, _MalformedProvider()).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )
        self.assertEqual(result.state, NarrativeJobState.FAILED)
        self.assertFalse(result.retryable)
        self.assertEqual(repo.list_artifacts(project_id=project.project_id), ())

    def test_chapter_output_must_match_requested_outline_node(self):
        repo, _, project, ref, job = scenarios._fixture()
        chapter_job = repo.update_job(replace(
            job,
            job_type="chapter",
            input_payload={"chapterKey": "chapter-1", "title": "求学"},
        ))
        result = NarrativeGenerationProcessor(
            repo,
            _WrongChapterProvider(ref.memory_version_id),
        ).run_job(project_id=project.project_id, job_id=chapter_job.job_id)
        self.assertEqual(result.state, NarrativeJobState.FAILED)
        self.assertEqual(repo.list_artifacts(project_id=project.project_id), ())


if __name__ == "__main__":
    unittest.main()

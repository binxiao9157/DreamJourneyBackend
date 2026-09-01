import unittest
from dataclasses import replace

from tests import test_narrative_generation as scenarios
from app.domain.narrative.contracts import BookProjectState, NarrativeJobState
from app.services.narrative_generation import NarrativeGenerationProcessor


class NarrativeGenerationTests(unittest.TestCase):
    def test_three_auditions_commit_from_one_snapshot(self):
        scenarios.test_three_auditions_commit_from_one_snapshot()

    def test_disabled_length_validation_keeps_fact_guards_and_accepts_short_auditions(self):
        scenarios.test_disabled_length_validation_keeps_fact_guards_and_accepts_short_auditions()

    def test_unknown_memory_reference_rejects_all_invalid_auditions(self):
        scenarios.test_unknown_memory_reference_rejects_all_invalid_auditions()

    def test_project_version_conflict_cannot_leave_partially_published_artifacts(self):
        scenarios.test_project_version_conflict_cannot_leave_partially_published_artifacts()

    def test_reader_returns_only_final_or_confirmed_chapters(self):
        scenarios.test_reader_returns_only_final_or_confirmed_chapters()

    def test_cancelled_job_cannot_publish_late_provider_output(self):
        repo, _, project, ref, job = scenarios._fixture()
        cancelled = repo.cancel_job(project_id=project.project_id, job_id=job.job_id)
        result = NarrativeGenerationProcessor(
            repo, scenarios._Provider(ref.memory_version_id)
        ).run_job(project_id=project.project_id, job_id=job.job_id)
        self.assertEqual(cancelled.state, NarrativeJobState.CANCELLED)
        self.assertEqual(result.state, NarrativeJobState.CANCELLED)
        self.assertEqual(repo.list_artifacts(project_id=project.project_id), ())
        self.assertEqual(
            repo.get_project_for_worker(project_id=project.project_id).state,
            BookProjectState.READY_FOR_CONFIRMATION,
        )

    def test_terminal_failure_restores_a_retryable_project_state(self):
        repo, _, project, _, job = scenarios._fixture()
        failed = repo.update_job(
            replace(
                job,
                state=NarrativeJobState.FAILED,
                attempt_count=job.max_attempts,
                retryable=False,
                error_code="provider_unavailable",
            )
        )
        repo.dead_letter_job(job=failed)
        self.assertEqual(
            repo.get_project_for_worker(project_id=project.project_id).state,
            BookProjectState.READY_FOR_CONFIRMATION,
        )


if __name__ == "__main__":
    unittest.main()

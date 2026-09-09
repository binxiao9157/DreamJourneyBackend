from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import unittest

from app.async_effects.owner_truth_memory_search_embedding_worker import (
    OwnerTruthMemorySearchEmbeddingWorkerRuntime,
)
from app.core.config import Settings
from app.services.owner_truth_memory_search_embedding_backfill import (
    OwnerTruthMemorySearchEmbeddingCompletion,
    OwnerTruthMemorySearchEmbeddingTask,
)
from app.services.owner_truth_memory_search_hybrid import OwnerTruthEmbeddingModel


class _Provider:
    model = OwnerTruthEmbeddingModel("runtime-test", "v1", 2)

    def __init__(self, store, *, fail: bool = False) -> None:
        self._store = store
        self._fail = fail
        self.calls: list[tuple[str, ...]] = []

    def embed_documents(self, *, texts):
        if self._store.active_unit_of_work:
            raise AssertionError("embedding provider must not run inside a database unit of work")
        self.calls.append(tuple(texts))
        if self._fail:
            raise RuntimeError("provider secret must not be surfaced")
        return tuple((0.25, 0.75) for _ in texts)


class _Repository:
    def __init__(self, tasks: tuple[OwnerTruthMemorySearchEmbeddingTask, ...]) -> None:
        self.tasks = tasks
        self.enqueued: list[tuple[object, int]] = []
        self.claimed = False
        self.completed: list[tuple[tuple[object, ...], tuple[tuple[float, ...], ...]]] = []
        self.retried: list[tuple[object, ...]] = []

    def enqueue_missing(self, *, model, limit):
        self.enqueued.append((model, limit))
        return len(self.tasks)

    def claim_batch(self, *, worker_id, model, limit, lease_seconds):
        del worker_id, model, limit, lease_seconds
        if self.claimed:
            return ()
        self.claimed = True
        return self.tasks

    def complete_batch(self, *, worker_id, tasks, vectors):
        del worker_id
        self.completed.append((tuple(tasks), tuple(tuple(value) for value in vectors)))
        return OwnerTruthMemorySearchEmbeddingCompletion(
            ready_count=len(tasks), stale_count=0
        )

    def retry_batch(self, *, worker_id, tasks, retry_seconds, error_code, max_attempts):
        self.retried.append(
            (worker_id, tuple(tasks), retry_seconds, error_code, max_attempts)
        )
        return len(tasks), 0


class _Store:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.active_unit_of_work = False

    @contextmanager
    def request_unit_of_work(self, *, correlation_id, command_id):
        del correlation_id, command_id
        self.active_unit_of_work = True
        try:
            yield
        finally:
            self.active_unit_of_work = False

    def owner_truth_memory_search_embedding_repository(self):
        return self.repository


class _MetricRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_attempt(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"sinkOutcome": "appended"}


class OwnerTruthMemorySearchEmbeddingWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = _Provider.model
        self.task = OwnerTruthMemorySearchEmbeddingTask(
            vault_id="vault-embedding-worker",
            owner_subject_id="owner-embedding-worker",
            authority_epoch=3,
            memory_version_id="memory-version-embedding-worker",
            content_hash="a" * 64,
            source_projection_checkpoint="b" * 64,
            model=self.model,
            search_text="只供派生 embedding 的已确认搜索正文",
            attempt=1,
        )
        self.settings = Settings(
            store_backend="postgres",
            owner_truth_memory_search_projection_worker_enabled=True,
            owner_truth_memory_search_embedding_worker_enabled=True,
        )

    def test_provider_is_called_outside_transactions_and_completion_is_bounded(self) -> None:
        repository = _Repository((self.task,))
        store = _Store(repository)
        provider = _Provider(store)
        metric_recorder = _MetricRecorder()

        result = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=self.settings,
            store=store,
            provider=provider,
            worker_id="worker-a",
            operation_metric_recorder=metric_recorder,
        ).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual(provider.calls, [(self.task.search_text,)])
        self.assertEqual(len(repository.completed), 1)
        self.assertFalse(repository.retried)
        self.assertEqual(len(metric_recorder.calls), 1)
        metric = metric_recorder.calls[0]
        self.assertEqual(metric["component_kind"], "worker")
        self.assertEqual(metric["component_id"], "ownerTruthMemorySearchEmbeddingWorker")
        self.assertEqual(metric["operation"], "ownerTruthMemorySearchEmbedding")
        self.assertEqual(metric["outcome"], "succeeded")
        self.assertNotIn(self.task.search_text, str(metric))

    def test_provider_failure_is_retryable_without_formal_write(self) -> None:
        repository = _Repository((self.task,))
        store = _Store(repository)
        provider = _Provider(store, fail=True)

        result = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=self.settings,
            store=store,
            provider=provider,
            worker_id="worker-b",
        ).run_once()

        self.assertEqual(result["status"], "retryWait")
        self.assertEqual(result["reason"], "searchEmbeddingProviderRetryableFailure")
        self.assertFalse(repository.completed)
        self.assertEqual(len(repository.retried), 1)
        self.assertNotIn("secret", str(result).lower())

    def test_kill_switch_blocks_even_if_a_test_provider_is_supplied(self) -> None:
        repository = _Repository((self.task,))
        store = _Store(repository)
        disabled = replace(
            self.settings,
            owner_truth_memory_search_embedding_worker_enabled=False,
        )

        result = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=disabled,
            store=store,
            provider=_Provider(store),
        ).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "ownerTruthMemorySearchEmbeddingWorkerDisabled")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

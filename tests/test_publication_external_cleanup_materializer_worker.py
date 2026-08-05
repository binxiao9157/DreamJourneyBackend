from __future__ import annotations

from contextlib import contextmanager
from contextlib import redirect_stderr
from io import StringIO
import unittest
from uuid import uuid4

from app.async_effects.provider_effect_repository import InMemoryProviderEffectRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.async_effects.publication_external_cleanup_materializer_worker import (
    PublicationExternalCleanupMaterializerWorkerRuntime,
    _parser,
)
from app.core.config import Settings
from app.services.publication_external_cleanup import (
    InMemoryPublicationExternalCleanupRepository,
    PublicationExternalCleanupMaterializationTarget,
)


class _CleanupRepository(InMemoryPublicationExternalCleanupRepository):
    def __init__(self, target: PublicationExternalCleanupMaterializationTarget) -> None:
        super().__init__()
        self._target = target

    def list_pending_materializations(self, *, limit: int):
        if self.effect_count(self._target.lifecycle_receipt_id):
            return ()
        return (self._target,)[:limit]


class _Store:
    def __init__(self, target: PublicationExternalCleanupMaterializationTarget) -> None:
        self.cleanup = _CleanupRepository(target)
        self.effects = InMemoryEffectKernelRepository()
        self.provider_effects = InMemoryProviderEffectRepository()
        self.uow_calls = 0

    def readiness_probe(self):
        return {"status": "ready"}

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        self.uow_calls += 1
        yield self

    def publication_external_cleanup_repository(self):
        return self.cleanup

    def effect_kernel_repository(self):
        return self.effects

    def provider_effect_repository(self):
        return self.provider_effects


class PublicationExternalCleanupMaterializerWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = PublicationExternalCleanupMaterializationTarget(
            lifecycle_receipt_id=str(uuid4()),
            vault_id="vault-cleanup-worker",
            owner_subject_id="owner-cleanup-worker",
            publication_id=str(uuid4()),
            publication_version_id=str(uuid4()),
            authority_epoch=7,
            action="systemSuspend",
            reason_code="sourceRedacted",
        )
        self.store = _Store(self.target)

    def _worker(self, *, enabled: bool = True):
        return PublicationExternalCleanupMaterializerWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                publication_external_cleanup_materializer_enabled=enabled,
            ),
            store=self.store,
            worker_id="publication-cleanup-worker-test",
        )

    def test_default_disabled_worker_does_not_materialize_receipts(self) -> None:
        result = self._worker(enabled=False).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "publicationExternalCleanupMaterializerDisabled")
        self.assertEqual(self.store.cleanup.effect_count(self.target.lifecycle_receipt_id), 0)
        self.assertEqual(self.store.uow_calls, 0)

    def test_enabled_worker_materializes_pending_receipt_without_provider_completion_claim(self) -> None:
        result = self._worker().run_once()

        self.assertEqual(result["status"], "materialized")
        self.assertEqual(result["materializedReceiptCount"], 1)
        self.assertEqual(result["materializedEffectCount"], 5)
        self.assertEqual(
            result["domainStates"],
            {
                "cache:pending": 1,
                "digitalHumanSession:pending": 1,
                "objectStorage:pending": 1,
                "providerVoice:pending": 1,
                "publicIndex:pending": 1,
            },
        )
        self.assertEqual(self.store.cleanup.effect_count(self.target.lifecycle_receipt_id), 5)

        replay = self._worker().run_once()
        self.assertEqual(replay["status"], "idle")
        self.assertEqual(replay["reason"], "noPendingPublicationExternalCleanup")

    def test_cli_accepts_once_or_loop_but_not_both(self) -> None:
        parser = _parser()
        self.assertTrue(parser.parse_args(["--once"]).once)
        self.assertTrue(parser.parse_args(["--loop"]).loop)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--once", "--loop"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

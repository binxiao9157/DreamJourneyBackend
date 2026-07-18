from __future__ import annotations

from contextlib import contextmanager
import unittest
import uuid

from app.async_effects.repository import InMemoryEffectKernelRepository
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandResult,
)
from app.services.owner_truth_source import OwnerTruthSourceAsyncEffectCommandService


class _FailingEffectWriter:
    def accept(self, _intent):
        raise RuntimeError("synthetic async effect insert failure")


class _AtomicSourceEffectStore:
    def __init__(self, *, effect_writer=None):
        self._active = False
        self._sources = {}
        self._receipts = {}
        self._effect_writer = effect_writer or InMemoryEffectKernelRepository()
        self.root_uow_count = 0
        self.rollback_count = 0

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        self.assert_no_raw_command(correlation_id, command_id)
        if self._active:
            yield self
            return
        source_snapshot = dict(self._sources)
        receipt_snapshot = dict(self._receipts)
        self._active = True
        self.root_uow_count += 1
        try:
            yield self
        except Exception:
            self._sources = source_snapshot
            self._receipts = receipt_snapshot
            self.rollback_count += 1
            raise
        finally:
            self._active = False

    @staticmethod
    def assert_no_raw_command(correlation_id: str, command_id: str) -> None:
        if not correlation_id or len(command_id) != 64:
            raise AssertionError("effect command must use opaque correlation and command hashes")

    def create_owner_truth_source(self, record):
        if not self._active:
            raise AssertionError("source write escaped its unit of work")
        receipt_key = (record.vault_id, record.command_id_hash)
        existing = self._receipts.get(receipt_key)
        if existing is not None:
            return OwnerTruthSourceCommandResult(
                outcome="deduplicated",
                receipt_id=existing["receiptId"],
                source_id=record.source_id,
                source_version=1,
                authority_epoch=0,
                content_hash=record.content_hash,
            )
        self._sources[(record.vault_id, record.source_id)] = record
        self._receipts[receipt_key] = {"receiptId": record.receipt_id}
        return OwnerTruthSourceCommandResult(
            outcome="created",
            receipt_id=record.receipt_id,
            source_id=record.source_id,
            source_version=1,
            authority_epoch=0,
            content_hash=record.content_hash,
        )

    def effect_kernel_repository(self):
        if not self._active:
            raise AssertionError("effect write escaped its unit of work")
        return self._effect_writer

    @property
    def source_count(self) -> int:
        return len(self._sources)


class OwnerTruthSourceAsyncEffectTests(unittest.TestCase):
    def setUp(self):
        self.store = _AtomicSourceEffectStore()
        self.service = OwnerTruthSourceAsyncEffectCommandService(self.store)
        self.context = OwnerTruthCommandContext(
            vault_id="vault-source-effect",
            owner_subject_id="owner-source-effect",
            actor_subject_id="owner-source-effect",
        )

    def command(self, *, command_id: str = "source-effect-command"):
        return CreateTextSourceCommand(
            command_id=command_id,
            source_id=str(uuid.uuid4()),
            expected_version=0,
            text="A source is committed before its extraction effect is requested.",
            metadata={"title": "Atomic source"},
        )

    def test_source_and_effect_share_one_root_uow_and_replay_receipts(self):
        command = self.command()

        created = self.service.create_text_source(command=command, context=self.context)
        replayed = self.service.create_text_source(command=command, context=self.context)

        self.assertEqual(self.store.root_uow_count, 2)
        self.assertEqual(self.store.source_count, 1)
        self.assertEqual(created.source.outcome, "created")
        self.assertEqual(replayed.source.outcome, "deduplicated")
        self.assertEqual(created.effect.outcome, "accepted")
        self.assertEqual(replayed.effect.outcome, "deduplicated")
        self.assertEqual(created.effect.operation_id, replayed.effect.operation_id)
        self.assertEqual(created.effect.outbox_event_id, replayed.effect.outbox_event_id)
        self.assertNotIn("payloadHash", created.public_receipt()["effect"])

    def test_effect_failure_rolls_back_the_source_write(self):
        store = _AtomicSourceEffectStore(effect_writer=_FailingEffectWriter())
        service = OwnerTruthSourceAsyncEffectCommandService(store)

        with self.assertRaisesRegex(RuntimeError, "synthetic async effect insert failure"):
            service.create_text_source(command=self.command(), context=self.context)

        self.assertEqual(store.source_count, 0)
        self.assertEqual(store.rollback_count, 1)


if __name__ == "__main__":
    unittest.main()

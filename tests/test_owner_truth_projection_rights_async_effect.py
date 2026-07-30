from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from app.async_effects.repository import InMemoryEffectKernelRepository
from app.domain.owner_truth.projection_rights import (
    OwnerTruthProjectionRightsRevisionCommand,
    ProjectionRightsState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE,
)
from app.services.owner_truth_projection_rights import (
    InMemoryOwnerTruthProjectionRightsRepository,
    OwnerTruthProjectionRightsService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _EffectWriter:
    def __init__(self, *, fail_after_write: bool = False) -> None:
        self._repository = InMemoryEffectKernelRepository()
        self._fail_after_write = fail_after_write
        self.summaries = []

    def accept(self, intent):
        summary = self._repository.accept(intent)
        self.summaries.append(summary)
        if self._fail_after_write:
            raise RuntimeError("synthetic projection rights effect write failure")
        return summary

    def snapshot(self):
        return {
            "records": self._repository.snapshot(),
            "summaries": list(self.summaries),
        }

    def restore(self, snapshot) -> None:
        self._repository._records = deepcopy(snapshot["records"])
        self.summaries = list(snapshot["summaries"])

    def record_count(self) -> int:
        return self._repository.record_count()


class _AtomicRightsEffectStore:
    def __init__(self, *, effect_writer: _EffectWriter | None = None) -> None:
        self.rights_repository = InMemoryOwnerTruthProjectionRightsRepository()
        self.effect_writer = effect_writer or _EffectWriter()
        self._active = False
        self.rollback_count = 0
        self.root_uow_count = 0

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        if not correlation_id or len(command_id) != 64:
            raise AssertionError("rights effect work must use opaque correlation and command hashes")
        if self._active:
            yield self
            return
        rights_snapshot = {
            "snapshots": deepcopy(self.rights_repository._snapshots),
            "commands": deepcopy(self.rights_repository._commands),
        }
        effect_snapshot = self.effect_writer.snapshot()
        self._active = True
        self.root_uow_count += 1
        try:
            yield self
        except Exception:
            self.rights_repository._snapshots = deepcopy(rights_snapshot["snapshots"])
            self.rights_repository._commands = deepcopy(rights_snapshot["commands"])
            self.effect_writer.restore(effect_snapshot)
            self.rollback_count += 1
            raise
        finally:
            self._active = False

    def owner_truth_projection_rights_repository(self):
        return self.rights_repository

    def effect_kernel_repository(self):
        if not self._active:
            raise AssertionError("rights effect write escaped its unit of work")
        return self.effect_writer


class OwnerTruthProjectionRightsAsyncEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = OwnerTruthCommandContext(
            vault_id="vault-projection-rights-effect",
            owner_subject_id="subject-projection-rights-effect",
            actor_subject_id="subject-projection-rights-effect",
        )
        self.store = _AtomicRightsEffectStore()
        self.service = OwnerTruthProjectionRightsService(self.store)

    def _command(self, *, command_id: str, state: ProjectionRightsState):
        return OwnerTruthProjectionRightsRevisionCommand(
            command_id=command_id,
            authority_epoch=0,
            expected_revision=0,
            state=state,
            event_hash=_hash({"command": command_id, "state": state.value}),
        )

    def test_rights_revision_and_value_free_projection_effect_share_one_uow(self) -> None:
        command = self._command(
            command_id="projection-rights-effect-active-001",
            state=ProjectionRightsState.ACTIVE,
        )

        result = self.service.record(context=self.context, command=command)
        replay = self.service.record(context=self.context, command=command)

        self.assertEqual(result.outcome, "recorded")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(result.snapshot.revision, 1)
        self.assertEqual(self.store.root_uow_count, 2)
        self.assertEqual(self.store.effect_writer.record_count(), 1)
        self.assertEqual(len(self.store.effect_writer.summaries), 2)
        accepted, deduplicated = self.store.effect_writer.summaries
        self.assertEqual(accepted.outcome, "accepted")
        self.assertEqual(deduplicated.outcome, "deduplicated")
        self.assertEqual(accepted.operation_id, deduplicated.operation_id)
        rendered_summary = json.dumps(vars(accepted), sort_keys=True).lower()
        self.assertNotIn("candidate", rendered_summary)
        self.assertNotIn("content", rendered_summary)

        records = self.store.effect_writer._repository.snapshot()
        record = next(iter(records.values()))
        self.assertEqual(
            record["summary"].operation_id,
            accepted.operation_id,
        )
        self.assertEqual(
            result.snapshot.event_hash,
            command.event_hash,
        )

    def test_revocation_still_records_a_rebuild_intent_for_fail_closed_worker_admission(self) -> None:
        result = self.service.record(
            context=self.context,
            command=self._command(
                command_id="projection-rights-effect-revoked-001",
                state=ProjectionRightsState.REVOKED,
            ),
        )

        self.assertEqual(result.snapshot.state, ProjectionRightsState.REVOKED)
        self.assertEqual(self.store.effect_writer.record_count(), 1)
        summary = self.store.effect_writer.summaries[0]
        self.assertTrue(summary.operation_id)
        self.assertNotIn(result.snapshot.event_hash, json.dumps(vars(summary), sort_keys=True))

    def test_effect_write_failure_rolls_back_the_rights_revision(self) -> None:
        store = _AtomicRightsEffectStore(effect_writer=_EffectWriter(fail_after_write=True))
        service = OwnerTruthProjectionRightsService(store)
        command = self._command(
            command_id="projection-rights-effect-failure-001",
            state=ProjectionRightsState.ACTIVE,
        )

        with self.assertRaisesRegex(RuntimeError, "synthetic projection rights effect write failure"):
            service.record(context=self.context, command=command)

        current = store.rights_repository.read(context=self.context, authority_epoch=0)
        self.assertEqual(store.rollback_count, 1)
        self.assertEqual(current.revision, 0)
        self.assertEqual(store.effect_writer.record_count(), 0)
        self.assertEqual(store.effect_writer.summaries, [])


class OwnerTruthProjectionRightsEffectContractTests(unittest.TestCase):
    def test_effect_contract_constants_remain_typed_and_value_free(self) -> None:
        self.assertEqual(MEMORY_PROJECTION_REBUILD_JOB_TYPE, "ownerTruth.memoryProjection.rebuild")
        self.assertEqual(
            MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE,
            "ownerTruth.projectionRights.recorded",
        )
        self.assertEqual(
            MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE,
            "ownerTruth.memoryProjection.rightsRebuildRequested",
        )


if __name__ == "__main__":
    unittest.main()

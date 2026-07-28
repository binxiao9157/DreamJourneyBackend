"""Service tests for C04's append-only, no-effect shadow report ledger."""

from __future__ import annotations

from hashlib import sha256
from typing import Optional
import unittest
from uuid import uuid4

from app.async_effects.provider_effects import provider_effect_catalog
from app.domain.owner_truth.legacy_backfill import (
    LegacyBackfillAdmissionAction,
    build_legacy_backfill_admission_plan,
)
from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    build_legacy_migration_inventory,
)
from app.domain.owner_truth.legacy_tail_shadow import (
    LegacyTailShadowChannel,
    LegacyTailShadowOperation,
    build_legacy_tail_shadow_report,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_tail_shadow import (
    InMemoryOwnerTruthLegacyTailShadowRepository,
    OwnerTruthLegacyTailShadowAccessDenied,
    OwnerTruthLegacyTailShadowConflict,
    OwnerTruthLegacyTailShadowService,
    OwnerTruthLegacyTailShadowUnavailable,
    PostgresOwnerTruthLegacyTailShadowRepository,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _TailShadowStore:
    def __init__(self) -> None:
        self.vault = {
            "ownerSubjectId": "owner-tail-shadow-service",
            "authorityEpoch": 4,
            "status": "active",
        }
        self.repository = InMemoryOwnerTruthLegacyTailShadowRepository(
            authority_supplier=self._authority,
        )

    def _authority(
        self,
        vault_id: str,
        _owner_subject_id: str,
    ) -> Optional[dict[str, object]]:
        if vault_id != "vault-tail-shadow-service":
            return None
        return dict(self.vault)

    def owner_truth_legacy_tail_shadow_repository(self):
        return self.repository


class _PostgresTailShadowCursor:
    """Minimal value-free cursor double for the C04 write contract."""

    def __init__(self, *, plan) -> None:
        self.plan = plan
        self.executed: list[str] = []
        self.mapping_rows: list[dict[str, object]] = []
        self._one = None
        self._many: list[dict[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def execute(self, query: str, params=()) -> None:
        normalized = " ".join(query.split())
        self.executed.append(normalized)
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            self._one = {"locked": True}
        elif "FROM owner_truth.vaults" in normalized:
            self._one = {
                "owner_subject_id": self.plan.owner_subject_id,
                "authority_epoch": self.plan.authority_epoch,
                "status": "active",
            }
        elif "FROM owner_truth.legacy_migration_backfill_plans" in normalized:
            self._one = {
                "id": self.plan.plan_id,
                "vault_id": self.plan.vault_id,
                "owner_subject_id": self.plan.owner_subject_id,
                "authority_epoch": self.plan.authority_epoch,
                "plan_hash": self.plan.plan_hash,
            }
        elif normalized.startswith("INSERT INTO owner_truth.legacy_migration_tail_shadow_reports"):
            self._one = {"plan_id": self.plan.plan_id}
        elif normalized.startswith("INSERT INTO owner_truth.legacy_migration_tail_shadow_mappings"):
            self.mapping_rows.append(
                {
                    "mapping_hash": params[2],
                    "channel": params[3],
                    "source_domain": params[4],
                    "source_legacy_id_hash": params[5],
                    "source_record_hash": params[6],
                    "action": params[7],
                    "operation_stable_key": params[8],
                    "provider_catalog_key": params[9],
                    "provider_query_reconcile_support": params[10],
                    "object_reference_hash": params[11],
                    "callback_fixture_hash": params[12],
                }
            )
            self._one = None
        elif "FROM owner_truth.legacy_migration_tail_shadow_mappings" in normalized:
            self._many = sorted(
                self.mapping_rows,
                key=lambda row: (str(row["channel"]), str(row["mapping_hash"])),
            )
            self._one = None
        else:  # pragma: no cover - fails loudly when repository SQL changes
            raise AssertionError(f"unexpected C04 repository query: {normalized}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _PostgresTailShadowConnection:
    def __init__(self, *, plan) -> None:
        self.cursor_double = _PostgresTailShadowCursor(plan=plan)

    def cursor(self, row_factory=None):
        del row_factory
        return self.cursor_double


class OwnerTruthLegacyTailShadowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _TailShadowStore()
        self.context = OwnerTruthCommandContext(
            vault_id="vault-tail-shadow-service",
            owner_subject_id="owner-tail-shadow-service",
            actor_subject_id="owner-tail-shadow-service",
        )
        inventory = build_legacy_migration_inventory(
            vault_id=self.context.vault_id,
            classifier_version="legacy-tail-shadow-service-test-v1",
            records=(
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id="private-archive-source-id",
                    record_hash=_digest("archive-record"),
                    canonical_owner_subject_id=self.context.owner_subject_id,
                    observed_owner_subject_id=self.context.owner_subject_id,
                    source_evidence_id="source-proof",
                    decision_receipt_id="decision-proof",
                    decision_is_terminal=True,
                    revision_evidence_id="revision-proof",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.KB_CHANGE,
                    legacy_id="private-kb-change-id",
                    record_hash=_digest("kb-change-record"),
                    canonical_owner_subject_id=self.context.owner_subject_id,
                    observed_owner_subject_id=self.context.owner_subject_id,
                ),
            ),
        )
        self.plan = build_legacy_backfill_admission_plan(
            inventory_run_id=str(uuid4()),
            inventory=inventory,
            owner_subject_id=self.context.owner_subject_id,
            authority_epoch=4,
        )

    def _operations(self) -> list[LegacyTailShadowOperation]:
        eligible = [
            entry
            for entry in self.plan.entries
            if entry.action
            in {
                LegacyBackfillAdmissionAction.REQUIRE_INDEPENDENT_LINEAGE_REPLAY,
                LegacyBackfillAdmissionAction.REQUIRE_OWNER_CANDIDATE_REVIEW,
                LegacyBackfillAdmissionAction.REQUIRE_EVIDENCE_REVIEW,
            }
        ]
        result: list[LegacyTailShadowOperation] = []
        for index, entry in enumerate(eligible, start=1):
            result.append(
                LegacyTailShadowOperation(
                    channel=LegacyTailShadowChannel.OUTBOX_JOB,
                    domain=entry.domain,
                    legacy_id_hash=entry.legacy_id_hash,
                    record_hash=entry.record_hash,
                    tail_cursor_hash=_digest(f"outbox:{index}"),
                    source_version=1,
                )
            )
        archive = next(entry for entry in eligible if entry.domain is LegacyMigrationDomain.ARCHIVE_ITEM)
        result.append(
            LegacyTailShadowOperation(
                channel=LegacyTailShadowChannel.OBJECT_REFERENCE,
                domain=archive.domain,
                legacy_id_hash=archive.legacy_id_hash,
                record_hash=archive.record_hash,
                tail_cursor_hash=_digest("object-tail"),
                source_version=1,
                object_reference_hash=_digest("object-reference"),
            )
        )
        for index, catalog_entry in enumerate(provider_effect_catalog(), start=100):
            if catalog_entry.requires_stable_provider_effect:
                result.append(
                    LegacyTailShadowOperation(
                        channel=LegacyTailShadowChannel.PROVIDER_EFFECT,
                        domain=archive.domain,
                        legacy_id_hash=archive.legacy_id_hash,
                        record_hash=archive.record_hash,
                        tail_cursor_hash=_digest(f"provider-tail:{index}"),
                        source_version=1,
                        provider_catalog_key=catalog_entry.key,
                        callback_fixture_hash=_digest(f"callback-fixture:{catalog_entry.key}"),
                    )
                )
        return result

    def test_disabled_service_fails_closed_without_persisting_a_report(self) -> None:
        service = OwnerTruthLegacyTailShadowService(self.store)

        with self.assertRaises(OwnerTruthLegacyTailShadowUnavailable):
            service.shadow(
                context=self.context,
                plan=self.plan,
                operations=self._operations(),
            )

        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_owner_only_report_is_replay_safe_and_has_no_effect_capability(self) -> None:
        service = OwnerTruthLegacyTailShadowService(self.store, enabled=True)

        created = service.shadow(
            context=self.context,
            plan=self.plan,
            operations=self._operations(),
        )
        replayed = service.shadow(
            context=self.context,
            plan=self.plan,
            operations=list(reversed(self._operations())),
        )

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.report.report_hash, replayed.report.report_hash)
        self.assertTrue(created.report.ready_for_next_gate)
        summary = created.public_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertEqual(summary["effectExecutionCount"], 0)
        self.assertEqual(summary["outboxWriteCount"], 0)
        self.assertEqual(summary["jobWriteCount"], 0)
        self.assertEqual(summary["objectStorageOperationCount"], 0)
        self.assertEqual(summary["providerCallCount"], 0)
        self.assertEqual(summary["providerCallbackProcessedCount"], 0)
        self.assertEqual(summary["callbackAcceptedCount"], 0)
        self.assertFalse(summary["cutoverAllowed"])
        self.assertFalse(summary["legacyWriterRetired"])
        self.assertEqual(self.store.repository.snapshot()["reportCount"], 1)
        self.assertNotIn("private-archive-source-id", repr(summary))
        self.assertNotIn("private-kb-change-id", repr(summary))

    def test_cross_owner_and_cross_vault_plan_are_rejected_before_write(self) -> None:
        service = OwnerTruthLegacyTailShadowService(self.store, enabled=True)
        attacker = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id="other-subject",
        )
        with self.assertRaises(OwnerTruthLegacyTailShadowAccessDenied):
            service.shadow(context=attacker, plan=self.plan, operations=self._operations())

        cross_vault = OwnerTruthCommandContext(
            vault_id="other-vault",
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id=self.context.owner_subject_id,
        )
        with self.assertRaises(OwnerTruthLegacyTailShadowAccessDenied):
            service.shadow(context=cross_vault, plan=self.plan, operations=self._operations())

        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_epoch_change_rejects_persisting_a_prebuilt_report(self) -> None:
        report = build_legacy_tail_shadow_report(plan=self.plan, operations=self._operations())
        self.store.vault["authorityEpoch"] = 5

        with self.assertRaises(OwnerTruthLegacyTailShadowConflict):
            self.store.repository.persist(
                owner_subject_id=self.context.owner_subject_id,
                plan=self.plan,
                report=report,
            )

        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_service_does_not_accept_an_admission_plan_from_another_owner(self) -> None:
        service = OwnerTruthLegacyTailShadowService(self.store, enabled=True)
        foreign_plan = build_legacy_backfill_admission_plan(
            inventory_run_id=str(uuid4()),
            inventory=build_legacy_migration_inventory(
                vault_id="other-vault",
                classifier_version="foreign-v1",
                records=(),
            ),
            owner_subject_id="other-owner",
            authority_epoch=0,
        )

        with self.assertRaises(OwnerTruthLegacyTailShadowAccessDenied):
            service.shadow(context=self.context, plan=foreign_plan, operations=())

        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_postgres_writer_records_only_c04_shadow_tables_and_mapping_evidence(self) -> None:
        report = build_legacy_tail_shadow_report(plan=self.plan, operations=self._operations())
        connection = _PostgresTailShadowConnection(plan=self.plan)
        repository = PostgresOwnerTruthLegacyTailShadowRepository(connection)

        result = repository.persist(
            owner_subject_id=self.context.owner_subject_id,
            plan=self.plan,
            report=report,
        )

        self.assertEqual(result.outcome, "created")
        self.assertEqual(len(connection.cursor_double.mapping_rows), len(report.mappings))
        executed = "\n".join(connection.cursor_double.executed)
        self.assertIn("legacy_migration_tail_shadow_reports", executed)
        self.assertIn("legacy_migration_tail_shadow_mappings", executed)
        for forbidden in (
            "INSERT INTO async_effects",
            "INSERT INTO owner_truth.sources",
            "INSERT INTO owner_truth.memory_candidates",
            "INSERT INTO owner_truth.memory_versions",
            "UPDATE ",
            "DELETE ",
        ):
            self.assertNotIn(forbidden, executed)


if __name__ == "__main__":
    unittest.main()

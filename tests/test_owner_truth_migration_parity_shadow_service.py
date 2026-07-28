"""Service tests for C05's append-only, value-free parity evidence ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional
import unittest

from app.domain.owner_truth.migration_parity_shadow import (
    MigrationParityAllowance,
    MigrationParityComparisonWindow,
    MigrationParityDimension,
    MigrationParityObservation,
    MigrationParitySurface,
    build_migration_parity_scope_hash,
    build_migration_parity_shadow_report,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_migration_parity_shadow import (
    InMemoryOwnerTruthMigrationParityShadowRepository,
    OwnerTruthMigrationParityShadowAccessDenied,
    OwnerTruthMigrationParityShadowConflict,
    OwnerTruthMigrationParityShadowService,
    OwnerTruthMigrationParityShadowUnavailable,
    PostgresOwnerTruthMigrationParityShadowRepository,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _ParityShadowStore:
    def __init__(self) -> None:
        self.vault = {
            "ownerSubjectId": "owner-parity-service",
            "authorityEpoch": 4,
            "status": "active",
        }
        self.repository = InMemoryOwnerTruthMigrationParityShadowRepository(
            authority_supplier=self._authority,
        )

    def _authority(
        self,
        vault_id: str,
        _owner_subject_id: str,
    ) -> Optional[dict[str, object]]:
        if vault_id != "vault-parity-service":
            return None
        return dict(self.vault)

    def owner_truth_migration_parity_shadow_repository(self):
        return self.repository


class _PostgresParityShadowCursor:
    """Minimal value-free cursor double for C05's append-only SQL contract."""

    def __init__(self, *, owner_subject_id: str, authority_epoch: int) -> None:
        self.owner_subject_id = owner_subject_id
        self.authority_epoch = authority_epoch
        self.executed: list[str] = []
        self.mismatch_rows: list[dict[str, object]] = []
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
                "owner_subject_id": self.owner_subject_id,
                "authority_epoch": self.authority_epoch,
                "status": "active",
            }
        elif normalized.startswith("INSERT INTO owner_truth.migration_parity_shadow_reports"):
            self._one = {"report_hash": params[0]}
        elif normalized.startswith("INSERT INTO owner_truth.migration_parity_shadow_mismatches"):
            self.mismatch_rows.append(
                {
                    "observation_hash": params[1],
                    "sample_id_hash": params[2],
                    "surface": params[3],
                    "dimension": params[4],
                    "mismatch_code": params[5],
                    "severity": params[6],
                    "legacy_value_hash": params[7],
                    "v4_value_hash": params[8],
                    "allowance_status": params[9],
                    "allowance_reason_code": params[10],
                    "approval_reference_hash": params[11],
                    "allowance_expires_at": params[12],
                }
            )
            self._one = None
        elif "FROM owner_truth.migration_parity_shadow_mismatches" in normalized:
            self._many = sorted(
                self.mismatch_rows,
                key=lambda row: str(row["observation_hash"]),
            )
            self._one = None
        else:  # pragma: no cover - fails loudly if C05 SQL changes
            raise AssertionError("unexpected C05 repository query: %s" % normalized)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _PostgresParityShadowConnection:
    def __init__(self, *, owner_subject_id: str, authority_epoch: int) -> None:
        self.cursor_double = _PostgresParityShadowCursor(
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
        )

    def cursor(self, row_factory=None):
        del row_factory
        return self.cursor_double


class OwnerTruthMigrationParityShadowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        self.store = _ParityShadowStore()
        self.context = OwnerTruthCommandContext(
            vault_id="vault-parity-service",
            owner_subject_id="owner-parity-service",
            actor_subject_id="owner-parity-service",
        )

    def _window(self, sample_count: int) -> MigrationParityComparisonWindow:
        return MigrationParityComparisonWindow(
            window_reference_hash=_digest("approved-parity-window"),
            scope_hash=build_migration_parity_scope_hash(
                vault_id=self.context.vault_id,
                owner_subject_id=self.context.owner_subject_id,
                authority_epoch=int(self.store.vault["authorityEpoch"]),
            ),
            denominator_source_hash=_digest("synthetic-parity-denominator"),
            threshold_source_hash=_digest("approved-parity-threshold"),
            expected_sample_count=sample_count,
        )

    def _observation(
        self,
        label: str,
        dimension: MigrationParityDimension,
        *,
        legacy: str = "legacy",
        v4: str = "v4",
        surface: MigrationParitySurface = MigrationParitySurface.READ,
    ) -> MigrationParityObservation:
        return MigrationParityObservation(
            sample_id_hash=_digest("sample:" + label),
            surface=surface,
            dimension=dimension,
            legacy_value_hash=_digest("value:" + legacy),
            v4_value_hash=_digest("value:" + v4),
        )

    def _approved_allowance(
        self,
        observation: MigrationParityObservation,
    ) -> MigrationParityAllowance:
        return MigrationParityAllowance(
            observation_hash=observation.observation_hash,
            reason_code="approvedDisplayNormalization",
            approval_reference_hash=_digest("product-data-approval"),
            expires_at=self.now + timedelta(days=1),
        )

    def _report(self):
        matching = self._observation(
            "resource",
            MigrationParityDimension.RESOURCE_IDENTITY,
            legacy="same-resource",
            v4="same-resource",
        )
        reviewable = self._observation(
            "display",
            MigrationParityDimension.DISPLAY_NORMALIZATION,
        )
        return build_migration_parity_shadow_report(
            window=self._window(2),
            observations=(matching, reviewable),
            allowances=(self._approved_allowance(reviewable),),
            as_of=self.now,
        )

    def test_disabled_service_fails_closed_without_persisting_a_report(self) -> None:
        service = OwnerTruthMigrationParityShadowService(self.store)

        with self.assertRaises(OwnerTruthMigrationParityShadowUnavailable):
            service.shadow(
                context=self.context,
                window=self._window(2),
                observations=self._report().observations,
                allowances=(self._approved_allowance(self._report().observations[1]),),
                as_of=self.now,
            )

        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_owner_only_report_is_replay_safe_and_value_free(self) -> None:
        service = OwnerTruthMigrationParityShadowService(self.store, enabled=True)
        report = self._report()
        allowances = tuple(
            mismatch.allowance
            for mismatch in report.mismatches
            if mismatch.allowance is not None
        )

        created = service.shadow(
            context=self.context,
            window=report.window,
            observations=report.observations,
            allowances=allowances,
            as_of=self.now,
        )
        replayed = service.shadow(
            context=self.context,
            window=report.window,
            observations=reversed(report.observations),
            allowances=allowances,
            as_of=self.now,
        )

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertTrue(created.report.ready_for_next_gate)
        summary = created.public_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertEqual(summary["commandEffectExecutionCount"], 0)
        self.assertEqual(summary["objectCopyExecutionCount"], 0)
        self.assertEqual(summary["providerCallCount"], 0)
        self.assertFalse(summary["providerCostCharged"])
        self.assertFalse(summary["cutoverAllowed"])
        self.assertEqual(self.store.repository.snapshot()["reportCount"], 1)
        self.assertNotIn("owner-parity-service", repr(summary))
        self.assertNotIn("vault-parity-service", repr(summary))

    def test_cross_owner_and_misbound_scope_are_rejected_before_write(self) -> None:
        service = OwnerTruthMigrationParityShadowService(self.store, enabled=True)
        attacker = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id="other-subject",
        )
        with self.assertRaises(OwnerTruthMigrationParityShadowAccessDenied):
            service.shadow(
                context=attacker,
                window=self._window(2),
                observations=self._report().observations,
                allowances=(self._approved_allowance(self._report().observations[1]),),
                as_of=self.now,
            )

        foreign_window = MigrationParityComparisonWindow(
            window_reference_hash=_digest("approved-parity-window"),
            scope_hash=build_migration_parity_scope_hash(
                vault_id="other-vault",
                owner_subject_id=self.context.owner_subject_id,
                authority_epoch=4,
            ),
            denominator_source_hash=_digest("synthetic-parity-denominator"),
            threshold_source_hash=_digest("approved-parity-threshold"),
            expected_sample_count=2,
        )
        report = self._report()
        allowances = tuple(
            mismatch.allowance
            for mismatch in report.mismatches
            if mismatch.allowance is not None
        )
        with self.assertRaises(OwnerTruthMigrationParityShadowConflict):
            service.shadow(
                context=self.context,
                window=foreign_window,
                observations=report.observations,
                allowances=allowances,
                as_of=self.now,
            )
        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_epoch_change_rejects_prebuilt_report_before_persistence(self) -> None:
        report = self._report()
        authority = self.store.repository.read_authority(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
        )
        self.store.vault["authorityEpoch"] = 5

        with self.assertRaises(OwnerTruthMigrationParityShadowConflict):
            self.store.repository.persist(authority=authority, report=report)

        self.assertEqual(self.store.repository.snapshot()["reportCount"], 0)

    def test_postgres_writer_records_only_c05_shadow_tables_and_mismatch_evidence(self) -> None:
        report = self._report()
        connection = _PostgresParityShadowConnection(
            owner_subject_id=self.context.owner_subject_id,
            authority_epoch=4,
        )
        repository = PostgresOwnerTruthMigrationParityShadowRepository(connection)
        authority = repository.read_authority(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
        )

        result = repository.persist(authority=authority, report=report)

        self.assertEqual(result.outcome, "created")
        self.assertEqual(len(connection.cursor_double.mismatch_rows), len(report.mismatches))
        executed = "\n".join(connection.cursor_double.executed)
        self.assertIn("migration_parity_shadow_reports", executed)
        self.assertIn("migration_parity_shadow_mismatches", executed)
        for forbidden in (
            "INSERT INTO async_effects",
            "INSERT INTO owner_truth.sources",
            "INSERT INTO owner_truth.memory_candidates",
            "INSERT INTO owner_truth.memory_versions",
            "UPDATE ",
            "DELETE ",
        ):
            self.assertNotIn(forbidden, executed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

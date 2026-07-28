from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional
import unittest

from app.domain.owner_truth.migration_parity_shadow import (
    MigrationParityAllowance,
    MigrationParityComparisonWindow,
    MigrationParityDimension,
    MigrationParityMismatchCode,
    MigrationParityObservation,
    MigrationParitySurface,
    OwnerTruthMigrationParityShadowConflict,
    OwnerTruthMigrationParityShadowError,
    build_migration_parity_shadow_report,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class OwnerTruthMigrationParityShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    def _window(self, sample_count: int) -> MigrationParityComparisonWindow:
        return MigrationParityComparisonWindow(
            window_reference_hash=_digest("approved-window"),
            scope_hash=_digest("owner-vault-authority-scope"),
            denominator_source_hash=_digest("synthetic-denominator"),
            threshold_source_hash=_digest("approved-threshold-source"),
            expected_sample_count=sample_count,
        )

    def _observation(
        self,
        sample: str,
        dimension: MigrationParityDimension,
        *,
        legacy: str = "legacy",
        v4: str = "v4",
        surface: MigrationParitySurface = MigrationParitySurface.READ,
    ) -> MigrationParityObservation:
        return MigrationParityObservation(
            sample_id_hash=_digest("sample:" + sample),
            surface=surface,
            dimension=dimension,
            legacy_value_hash=_digest("value:" + legacy),
            v4_value_hash=_digest("value:" + v4),
        )

    def _approved_allowance(
        self,
        observation: MigrationParityObservation,
        *,
        expires_at: Optional[datetime] = None,
    ) -> MigrationParityAllowance:
        return MigrationParityAllowance(
            observation_hash=observation.observation_hash,
            reason_code="approvedDisplayNormalization",
            approval_reference_hash=_digest("product-data-approval"),
            expires_at=expires_at or self.now + timedelta(days=1),
        )

    def test_taxonomy_covers_m01_to_m08_and_only_m08_can_be_approved(self) -> None:
        observations = (
            self._observation("m01", MigrationParityDimension.OWNER_SUBJECT_ID),
            self._observation("m02", MigrationParityDimension.RESOURCE_IDENTITY),
            self._observation("m03", MigrationParityDimension.VISIBILITY),
            self._observation("m04", MigrationParityDimension.TERMINAL_DECISION),
            self._observation("m05", MigrationParityDimension.CANONICAL_CONTENT_HASH),
            self._observation("m06", MigrationParityDimension.CITATION_LINEAGE),
            self._observation("m07", MigrationParityDimension.PROJECTION_CHECKPOINT),
            self._observation("m08", MigrationParityDimension.DISPLAY_NORMALIZATION),
        )
        report = build_migration_parity_shadow_report(
            window=self._window(len(observations)),
            observations=reversed(observations),
            allowances=(self._approved_allowance(observations[-1]),),
            as_of=self.now,
        )
        summary = report.value_free_summary()

        self.assertEqual(
            summary["mismatchCountsByCode"],
            {"M01": 1, "M02": 1, "M03": 1, "M04": 1, "M05": 1, "M06": 1, "M07": 1, "M08": 1},
        )
        self.assertEqual(summary["blockingMismatchCount"], 7)
        self.assertEqual(summary["approvedM08DifferenceCount"], 1)
        self.assertEqual(summary["unresolvedM08DifferenceCount"], 0)
        self.assertFalse(summary["readyForNextGate"])
        self.assertFalse(summary["cutoverAllowed"])
        self.assertTrue(summary["shadowOnly"])
        self.assertEqual(summary["commandEffectExecutionCount"], 0)
        self.assertEqual(summary["objectCopyExecutionCount"], 0)
        self.assertEqual(summary["providerCallCount"], 0)
        self.assertFalse(summary["providerCostCharged"])
        self.assertEqual(
            set(MigrationParityMismatchCode),
            {item.observation.mismatch_code for item in report.mismatches},
        )

    def test_matching_corpus_is_deterministic_and_ready_without_effects(self) -> None:
        observations = (
            self._observation(
                "read",
                MigrationParityDimension.RESOURCE_IDENTITY,
                legacy="same",
                v4="same",
            ),
            self._observation(
                "projection",
                MigrationParityDimension.PROJECTION_CHECKPOINT,
                legacy="same-projection",
                v4="same-projection",
                surface=MigrationParitySurface.PROJECTION,
            ),
            self._observation(
                "command",
                MigrationParityDimension.COMMAND_EFFECT_PLAN,
                legacy="same-command-plan",
                v4="same-command-plan",
                surface=MigrationParitySurface.COMMAND,
            ),
        )
        first = build_migration_parity_shadow_report(
            window=self._window(3), observations=observations, as_of=self.now
        )
        second = build_migration_parity_shadow_report(
            window=self._window(3), observations=reversed(observations), as_of=self.now
        )
        summary = first.value_free_summary()

        self.assertEqual(first.report_hash, second.report_hash)
        self.assertEqual(summary["matchCount"], 3)
        self.assertEqual(summary["mismatchCount"], 0)
        self.assertTrue(summary["readyForNextGate"])
        self.assertEqual(summary["writeOperationCount"], 0)
        self.assertFalse(summary["authorityEpochChanged"])
        self.assertFalse(summary["legacyWriterRetired"])

    def test_m08_requires_a_current_bound_allowance(self) -> None:
        observation = self._observation(
            "display", MigrationParityDimension.DISPLAY_NORMALIZATION
        )
        missing = build_migration_parity_shadow_report(
            window=self._window(1), observations=(observation,), as_of=self.now
        )
        expired = build_migration_parity_shadow_report(
            window=self._window(1),
            observations=(observation,),
            allowances=(
                self._approved_allowance(
                    observation, expires_at=self.now - timedelta(seconds=1)
                ),
            ),
            as_of=self.now,
        )
        approved = build_migration_parity_shadow_report(
            window=self._window(1),
            observations=(observation,),
            allowances=(self._approved_allowance(observation),),
            as_of=self.now,
        )

        self.assertEqual(missing.value_free_summary()["unresolvedM08DifferenceCount"], 1)
        self.assertEqual(expired.mismatches[0].allowance_status, "expired")
        self.assertFalse(expired.ready_for_next_gate)
        self.assertTrue(approved.ready_for_next_gate)
        self.assertEqual(approved.mismatches[0].allowance_status, "approved")

    def test_allowance_cannot_waive_m01_to_m07_or_target_a_match(self) -> None:
        blocker = self._observation("owner", MigrationParityDimension.OWNER_SUBJECT_ID)
        with self.assertRaises(OwnerTruthMigrationParityShadowError):
            build_migration_parity_shadow_report(
                window=self._window(1),
                observations=(blocker,),
                allowances=(self._approved_allowance(blocker),),
                as_of=self.now,
            )

        matching_m08 = self._observation(
            "matching-m08",
            MigrationParityDimension.DISPLAY_NORMALIZATION,
            legacy="same",
            v4="same",
        )
        with self.assertRaises(OwnerTruthMigrationParityShadowError):
            build_migration_parity_shadow_report(
                window=self._window(1),
                observations=(matching_m08,),
                allowances=(self._approved_allowance(matching_m08),),
                as_of=self.now,
            )

    def test_duplicate_is_counted_but_rebinding_same_identity_fails_closed(self) -> None:
        observation = self._observation("same", MigrationParityDimension.OBJECT_COPY_HASH)
        duplicate = build_migration_parity_shadow_report(
            window=self._window(1),
            observations=(observation, observation),
            as_of=self.now,
        )
        self.assertEqual(duplicate.duplicate_input_count, 1)
        self.assertEqual(len(duplicate.observations), 1)

        rebound = self._observation(
            "same",
            MigrationParityDimension.OBJECT_COPY_HASH,
            legacy="changed",
        )
        with self.assertRaises(OwnerTruthMigrationParityShadowConflict):
            build_migration_parity_shadow_report(
                window=self._window(1),
                observations=(observation, rebound),
                as_of=self.now,
            )

    def test_denominator_and_raw_value_boundaries_fail_closed(self) -> None:
        observation = self._observation("single", MigrationParityDimension.COUNT)
        with self.assertRaises(OwnerTruthMigrationParityShadowError):
            build_migration_parity_shadow_report(
                window=self._window(2), observations=(observation,), as_of=self.now
            )

        with self.assertRaises(OwnerTruthMigrationParityShadowError):
            MigrationParityObservation(
                sample_id_hash="raw-owner-id",
                surface=MigrationParitySurface.READ,
                dimension=MigrationParityDimension.OWNER_SUBJECT_ID,
                legacy_value_hash=_digest("legacy"),
                v4_value_hash=_digest("v4"),
            )
        with self.assertRaises(OwnerTruthMigrationParityShadowError):
            MigrationParityObservation(
                sample_id_hash=_digest("both-absent"),
                surface=MigrationParitySurface.READ,
                dimension=MigrationParityDimension.OPTIONAL_LEGACY_METADATA,
                legacy_value_hash=None,
                v4_value_hash=None,
            )
        with self.assertRaises(OwnerTruthMigrationParityShadowError):
            self._observation(
                "m08-command",
                MigrationParityDimension.DISPLAY_NORMALIZATION,
                surface=MigrationParitySurface.COMMAND,
            )

    def test_every_declared_dimension_has_an_explicit_taxonomy_mapping(self) -> None:
        codes = set()
        for index, dimension in enumerate(MigrationParityDimension):
            observation = self._observation("dimension-%d" % index, dimension)
            codes.add(observation.mismatch_code)
        self.assertEqual(codes, set(MigrationParityMismatchCode))


if __name__ == "__main__":
    unittest.main()

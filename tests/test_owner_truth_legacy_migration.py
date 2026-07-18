from __future__ import annotations

from hashlib import sha256
import unittest

from app.domain.owner_truth.legacy_migration import (
    LegacyEvidenceState,
    LegacyMigrationClassification,
    LegacyMigrationDisposition,
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    OwnerTruthLegacyMigrationError,
    build_legacy_migration_inventory,
    classify_legacy_record,
)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class OwnerTruthLegacyMigrationTests(unittest.TestCase):
    def _record(self, **overrides) -> LegacyMigrationRecord:
        payload = {
            "domain": LegacyMigrationDomain.MEMORY,
            "legacy_id": "legacy-memory-001",
            "record_hash": digest("legacy private memory payload"),
            "canonical_owner_subject_id": "owner-a",
            "observed_owner_subject_id": "owner-a",
            "authority_state": "active",
            "source_evidence_id": None,
            "decision_receipt_id": None,
            "observed_only": False,
        }
        payload.update(overrides)
        return LegacyMigrationRecord(**payload)

    def test_complete_owner_source_and_decision_evidence_is_only_migration_eligible_path(self) -> None:
        entry = classify_legacy_record(
            self._record(
                source_evidence_id="source-opaque-id",
                decision_receipt_id="receipt-opaque-id",
                decision_is_terminal=True,
                revision_evidence_id="revision-opaque-id",
            )
        )

        self.assertEqual(entry.classification, LegacyMigrationClassification.PROVEN_CONFIRMED)
        self.assertEqual(entry.disposition, LegacyMigrationDisposition.MEMORY_V1_ELIGIBLE)
        self.assertEqual(entry.owner_evidence_state, LegacyEvidenceState.VERIFIED)
        self.assertEqual(entry.source_evidence_state, LegacyEvidenceState.VERIFIED)
        self.assertEqual(entry.decision_evidence_state, LegacyEvidenceState.VERIFIED)

    def test_unconfirmed_archive_and_kb_observations_can_only_become_candidates(self) -> None:
        archive = classify_legacy_record(
            self._record(
                domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                legacy_id="archive-opaque-id",
                record_hash=digest("photo metadata only"),
            )
        )
        kb = classify_legacy_record(
            self._record(
                domain=LegacyMigrationDomain.KB_SNAPSHOT,
                legacy_id="kb-snapshot-opaque-id",
                record_hash=digest("legacy graph"),
            )
        )
        kb_receipt = classify_legacy_record(
            self._record(
                domain=LegacyMigrationDomain.KB_RECEIPT,
                legacy_id="kb-receipt-opaque-id",
                record_hash=digest("legacy operation receipt"),
            )
        )

        self.assertEqual(archive.classification, LegacyMigrationClassification.OBSERVED_CANDIDATE)
        self.assertEqual(kb.classification, LegacyMigrationClassification.OBSERVED_CANDIDATE)
        self.assertEqual(kb_receipt.classification, LegacyMigrationClassification.OBSERVED_CANDIDATE)
        self.assertEqual(archive.disposition, LegacyMigrationDisposition.CANDIDATE_ONLY)
        self.assertEqual(kb.disposition, LegacyMigrationDisposition.CANDIDATE_ONLY)
        self.assertEqual(kb_receipt.disposition, LegacyMigrationDisposition.CANDIDATE_ONLY)

    def test_owner_ambiguity_quarantines_before_any_candidate_or_memory_path(self) -> None:
        entry = classify_legacy_record(
            self._record(
                domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                observed_owner_subject_id="other-owner",
                source_evidence_id="source-opaque-id",
                decision_receipt_id="receipt-opaque-id",
                decision_is_terminal=True,
                revision_evidence_id="revision-opaque-id",
            )
        )

        self.assertEqual(entry.classification, LegacyMigrationClassification.QUARANTINE)
        self.assertEqual(entry.disposition, LegacyMigrationDisposition.QUARANTINE)
        self.assertEqual(entry.owner_evidence_state, LegacyEvidenceState.AMBIGUOUS)
        self.assertEqual(entry.reason_code, "legacyOwnerEvidenceMismatch")

    def test_conversation_cache_is_never_promoted_to_owner_memory(self) -> None:
        entry = classify_legacy_record(
            self._record(
                domain=LegacyMigrationDomain.CONVERSATION_CACHE,
                legacy_id="conversation-cache-opaque-id",
                record_hash=digest("assistant text must not migrate"),
                source_evidence_id="source-opaque-id",
                decision_receipt_id="receipt-opaque-id",
            )
        )

        self.assertEqual(entry.classification, LegacyMigrationClassification.DO_NOT_MIGRATE)
        self.assertEqual(entry.disposition, LegacyMigrationDisposition.EXCLUDED)
        self.assertEqual(entry.owner_evidence_state, LegacyEvidenceState.NOT_APPLICABLE)

    def test_receipt_without_terminal_decision_and_revision_never_promotes_memory(self) -> None:
        entry = classify_legacy_record(
            self._record(
                source_evidence_id="source-opaque-id",
                decision_receipt_id="receipt-opaque-id",
                decision_is_terminal=False,
            )
        )

        self.assertEqual(entry.classification, LegacyMigrationClassification.NEEDS_REVIEW)
        self.assertEqual(entry.disposition, LegacyMigrationDisposition.REVIEW_QUEUE)
        self.assertEqual(entry.decision_evidence_state, LegacyEvidenceState.MISSING)

    def test_inventory_is_deterministic_value_free_and_marks_unavailable_conversation_storage(self) -> None:
        secret_legacy_id = "archive-raw-private-identifier"
        first = build_legacy_migration_inventory(
            vault_id="owner-a",
            classifier_version="legacy-classifier-v1",
            records=(
                self._record(
                    domain=LegacyMigrationDomain.KB_CHANGE,
                    legacy_id="kb-change-opaque-id",
                    record_hash=digest("change payload"),
                    observed_only=True,
                ),
                self._record(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id=secret_legacy_id,
                    record_hash=digest("archive payload"),
                ),
            ),
            unavailable_domains=(LegacyMigrationDomain.CONVERSATION_CACHE,),
        )
        replay = build_legacy_migration_inventory(
            vault_id="owner-a",
            classifier_version="legacy-classifier-v1",
            records=tuple(reversed((
                self._record(
                    domain=LegacyMigrationDomain.KB_CHANGE,
                    legacy_id="kb-change-opaque-id",
                    record_hash=digest("change payload"),
                    observed_only=True,
                ),
                self._record(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id=secret_legacy_id,
                    record_hash=digest("archive payload"),
                ),
            ))),
            unavailable_domains=(LegacyMigrationDomain.CONVERSATION_CACHE,),
        )

        self.assertEqual(first.inventory_hash, replay.inventory_hash)
        self.assertEqual(first.summary()["entryCount"], 2)
        self.assertEqual(first.summary()["unavailableDomains"], ["conversationCache"])
        self.assertNotIn(secret_legacy_id, str(first.summary()))
        self.assertNotIn(secret_legacy_id, str([entry.summary() for entry in first.entries]))

    def test_duplicate_legacy_identity_fails_closed(self) -> None:
        record = self._record()
        with self.assertRaises(OwnerTruthLegacyMigrationError):
            build_legacy_migration_inventory(
                vault_id="owner-a",
                classifier_version="legacy-classifier-v1",
                records=(record, record),
            )


if __name__ == "__main__":
    unittest.main()

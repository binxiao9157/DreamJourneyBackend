from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/0060_legacy_identity_inbox_bridge.sql"
METADATA = ROOT / "db/migrations/0060_legacy_identity_inbox_bridge.json"


class LegacyIdentityInboxBridgeMigrationContractTests(unittest.TestCase):
    def test_bridge_is_additive_default_off_and_never_backfills_accounts(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        metadata = METADATA.read_text(encoding="utf-8")

        self.assertIn("create table legacy_identity_aliases", sql)
        self.assertIn("references users(id)", sql)
        self.assertIn("references subjects(id)", sql)
        self.assertIn("references owner_truth.vaults(vault_id)", sql)
        self.assertIn("references identity_proofs(id)", sql)
        self.assertIn("claim_pending", sql)
        self.assertIn("verified", sql)
        self.assertIn("legacy_identity_aliases_validate_insert", sql)
        self.assertIn("legacy_identity_aliases_validate_update", sql)
        self.assertNotIn("insert into legacy_identity_aliases", sql)
        self.assertNotIn("update users", sql)
        self.assertNotIn("mailbox_letters", sql)
        self.assertIn('"legacyIdentityAliasBridgeV1": false', metadata)
        self.assertIn('"legacyInboxAccountResolverV1": false', metadata)

    def test_verified_alias_must_be_bound_to_matching_active_identity_and_vault(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()

        self.assertIn("proof_subject_id is distinct from new.subject_id", sql)
        self.assertIn("subject_status is distinct from 'active'", sql)
        self.assertIn("vault_status is distinct from 'active'", sql)
        self.assertIn("vault_owner_subject_id is distinct from new.subject_id", sql)
        self.assertIn("legacy identity alias coordinates are immutable", sql)
        self.assertIn("legacy identity alias row version must advance by one", sql)


if __name__ == "__main__":
    unittest.main()

"""Static contract tests for the additive Voice/DH Authority schema."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class VoiceDHAuthorityMigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = next(
            item for item in load_migrations(default_migrations_dir()) if item.version == "0042"
        )
        self.manifest = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_additive_default_off_schema_covers_all_future_authority_records(self) -> None:
        self.assertEqual(self.migration.name, "voice_dh_authority_schema")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertFalse(self.manifest["releaseFlags"]["voiceDhAuthorityAdmission"])
        for table in (
            "voice_dh.voice_profile_versions",
            "voice_dh.sample_intents",
            "voice_dh.generated_audio_intents",
            "voice_dh.dh_session_admissions",
            "voice_dh.authority_receipts",
        ):
            self.assertIn(f"CREATE TABLE {table}", self.migration.sql)

    def test_schema_binds_current_vault_authority_and_remains_append_only(self) -> None:
        self.assertIn("voice_dh.bind_vault_authority", self.migration.sql)
        self.assertIn("owner_truth.vaults", self.migration.sql)
        self.assertIn("voice_dh.assert_profile_parent", self.migration.sql)
        self.assertIn("voice_dh.assert_receipt_resource", self.migration.sql)
        self.assertIn("voice_dh.append_only", self.migration.sql)
        self.assertIn("BEFORE UPDATE OR DELETE", self.migration.sql)

    def test_schema_prohibits_provider_credentials_and_content_payloads(self) -> None:
        normalized = self.migration.sql.lower()
        for forbidden in (
            "provider_speaker_id",
            "audio_base64",
            "access_token",
            "secret_key",
            "object_url",
            "preview_url",
            "text_content",
        ):
            self.assertNotIn(forbidden, normalized)
        for forbidden_column in ("credential", "token", "audio_base64", "text_content"):
            self.assertNotIn(f"\n    {forbidden_column} ", normalized)
        self.assertIn("status in ('blocked', 'notaccepted', 'revoked', 'legacyobserved')", normalized)
        self.assertIn("status in ('blocked', 'notgenerated', 'revoked')", normalized)


if __name__ == "__main__":
    unittest.main()

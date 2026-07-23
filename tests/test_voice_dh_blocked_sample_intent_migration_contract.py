"""Static contract tests for the G0 blocked Voice/DH sample-intent receipt migration."""

from __future__ import annotations

import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class VoiceDHBlockedSampleIntentMigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0043"
        )
        self.manifest = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_migration_is_backward_compatible_and_default_off(self) -> None:
        self.assertEqual(self.migration.name, "voice_dh_blocked_sample_intent_receipts")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "backwardCompatible")
        self.assertFalse(self.manifest["releaseFlags"]["voiceDhBlockedSampleIntent"])
        self.assertFalse(self.manifest["releaseFlags"]["voiceClone"])
        self.assertFalse(self.manifest["releaseFlags"]["digitalHuman"])

    def test_only_blocked_training_profile_can_parent_a_sample_intent(self) -> None:
        normalized = self.migration.sql.lower()
        self.assertIn("voice_dh.assert_blocked_sample_intent_parent", normalized)
        self.assertIn("profile_purpose is distinct from 'training'", normalized)
        self.assertIn("profile_provider is distinct from 'volcenginevoiceclone'", normalized)
        self.assertIn("profile_status is distinct from 'blocked'", normalized)
        self.assertIn("profile_subject_id is distinct from profile_owner_subject_id", normalized)
        self.assertIn("profile_actor_subject_id is distinct from profile_owner_subject_id", normalized)
        self.assertIn("new.status is distinct from 'blocked'", normalized)
        self.assertIn("new.sample_format not in ('wav', 'mp3', 'm4a')", normalized)
        self.assertIn("new.duration_millis < 1", normalized)
        self.assertIn("voice_dh_sample_intents_bind_blocked_training_profile", normalized)

    def test_receipts_accept_only_blocked_profile_or_sample_intent_resources(self) -> None:
        normalized = self.migration.sql.lower()
        self.assertIn("new.resource_kind = 'voiceprofileversion'", normalized)
        self.assertIn("new.resource_kind = 'sampleintent'", normalized)
        self.assertIn("parent_profile_status is distinct from 'blocked'", normalized)
        self.assertIn("resource_status is distinct from 'blocked'", normalized)
        self.assertIn("new.operation is distinct from 'blockedadmission'", normalized)
        self.assertNotIn("generatedaudiointent", normalized)
        self.assertNotIn("dhsessionadmission", normalized)

    def test_migration_does_not_add_media_or_provider_secret_storage(self) -> None:
        normalized = self.migration.sql.lower()
        for forbidden in (
            "audio_base64",
            "access_token",
            "secret_key",
            "object_url",
            "preview_url",
            "text_content",
            "provider_speaker_id",
        ):
            self.assertNotIn(forbidden, normalized)


if __name__ == "__main__":
    unittest.main()

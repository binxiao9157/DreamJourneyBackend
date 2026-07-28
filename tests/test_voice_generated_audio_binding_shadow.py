"""G0 tests for the default-off GeneratedAudio binding observer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import unittest
from uuid import uuid4

from app.services.voice_dh_authority import (
    VoiceDHAuthorityContext,
    VoiceDHProvider,
    VoiceDHPurpose,
    VoiceProfileVersionAdmissionCommand,
    VoiceProfileVersionAuthorityRecord,
)
from app.services.voice_generated_audio_binding_shadow import (
    VoiceGeneratedAudioBindingCommand,
    VoiceGeneratedAudioBindingDisposition,
    VoiceGeneratedAudioBindingError,
    VoiceGeneratedAudioBindingShadow,
    VoiceGeneratedAudioOutputMode,
)


NOW = datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**changes: object) -> VoiceDHAuthorityContext:
    values: dict[str, object] = {
        "vault_id": "vault-generated-audio-owner",
        "owner_subject_id": "owner-generated-audio",
        "actor_subject_id": "owner-generated-audio",
        "authority_epoch": 7,
    }
    values.update(changes)
    return VoiceDHAuthorityContext(**values)  # type: ignore[arg-type]


def _profile_command(**changes: object) -> VoiceProfileVersionAdmissionCommand:
    values: dict[str, object] = {
        "command_id": "voice-profile-admission-generated-audio-001",
        "profile_id": "voice-profile-generated-audio-001",
        "profile_version": 3,
        "subject_id": "owner-generated-audio",
        "purpose": VoiceDHPurpose.PRIVATE_SYNTHESIS,
        "provider": VoiceDHProvider.VOLCENGINE_VOICE_CLONE,
        "policy_version": "voicePolicyV4",
        "consent_receipt_hash": _digest("consent"),
        "purpose_grant_hash": _digest("purpose-grant"),
        "payload_hash": _digest("profile-command"),
    }
    values.update(changes)
    return VoiceProfileVersionAdmissionCommand(**values)  # type: ignore[arg-type]


def _profile_authority(
    *,
    context: VoiceDHAuthorityContext | None = None,
    command: VoiceProfileVersionAdmissionCommand | None = None,
) -> VoiceProfileVersionAuthorityRecord:
    return VoiceProfileVersionAuthorityRecord(
        id=str(uuid4()),
        context=context or _context(),
        command=command or _profile_command(),
    )


def _binding(
    profile_authority: VoiceProfileVersionAuthorityRecord,
    **changes: object,
) -> VoiceGeneratedAudioBindingCommand:
    command = profile_authority.command
    values: dict[str, object] = {
        "command_id": "generated-audio-binding-001",
        "profile_version_id": profile_authority.id,
        "profile_id": command.profile_id,
        "profile_version": command.profile_version,
        "purpose": VoiceDHPurpose.DH_AUDIO_DRIVE,
        "provider": VoiceDHProvider.VOLCENGINE_VOICE_CLONE,
        "policy_version": command.policy_version,
        "source_binding_hash": _digest("echo-answer-version-001"),
        "text_hash": _digest("rendered-answer-001"),
        "request_hash": _digest("generated-audio-request-001"),
        "output_mode": VoiceGeneratedAudioOutputMode.TENCENT_AUDIO_DRIVE,
        "audio_format": "pcm16kMono",
        "sample_rate": 16000,
        "channel_count": 1,
        "issued_at": NOW - timedelta(seconds=10),
        "expires_at": NOW + timedelta(seconds=120),
    }
    values.update(changes)
    return VoiceGeneratedAudioBindingCommand(**values)  # type: ignore[arg-type]


class VoiceGeneratedAudioBindingShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_the_binding_inputs(self) -> None:
        observer = VoiceGeneratedAudioBindingShadow()

        result = observer.observe(
            context=object(),
            profile_authority=object(),
            command=object(),
        )

        self.assertEqual(result.disposition, VoiceGeneratedAudioBindingDisposition.SHADOW_DISABLED)
        self.assertFalse(result.value_free_summary()["generatedAudioPersisted"])

    def test_matching_private_profile_and_pcm_binding_remains_blocked_and_value_free(self) -> None:
        context = _context()
        authority = _profile_authority(context=context)
        binding = _binding(authority)

        result = VoiceGeneratedAudioBindingShadow().observe(
            context=context,
            profile_authority=authority,
            command=binding,
            enabled=True,
            now=NOW,
        )

        self.assertEqual(result.disposition, VoiceGeneratedAudioBindingDisposition.BLOCKED)
        self.assertIn("profileAuthorityBlocked", result.reason_codes)
        self.assertIn("g0NoGeneratedAudioPersistence", result.reason_codes)
        summary = result.value_free_summary()
        self.assertFalse(summary["generatedAudioPersisted"])
        self.assertFalse(summary["audioBytesStored"])
        self.assertFalse(summary["providerEffectAllowed"])
        self.assertFalse(summary["providerEffectPerformed"])
        self.assertFalse(summary["cachePromotionAllowed"])
        self.assertFalse(summary["releaseVisible"])
        for forbidden in (
            authority.id,
            binding.source_binding_hash,
            binding.text_hash,
            binding.request_hash,
            binding.binding_fingerprint,
            "voice-profile-generated-audio-001",
        ):
            self.assertNotIn(forbidden, repr(summary))

    def test_cross_owner_or_profile_version_mismatch_never_promotes_binding(self) -> None:
        authority = _profile_authority()
        foreign_context = _context(owner_subject_id="other-owner", actor_subject_id="other-owner")
        foreign_result = VoiceGeneratedAudioBindingShadow().observe(
            context=foreign_context,
            profile_authority=authority,
            command=_binding(authority),
            enabled=True,
            now=NOW,
        )
        self.assertEqual(foreign_result.disposition, VoiceGeneratedAudioBindingDisposition.BLOCKED)
        self.assertIn("profileAuthorityContextMismatch", foreign_result.reason_codes)
        self.assertFalse(foreign_result.value_free_summary()["generatedAudioPersisted"])

        mismatched_binding = _binding(authority, profile_version_id=str(uuid4()))
        mismatched_result = VoiceGeneratedAudioBindingShadow().observe(
            context=_context(),
            profile_authority=authority,
            command=mismatched_binding,
            enabled=True,
            now=NOW,
        )
        self.assertIn("profileVersionBindingMismatch", mismatched_result.reason_codes)
        self.assertFalse(mismatched_result.value_free_summary()["cachePromotionAllowed"])

    def test_stable_command_replay_and_conflict_are_observed_without_a_receipt(self) -> None:
        context = _context()
        authority = _profile_authority(context=context)
        binding = _binding(authority)
        observer = VoiceGeneratedAudioBindingShadow()

        first = observer.observe(
            context=context,
            profile_authority=authority,
            command=binding,
            enabled=True,
            now=NOW,
        )
        replay = observer.observe(
            context=context,
            profile_authority=authority,
            command=binding,
            enabled=True,
            now=NOW,
        )
        conflict = observer.observe(
            context=context,
            profile_authority=authority,
            command=_binding(authority, request_hash=_digest("changed-request")),
            enabled=True,
            now=NOW,
        )

        self.assertNotIn("stableCommandReplayObserved", first.reason_codes)
        self.assertIn("stableCommandReplayObserved", replay.reason_codes)
        self.assertIn("stableCommandHashConflict", conflict.reason_codes)
        self.assertFalse(conflict.value_free_summary()["generatedAudioPersisted"])

    def test_audio_drive_and_public_visitor_shapes_are_rejected_at_contract_construction(self) -> None:
        authority = _profile_authority()
        with self.assertRaises(VoiceGeneratedAudioBindingError):
            _binding(authority, audio_format="wav")
        with self.assertRaises(VoiceGeneratedAudioBindingError):
            _binding(authority, purpose=VoiceDHPurpose.PRIVATE_SYNTHESIS)
        with self.assertRaises(VoiceGeneratedAudioBindingError):
            _binding(authority, purpose=VoiceDHPurpose.VISITOR_PUBLIC_VOICE)

    def test_profile_or_purpose_changes_produce_distinct_non_public_binding_fingerprints(self) -> None:
        authority = _profile_authority()
        drive = _binding(authority)
        preview = _binding(
            authority,
            command_id="generated-audio-binding-preview",
            purpose=VoiceDHPurpose.PREVIEW,
            output_mode=VoiceGeneratedAudioOutputMode.DEFAULT,
            audio_format="mp3",
            sample_rate=24000,
            channel_count=1,
        )

        self.assertNotEqual(drive.binding_fingerprint, preview.binding_fingerprint)
        self.assertNotEqual(drive.purpose, preview.purpose)

    def test_module_does_not_import_provider_network_storage_or_live_tts(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/services/voice_generated_audio_binding_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services.tts",
            "requests",
            "httpx",
            "boto3",
            "urllib.request",
            "psycopg",
            "sqlite3",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

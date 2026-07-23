"""G0 tests for the default-deny Voice/DH Authority profile admission seam."""

from __future__ import annotations

import unittest

from app.services.voice_dh_authority import (
    InMemoryVoiceDHAuthorityRepository,
    VoiceDHAuthorityAccessDenied,
    VoiceDHAuthorityConflict,
    VoiceDHAuthorityContext,
    VoiceDHAuthorityDisposition,
    VoiceDHAuthorityError,
    VoiceDHAuthorityService,
    VoiceDHProvider,
    VoiceDHPurpose,
    VoiceProfileVersionAdmissionCommand,
)


def _context(*, actor: str = "owner-voice-authority") -> VoiceDHAuthorityContext:
    return VoiceDHAuthorityContext(
        vault_id="vault-voice-authority",
        owner_subject_id="owner-voice-authority",
        actor_subject_id=actor,
        authority_epoch=0,
    )


def _command(
    *,
    command_id: str = "voice-profile-admission-001",
    profile_id: str = "voice-profile-owner-001",
    profile_version: int = 1,
    subject_id: str = "owner-voice-authority",
    payload_hash: str = "c" * 64,
) -> VoiceProfileVersionAdmissionCommand:
    return VoiceProfileVersionAdmissionCommand(
        command_id=command_id,
        profile_id=profile_id,
        profile_version=profile_version,
        subject_id=subject_id,
        purpose=VoiceDHPurpose.PRIVATE_SYNTHESIS,
        provider=VoiceDHProvider.VOLCENGINE_VOICE_CLONE,
        policy_version="voice-policy-v1",
        consent_receipt_hash="a" * 64,
        purpose_grant_hash="b" * 64,
        payload_hash=payload_hash,
    )


class VoiceDHAuthorityServiceTests(unittest.TestCase):
    def test_disabled_service_does_not_inspect_or_persist_a_profile_admission(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository)

        result = service.admit_self_profile_version(context=_context(), command=_command())

        self.assertEqual(result.disposition, VoiceDHAuthorityDisposition.SHADOW_DISABLED)
        self.assertEqual(repository.snapshot(), {"profileVersionCount": 0, "receiptCount": 0})
        summary = result.value_free_summary()
        self.assertFalse(summary["authorityRecordWritten"])
        self.assertFalse(summary["providerEffectAllowed"])
        self.assertFalse(summary["providerEffectPerformed"])
        self.assertFalse(summary["releaseVisible"])
        self.assertNotIn("voice-profile-owner-001", repr(summary))

    def test_enabled_g0_admission_records_only_a_blocked_self_profile_and_receipt(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository, enabled=True)

        result = service.admit_self_profile_version(context=_context(), command=_command())

        self.assertEqual(result.disposition, VoiceDHAuthorityDisposition.BLOCKED_RECORDED)
        self.assertEqual(result.outcome, "created")
        self.assertIsNotNone(result.profile_version_id)
        self.assertIsNotNone(result.receipt_id)
        self.assertEqual(repository.snapshot(), {"profileVersionCount": 1, "receiptCount": 1})
        summary = result.value_free_summary()
        self.assertTrue(summary["authorityRecordWritten"])
        self.assertEqual(summary["status"], "blocked")
        self.assertFalse(summary["providerEffectAllowed"])
        self.assertFalse(summary["providerEffectPerformed"])
        self.assertFalse(summary["releaseVisible"])
        for forbidden in ("voice-profile-owner-001", "a" * 64, "b" * 64, "c" * 64):
            self.assertNotIn(forbidden, repr(summary))

    def test_identical_admission_is_deduplicated_but_changed_profile_version_conflicts(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository, enabled=True)
        context = _context()
        command = _command()

        created = service.admit_self_profile_version(context=context, command=command)
        replayed = service.admit_self_profile_version(context=context, command=command)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.profile_version_id, replayed.profile_version_id)
        self.assertEqual(repository.snapshot(), {"profileVersionCount": 1, "receiptCount": 1})

        with self.assertRaises(VoiceDHAuthorityConflict):
            service.admit_self_profile_version(
                context=context,
                command=_command(command_id="voice-profile-admission-002", payload_hash="d" * 64),
            )

    def test_g0_rejects_actor_or_subject_that_is_not_the_owner(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository, enabled=True)

        with self.assertRaises(VoiceDHAuthorityAccessDenied):
            service.admit_self_profile_version(context=_context(actor="family-member"), command=_command())
        with self.assertRaises(VoiceDHAuthorityAccessDenied):
            service.admit_self_profile_version(
                context=_context(),
                command=_command(subject_id="family-member"),
            )

        self.assertEqual(repository.snapshot(), {"profileVersionCount": 0, "receiptCount": 0})

    def test_provider_speaker_id_cannot_be_stored_as_an_internal_profile_id(self) -> None:
        with self.assertRaises(VoiceDHAuthorityError):
            _command(profile_id="S_providerSpeakerId")


if __name__ == "__main__":
    unittest.main()

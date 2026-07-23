"""G0 tests for the default-deny Voice/DH Authority profile admission seam."""

from __future__ import annotations

from hashlib import sha256
import unittest

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_dh_authority import (
    InMemoryVoiceDHAuthorityRepository,
    VoiceDHAuthorityAccessDenied,
    VoiceDHAuthorityConflict,
    VoiceDHAuthorityContext,
    VoiceDHAuthorityDisposition,
    VoiceDHAuthorityError,
    VoiceDHAuthorityService,
    VoiceDHBlockedSampleIntentCommand,
    VoiceDHProvider,
    VoiceDHPurpose,
    VoiceProfileVersionAdmissionCommand,
)
from app.services.voice_dh_consent_policy import (
    VoiceDHPurpose as ConsentVoiceDHPurpose,
    VoiceDHPurposeConsentDecision,
    VoiceDHPurposeConsentDisposition,
)
from app.services.voice_training_preflight_shadow import (
    VoiceTrainingCommandPreflightShadow,
    VoiceTrainingEvidenceReference,
    VoiceTrainingPreflightRequest,
    VoiceTrainingProfileReference,
    VoiceTrainingSampleDescriptor,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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


def _training_command(
    *,
    command_id: str = "voice-training-profile-admission-001",
    profile_id: str = "voice-training-profile-owner-001",
) -> VoiceProfileVersionAdmissionCommand:
    return VoiceProfileVersionAdmissionCommand(
        command_id=command_id,
        profile_id=profile_id,
        profile_version=1,
        subject_id="owner-voice-authority",
        purpose=VoiceDHPurpose.TRAINING,
        provider=VoiceDHProvider.VOLCENGINE_VOICE_CLONE,
        policy_version="voice-policy-v1",
        consent_receipt_hash="a" * 64,
        purpose_grant_hash="b" * 64,
        payload_hash="c" * 64,
    )


def _training_preflight_request(
    *,
    actor_subject_id: str = "owner-voice-authority",
    subject_id: str = "owner-voice-authority",
) -> VoiceTrainingPreflightRequest:
    return VoiceTrainingPreflightRequest(
        evaluationMode="syntheticG0",
        vaultId="vault-voice-authority",
        ownerSubjectId="owner-voice-authority",
        actorSubjectId=actor_subject_id,
        subjectId=subject_id,
        authorityEpoch=0,
        purpose=ConsentVoiceDHPurpose.TRAINING,
        provider="volcengineVoiceClone",
        region="cn-mainland",
        requestHash=_digest("voice-training-preflight-request"),
        profileReference=VoiceTrainingProfileReference(
            profileId="voice-training-profile-owner-001",
            profileVersion=1,
        ),
        consentDecision=VoiceDHPurposeConsentDecision(
            policyVersion="voice-policy-v1",
            purpose=ConsentVoiceDHPurpose.TRAINING,
            status=VoiceDHPurposeConsentDisposition.DENIED,
            reasonCodes=("syntheticG0Only",),
            syntheticPreconditionsSatisfied=True,
            requiredExternalGates=("G1", "G3", "G4"),
        ),
        subjectEligibility=SubjectEligibilityDecision(
            capability=HighRiskCapability.CLONED_VOICE,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        ),
        randomConsentStatementReference=VoiceTrainingEvidenceReference(
            referenceHash=_digest("statement"),
        ),
        livenessReference=VoiceTrainingEvidenceReference(referenceHash=_digest("liveness")),
        qualityReference=VoiceTrainingEvidenceReference(referenceHash=_digest("quality")),
        sampleDescriptor=VoiceTrainingSampleDescriptor(
            sourceObjectReferenceHash=_digest("source-object"),
            sampleHash=_digest("sample"),
            mediaFormat="wav",
            durationMilliseconds=12_000,
            byteLength=128_000,
        ),
    )


class VoiceDHAuthorityServiceTests(unittest.TestCase):
    def test_disabled_service_does_not_inspect_or_persist_a_profile_admission(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository)

        result = service.admit_self_profile_version(context=_context(), command=_command())

        self.assertEqual(result.disposition, VoiceDHAuthorityDisposition.SHADOW_DISABLED)
        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 0, "sampleIntentCount": 0, "receiptCount": 0},
        )
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
        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 1, "sampleIntentCount": 0, "receiptCount": 1},
        )
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
        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 1, "sampleIntentCount": 0, "receiptCount": 1},
        )

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

        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 0, "sampleIntentCount": 0, "receiptCount": 0},
        )

    def test_provider_speaker_id_cannot_be_stored_as_an_internal_profile_id(self) -> None:
        with self.assertRaises(VoiceDHAuthorityError):
            _command(profile_id="S_providerSpeakerId")

    def test_synthetic_preflight_records_only_a_blocked_sample_intent_and_receipt(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository, enabled=True)
        context = _context()
        profile_command = _training_command()
        profile = service.admit_self_profile_version(context=context, command=profile_command)
        self.assertIsNotNone(profile.profile_version_id)

        request = _training_preflight_request()
        decision = VoiceTrainingCommandPreflightShadow().observe(request, enabled=True)
        command = VoiceDHBlockedSampleIntentCommand.from_synthetic_preflight(
            context=context,
            profile_version_id=str(profile.profile_version_id),
            profile_command=profile_command,
            profile_policy_version="voice-policy-v1",
            command_id="voice-training-sample-intent-001",
            request=request,
            decision=decision,
        )
        created = service.admit_blocked_training_sample_intent(context=context, command=command)
        replayed = service.admit_blocked_training_sample_intent(context=context, command=command)

        self.assertEqual(created.disposition, VoiceDHAuthorityDisposition.BLOCKED_RECORDED)
        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.sample_intent_id, replayed.sample_intent_id)
        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 1, "sampleIntentCount": 1, "receiptCount": 2},
        )
        summary = created.value_free_summary()
        self.assertTrue(summary["sampleIntentWritten"])
        self.assertFalse(summary["sampleObjectCreated"])
        self.assertFalse(summary["trainingCommandCreated"])
        self.assertFalse(summary["providerEffectAllowed"])
        self.assertFalse(summary["providerEffectPerformed"])
        for forbidden in (request.sampleDescriptor.sampleHash, request.requestHash, "voice-training-sample-intent-001"):
            self.assertNotIn(forbidden, repr(summary))

    def test_sample_intent_rejects_family_or_incomplete_synthetic_preflight(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository, enabled=True)
        context = _context()
        profile_command = _training_command()
        profile = service.admit_self_profile_version(context=context, command=profile_command)
        self.assertIsNotNone(profile.profile_version_id)
        request = _training_preflight_request(
            actor_subject_id="family-member",
            subject_id="family-member",
        )
        decision = VoiceTrainingCommandPreflightShadow().observe(request, enabled=True)

        with self.assertRaises(VoiceDHAuthorityAccessDenied):
            VoiceDHBlockedSampleIntentCommand.from_synthetic_preflight(
                context=context,
                profile_version_id=str(profile.profile_version_id),
                profile_command=profile_command,
                profile_policy_version="voice-policy-v1",
                command_id="voice-training-sample-intent-family",
                request=request,
                decision=decision,
            )
        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 1, "sampleIntentCount": 0, "receiptCount": 1},
        )

    def test_sample_intent_requires_existing_blocked_training_profile(self) -> None:
        repository = InMemoryVoiceDHAuthorityRepository()
        service = VoiceDHAuthorityService(repository, enabled=True)
        context = _context()
        profile_command = _training_command()
        detached_profile = VoiceDHAuthorityService(
            InMemoryVoiceDHAuthorityRepository(),
            enabled=True,
        ).admit_self_profile_version(context=context, command=profile_command)
        self.assertIsNotNone(detached_profile.profile_version_id)
        request = _training_preflight_request()
        decision = VoiceTrainingCommandPreflightShadow().observe(request, enabled=True)
        command = VoiceDHBlockedSampleIntentCommand.from_synthetic_preflight(
            context=context,
            profile_version_id=str(detached_profile.profile_version_id),
            profile_command=profile_command,
            profile_policy_version="voice-policy-v1",
            command_id="voice-training-sample-intent-orphan",
            request=request,
            decision=decision,
        )

        with self.assertRaises(VoiceDHAuthorityConflict):
            service.admit_blocked_training_sample_intent(context=context, command=command)
        self.assertEqual(
            repository.snapshot(),
            {"profileVersionCount": 0, "sampleIntentCount": 0, "receiptCount": 0},
        )


if __name__ == "__main__":
    unittest.main()

"""G0 tests for the default-off server role voice selection vocabulary."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import unittest

from app.services.voice_dh_authority import VoiceDHPurpose
from app.services.voice_role_selection_shadow import (
    VoiceProfileResolutionState,
    VoiceRoleKind,
    VoiceRoleSelectionAuthorityContext,
    VoiceRoleSelectionCandidateSource,
    VoiceRoleSelectionDisposition,
    VoiceRoleSelectionError,
    VoiceRoleSelectionFallbackSource,
    VoiceRoleSelectionRequest,
    VoiceRoleSelectionShadow,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**changes: object) -> VoiceRoleSelectionAuthorityContext:
    values: dict[str, object] = {
        "vault_id": "vault-role-voice-owner",
        "owner_subject_id": "owner-role-voice",
        "actor_subject_id": "owner-role-voice",
        "authority_epoch": 4,
    }
    values.update(changes)
    return VoiceRoleSelectionAuthorityContext(**values)  # type: ignore[arg-type]


def _request(**changes: object) -> VoiceRoleSelectionRequest:
    values: dict[str, object] = {
        "request_id": "role-voice-selection-001",
        "runtime_id": "echo-runtime-role-voice-001",
        "runtime_generation": 3,
        "vault_id": "vault-role-voice-owner",
        "owner_subject_id": "owner-role-voice",
        "actor_subject_id": "owner-role-voice",
        "authority_epoch": 4,
        "role_subject_id": "owner-role-voice",
        "role_kind": VoiceRoleKind.SELF,
        "profile_id": "voice-profile-owner-001",
        "profile_version": 2,
        "profile_subject_id": "owner-role-voice",
        "profile_state": VoiceProfileResolutionState.READY,
        "purpose": VoiceDHPurpose.DH_AUDIO_DRIVE,
        "policy_version": "voice-role-policy-v1",
        "independent_consent_observed": True,
        "published_purpose_grant_observed": True,
        "quality_acceptance_observed": True,
        "request_hash": _digest("role-voice-request-001"),
    }
    values.update(changes)
    return VoiceRoleSelectionRequest(**values)  # type: ignore[arg-type]


class VoiceRoleSelectionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_the_role_or_profile(self) -> None:
        result = VoiceRoleSelectionShadow().observe(context=object(), request=object())

        self.assertEqual(result.disposition, VoiceRoleSelectionDisposition.SHADOW_DISABLED)
        self.assertFalse(result.value_free_summary()["providerEffectAllowed"])
        self.assertFalse(result.value_free_summary()["roleVoiceReceiptPersisted"])

    def test_self_default_ai_is_explicit_and_does_not_require_a_profile(self) -> None:
        request = _request(
            profile_id=None,
            profile_version=None,
            profile_subject_id=None,
            profile_state=VoiceProfileResolutionState.MISSING,
        )

        result = VoiceRoleSelectionShadow().observe(
            context=_context(),
            request=request,
            enabled=True,
        )

        self.assertEqual(result.disposition, VoiceRoleSelectionDisposition.BLOCKED)
        self.assertEqual(result.candidate_source, VoiceRoleSelectionCandidateSource.DEFAULT_AI)
        self.assertEqual(result.fallback_source, VoiceRoleSelectionFallbackSource.DEFAULT_AI)
        self.assertFalse(result.profile_candidate_eligible)
        self.assertFalse(result.clear_previous_profile)
        self.assertIn("selfDefaultAIRequested", result.reason_codes)

    def test_self_and_independently_published_living_profile_are_candidates_only(self) -> None:
        observer = VoiceRoleSelectionShadow()
        self_result = observer.observe(context=_context(), request=_request(), enabled=True)
        living_result = observer.observe(
            context=_context(),
            request=_request(
                request_id="role-voice-selection-living-001",
                runtime_id="echo-runtime-role-voice-living-001",
                role_kind=VoiceRoleKind.LIVING_ADULT,
                role_subject_id="adult-family-subject",
                profile_id="voice-profile-adult-001",
                profile_subject_id="adult-family-subject",
                request_hash=_digest("role-voice-request-living-001"),
            ),
            enabled=True,
        )

        self.assertEqual(self_result.candidate_source, VoiceRoleSelectionCandidateSource.SELF_PROFILE)
        self.assertTrue(self_result.profile_candidate_eligible)
        self.assertFalse(self_result.clear_previous_profile)
        self.assertIn("syntheticSelfProfileCandidateOnly", self_result.reason_codes)
        self.assertEqual(
            living_result.candidate_source,
            VoiceRoleSelectionCandidateSource.PUBLISHED_LIVING_PROFILE,
        )
        self.assertTrue(living_result.profile_candidate_eligible)
        self.assertFalse(living_result.clear_previous_profile)
        self.assertIn("syntheticPublishedLivingProfileCandidateOnly", living_result.reason_codes)
        for result in (self_result, living_result):
            summary = result.value_free_summary()
            self.assertFalse(summary["providerEffectAllowed"])
            self.assertFalse(summary["providerEffectPerformed"])
            self.assertFalse(summary["releaseVisible"])

    def test_family_relationship_minor_and_memorial_never_promote_a_profile(self) -> None:
        observer = VoiceRoleSelectionShadow()
        cases = {
            "family": _request(
                request_id="role-voice-family",
                runtime_id="echo-runtime-role-voice-family",
                role_kind=VoiceRoleKind.FAMILY_RELATION_ONLY,
                role_subject_id="family-subject",
                profile_id="voice-profile-family-001",
                profile_subject_id="family-subject",
                request_hash=_digest("role-voice-family"),
            ),
            "minor": _request(
                request_id="role-voice-minor",
                runtime_id="echo-runtime-role-voice-minor",
                role_kind=VoiceRoleKind.MINOR,
                role_subject_id="minor-subject",
                profile_id="voice-profile-minor-001",
                profile_subject_id="minor-subject",
                request_hash=_digest("role-voice-minor"),
            ),
            "memorial": _request(
                request_id="role-voice-memorial",
                runtime_id="echo-runtime-role-voice-memorial",
                role_kind=VoiceRoleKind.MEMORIAL_OR_DECEASED,
                role_subject_id="memorial-subject",
                profile_id="voice-profile-memorial-001",
                profile_subject_id="memorial-subject",
                request_hash=_digest("role-voice-memorial"),
            ),
        }
        expected = {
            "family": ("familyRelationshipNotVoiceGrant", VoiceRoleSelectionFallbackSource.NEUTRAL_DEFAULT_AI),
            "minor": ("minorRoleVoiceForbidden", VoiceRoleSelectionFallbackSource.TEXT_ONLY),
            "memorial": ("memorialOrDeceasedRoleVoiceForbidden", VoiceRoleSelectionFallbackSource.TEXT_ONLY),
        }

        for name, request in cases.items():
            with self.subTest(name=name):
                result = observer.observe(context=_context(), request=request, enabled=True)
                self.assertEqual(result.candidate_source, VoiceRoleSelectionCandidateSource.NONE)
                self.assertFalse(result.profile_candidate_eligible)
                self.assertTrue(result.clear_previous_profile)
                self.assertIn(expected[name][0], result.reason_codes)
                self.assertEqual(result.fallback_source, expected[name][1])

    def test_profile_revocation_deletion_or_subject_mismatch_clears_old_profile(self) -> None:
        cases = {
            "revoked": _request(profile_state=VoiceProfileResolutionState.REVOKED),
            "deleted": _request(profile_state=VoiceProfileResolutionState.DELETED),
            "wrongSubject": _request(profile_subject_id="other-subject"),
        }
        expected = {
            "revoked": "voiceProfileRevoked",
            "deleted": "voiceProfileDeleted",
            "wrongSubject": "voiceProfileSubjectMismatch",
        }

        for name, request in cases.items():
            with self.subTest(name=name):
                result = VoiceRoleSelectionShadow().observe(
                    context=_context(),
                    request=request,
                    enabled=True,
                )
                self.assertEqual(result.candidate_source, VoiceRoleSelectionCandidateSource.DEFAULT_AI)
                self.assertTrue(result.clear_previous_profile)
                self.assertIn(expected[name], result.reason_codes)
                self.assertFalse(result.profile_candidate_eligible)

    def test_rapid_switch_stale_or_same_generation_conflict_clears_previous_profile(self) -> None:
        observer = VoiceRoleSelectionShadow()
        current = _request(runtime_generation=9)
        newer = _request(
            request_id="role-voice-newer",
            runtime_generation=10,
            profile_id=None,
            profile_version=None,
            profile_subject_id=None,
            profile_state=VoiceProfileResolutionState.MISSING,
            request_hash=_digest("role-voice-newer"),
        )
        stale = _request(
            request_id="role-voice-stale",
            runtime_generation=9,
            request_hash=_digest("role-voice-stale"),
        )
        conflict = _request(
            request_id="role-voice-conflict",
            runtime_generation=10,
            role_kind=VoiceRoleKind.FAMILY_RELATION_ONLY,
            role_subject_id="family-subject",
            profile_id="voice-profile-family-001",
            profile_subject_id="family-subject",
            request_hash=_digest("role-voice-conflict"),
        )

        self.assertTrue(observer.observe(context=_context(), request=current, enabled=True).runtime_generation_accepted)
        self.assertTrue(observer.observe(context=_context(), request=newer, enabled=True).runtime_generation_accepted)
        stale_result = observer.observe(context=_context(), request=stale, enabled=True)
        conflict_result = observer.observe(context=_context(), request=conflict, enabled=True)

        for result, expected in (
            (stale_result, "staleRuntimeGeneration"),
            (conflict_result, "sameGenerationRoleSelectionConflict"),
        ):
            self.assertFalse(result.runtime_generation_accepted)
            self.assertTrue(result.clear_previous_profile)
            self.assertIn(expected, result.reason_codes)
            self.assertIn("staleOrConflictingRuntimeRoleSelection", result.reason_codes)

    def test_cross_owner_context_and_provider_speaker_id_fail_closed(self) -> None:
        cross_owner = VoiceRoleSelectionShadow().observe(
            context=_context(),
            request=_request(owner_subject_id="other-owner", actor_subject_id="other-owner"),
            enabled=True,
        )
        self.assertEqual(cross_owner.fallback_source, VoiceRoleSelectionFallbackSource.TEXT_ONLY)
        self.assertTrue(cross_owner.clear_previous_profile)
        self.assertIn("ownerVaultAuthorityMismatch", cross_owner.reason_codes)

        with self.assertRaises(VoiceRoleSelectionError):
            _request(profile_id="S_providerSpeakerId")

    def test_replay_is_observable_and_value_free_summary_has_no_profile_or_subject(self) -> None:
        observer = VoiceRoleSelectionShadow()
        request = _request()
        observer.observe(context=_context(), request=request, enabled=True)
        replay = observer.observe(context=_context(), request=request, enabled=True)
        summary = replay.value_free_summary()

        self.assertIn("stableRoleVoiceRequestReplayObserved", replay.reason_codes)
        self.assertIn("stableRuntimeGenerationReplayObserved", replay.reason_codes)
        for forbidden in (
            request.profile_id or "",
            request.profile_subject_id or "",
            request.role_subject_id,
            request.request_hash,
        ):
            self.assertNotIn(forbidden, repr(summary))

    def test_module_does_not_import_provider_network_storage_or_legacy_voice_routes(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/services/voice_role_selection_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services.tts",
            "app.services.voice_clone",
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

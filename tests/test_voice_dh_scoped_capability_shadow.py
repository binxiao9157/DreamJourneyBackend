"""G0 tests for the default-deny Voice/DH scoped capability observer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import unittest

from app.services.voice_dh_authority import VoiceDHProvider, VoiceDHPurpose
from app.services.voice_dh_scoped_capability_shadow import (
    ScopedCapabilityAdmissionRequest,
    ScopedCapabilityAdmissionShadow,
    ScopedCapabilityAuthorityContext,
    ScopedCapabilityShadowDisposition,
)


NOW = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**changes: object) -> ScopedCapabilityAuthorityContext:
    values: dict[str, object] = {
        "vault_id": "vault-capability-owner",
        "owner_subject_id": "owner-capability",
        "actor_subject_id": "owner-capability",
        "authority_epoch": 4,
        "audience": "iosEchoRuntime",
    }
    values.update(changes)
    return ScopedCapabilityAuthorityContext(**values)  # type: ignore[arg-type]


def _request(**changes: object) -> ScopedCapabilityAdmissionRequest:
    values: dict[str, object] = {
        "request_id": "voice-dh-capability-request-001",
        "vault_id": "vault-capability-owner",
        "owner_subject_id": "owner-capability",
        "actor_subject_id": "owner-capability",
        "subject_id": "owner-capability",
        "authority_epoch": 4,
        "purpose": VoiceDHPurpose.DH_AUDIO_DRIVE,
        "provider": VoiceDHProvider.TENCENT_DIGITAL_HUMAN,
        "resource": "digitalHumanSession",
        "nonce_hash": _digest("nonce-001"),
        "issued_at": NOW - timedelta(seconds=15),
        "expires_at": NOW + timedelta(seconds=45),
        "audience": "iosEchoRuntime",
        "one_time": True,
        "request_hash": _digest("request-001"),
    }
    values.update(changes)
    return ScopedCapabilityAdmissionRequest(**values)  # type: ignore[arg-type]


class ScopedCapabilityAdmissionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_or_observe_a_request(self) -> None:
        observer = ScopedCapabilityAdmissionShadow()
        disabled = observer.observe(context=object(), request=object())
        self.assertEqual(disabled.disposition, ScopedCapabilityShadowDisposition.SHADOW_DISABLED)

        observed = observer.observe(context=_context(), request=_request(), enabled=True, now=NOW)
        self.assertNotIn("stableRequestReplayObserved", observed.reason_codes)
        self.assertNotIn("nonceReplayObservedInMemory", observed.reason_codes)

    def test_matching_short_ttl_request_remains_blocked_and_value_free(self) -> None:
        result = ScopedCapabilityAdmissionShadow().observe(
            context=_context(),
            request=_request(),
            enabled=True,
            now=NOW,
        )
        self.assertEqual(result.disposition, ScopedCapabilityShadowDisposition.BLOCKED)
        self.assertIn("g0NoCapabilityIssuer", result.reason_codes)
        summary = result.value_free_summary()
        self.assertFalse(summary["capabilityIssued"])
        self.assertFalse(summary["nonceConsumed"])
        self.assertFalse(summary["providerEffectAllowed"])
        self.assertFalse(summary["providerEffectPerformed"])
        self.assertFalse(summary["replayProtectionPersistent"])
        self.assertFalse(summary["releaseVisible"])
        for forbidden in (_request().request_hash, _request().nonce_hash, "voice-dh-capability-request-001"):
            self.assertNotIn(forbidden, repr(summary))

    def test_expired_long_ttl_and_non_one_time_requests_remain_blocked(self) -> None:
        observer = ScopedCapabilityAdmissionShadow()
        cases = {
            "expired": _request(expires_at=NOW - timedelta(seconds=1)),
            "longTtl": _request(
                request_id="voice-dh-capability-request-long",
                nonce_hash=_digest("nonce-long"),
                expires_at=NOW + timedelta(seconds=600),
            ),
            "notOneTime": _request(
                request_id="voice-dh-capability-request-reusable",
                nonce_hash=_digest("nonce-reusable"),
                one_time=False,
            ),
        }
        expected = {
            "expired": "capabilityExpired",
            "longTtl": "ttlExceedsShadowMaximum",
            "notOneTime": "oneTimeCapabilityRequired",
        }
        for name, request in cases.items():
            with self.subTest(name=name):
                result = observer.observe(context=_context(), request=request, enabled=True, now=NOW)
                self.assertEqual(result.disposition, ScopedCapabilityShadowDisposition.BLOCKED)
                self.assertIn(expected[name], result.reason_codes)

    def test_cross_owner_vault_and_audience_never_admit_capability(self) -> None:
        result = ScopedCapabilityAdmissionShadow().observe(
            context=_context(),
            request=_request(
                vault_id="vault-other",
                actor_subject_id="other-actor",
                subject_id="other-subject",
                audience="androidRuntime",
            ),
            enabled=True,
            now=NOW,
        )
        self.assertEqual(result.disposition, ScopedCapabilityShadowDisposition.BLOCKED)
        self.assertIn("ownerVaultAuthorityMismatch", result.reason_codes)
        self.assertIn("audienceMismatch", result.reason_codes)
        self.assertFalse(result.value_free_summary()["capabilityIssued"])

    def test_replay_and_changed_hash_under_same_stable_request_are_detected(self) -> None:
        observer = ScopedCapabilityAdmissionShadow()
        first = _request()
        replay = observer.observe(context=_context(), request=first, enabled=True, now=NOW)
        same = observer.observe(context=_context(), request=first, enabled=True, now=NOW)
        changed = observer.observe(
            context=_context(),
            request=_request(request_hash=_digest("request-001-changed")),
            enabled=True,
            now=NOW,
        )
        self.assertNotIn("stableRequestReplayObserved", replay.reason_codes)
        self.assertIn("stableRequestReplayObserved", same.reason_codes)
        self.assertIn("nonceReplayObservedInMemory", same.reason_codes)
        self.assertIn("stableRequestHashConflict", changed.reason_codes)
        self.assertIn("nonceReplayObservedInMemory", changed.reason_codes)

    def test_invalid_context_is_value_free_and_default_denied(self) -> None:
        result = ScopedCapabilityAdmissionShadow().observe(
            context=_context(),
            request=object(),
            enabled=True,
            now=NOW,
        )
        self.assertEqual(result.disposition, ScopedCapabilityShadowDisposition.INVALID_CONTEXT)
        self.assertFalse(result.value_free_summary()["capabilityIssued"])

    def test_module_does_not_import_provider_network_or_persistence_clients(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/services/voice_dh_scoped_capability_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "requests",
            "httpx",
            "boto3",
            "urllib.request",
            "psycopg",
            "sqlite3",
        ):
            self.assertNotIn(forbidden, source)

    def test_deployed_smoke_remains_container_only_and_side_effect_free(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "scripts/backend-voice-dh-scoped-capability-shadow-deployed-smoke.py"
        ).read_text(encoding="utf-8")
        self.assertIn("must run inside the deployed API container", source)
        self.assertIn("ScopedCapabilityAdmissionShadow", source)
        for forbidden in (
            "requests",
            "httpx",
            "boto3",
            "urllib.request",
            "psycopg",
            "sqlite3",
            "VoiceDHAuthorityService",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

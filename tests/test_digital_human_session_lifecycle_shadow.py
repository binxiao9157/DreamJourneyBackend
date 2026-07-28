"""G0 tests for the default-off Tencent session lifecycle observer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import unittest

from app.services.digital_human_session_lifecycle_shadow import (
    DigitalHumanAssetSource,
    DigitalHumanAssetState,
    DigitalHumanSessionLifecycleAuthorityContext,
    DigitalHumanSessionLifecycleCommand,
    DigitalHumanSessionLifecycleDisposition,
    DigitalHumanSessionLifecycleOperation,
    DigitalHumanSessionLifecycleShadow,
)
from app.services.voice_dh_authority import VoiceDHPurpose


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**changes: object) -> DigitalHumanSessionLifecycleAuthorityContext:
    values: dict[str, object] = {
        "vault_id": "vault-dh-session-owner",
        "owner_subject_id": "owner-dh-session",
        "actor_subject_id": "owner-dh-session",
        "authority_epoch": 8,
    }
    values.update(changes)
    return DigitalHumanSessionLifecycleAuthorityContext(**values)  # type: ignore[arg-type]


def _command(**changes: object) -> DigitalHumanSessionLifecycleCommand:
    values: dict[str, object] = {
        "command_id": "dh-session-open-001",
        "runtime_id": "echo-runtime-dh-session-001",
        "runtime_generation": 5,
        "vault_id": "vault-dh-session-owner",
        "owner_subject_id": "owner-dh-session",
        "actor_subject_id": "owner-dh-session",
        "authority_epoch": 8,
        "role_subject_id": "owner-dh-session",
        "operation": DigitalHumanSessionLifecycleOperation.OPEN,
        "asset_registry_id": "dh-asset-registry-001",
        "asset_source": DigitalHumanAssetSource.BACKEND_REGISTRY,
        "asset_state": DigitalHumanAssetState.ACTIVE,
        "provider_project_reference_hash": _digest("tencent-project-ref-001"),
        "purpose": VoiceDHPurpose.DH_AUDIO_DRIVE,
        "issued_at": NOW - timedelta(seconds=20),
        "lease_expires_at": NOW + timedelta(seconds=120),
        "request_hash": _digest("dh-session-command-001"),
    }
    values.update(changes)
    return DigitalHumanSessionLifecycleCommand(**values)  # type: ignore[arg-type]


class DigitalHumanSessionLifecycleShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_session_inputs(self) -> None:
        result = DigitalHumanSessionLifecycleShadow().observe(context=object(), command=object())

        self.assertEqual(result.disposition, DigitalHumanSessionLifecycleDisposition.SHADOW_DISABLED)
        self.assertFalse(result.value_free_summary()["providerSessionKnown"])
        self.assertFalse(result.value_free_summary()["localLeaseProviderReady"])

    def test_active_backend_asset_open_is_candidate_only_and_never_opens_provider(self) -> None:
        command = _command()
        result = DigitalHumanSessionLifecycleShadow().observe(
            context=_context(),
            command=command,
            enabled=True,
            now=NOW,
        )

        self.assertEqual(result.disposition, DigitalHumanSessionLifecycleDisposition.BLOCKED)
        self.assertTrue(result.asset_candidate_eligible)
        self.assertTrue(result.runtime_generation_accepted)
        self.assertFalse(result.clear_runtime)
        self.assertTrue(result.fallback_to_text)
        self.assertFalse(result.provider_session_known)
        self.assertFalse(result.provider_session_opened)
        self.assertFalse(result.provider_session_closed)
        self.assertFalse(result.cleanup_receipt_persisted)
        self.assertFalse(result.local_lease_provider_ready)
        self.assertIn("providerSessionOpenBlocked", result.reason_codes)
        self.assertIn("g0NoProviderSessionCommand", result.reason_codes)
        summary = result.value_free_summary()
        for forbidden in (
            command.asset_registry_id,
            command.role_subject_id,
            command.provider_project_reference_hash,
            command.request_hash,
        ):
            self.assertNotIn(forbidden, repr(summary))

    def test_local_override_revoked_or_unknown_asset_clears_runtime(self) -> None:
        cases = {
            "localOverride": _command(asset_source=DigitalHumanAssetSource.LOCAL_QA_OVERRIDE),
            "revoked": _command(asset_state=DigitalHumanAssetState.REVOKED),
            "unknown": _command(asset_state=DigitalHumanAssetState.UNKNOWN),
        }
        expected = {
            "localOverride": "localQaAssetOverrideNotReleaseEligible",
            "revoked": "assetRevoked",
            "unknown": "assetStateUnknown",
        }

        for name, command in cases.items():
            with self.subTest(name=name):
                result = DigitalHumanSessionLifecycleShadow().observe(
                    context=_context(),
                    command=command,
                    enabled=True,
                    now=NOW,
                )
                self.assertFalse(result.asset_candidate_eligible)
                self.assertTrue(result.clear_runtime)
                self.assertIn(expected[name], result.reason_codes)

    def test_wrong_owner_or_non_owner_role_fails_closed(self) -> None:
        cross_owner = DigitalHumanSessionLifecycleShadow().observe(
            context=_context(),
            command=_command(owner_subject_id="other-owner", actor_subject_id="other-owner"),
            enabled=True,
            now=NOW,
        )
        third_party_role = DigitalHumanSessionLifecycleShadow().observe(
            context=_context(),
            command=_command(role_subject_id="adult-family-subject"),
            enabled=True,
            now=NOW,
        )

        for result, reason in (
            (cross_owner, "ownerVaultAuthorityMismatch"),
            (third_party_role, "roleSubjectNotOwnerForG0"),
        ):
            self.assertTrue(result.clear_runtime)
            self.assertIn(reason, result.reason_codes)
            self.assertFalse(result.provider_session_known)

    def test_duplicate_open_asset_switch_and_late_callback_are_fenced(self) -> None:
        observer = DigitalHumanSessionLifecycleShadow()
        opened = _command(runtime_generation=5)
        replay = _command(runtime_generation=5)
        asset_switch = _command(
            command_id="dh-session-open-asset-switch",
            asset_registry_id="dh-asset-registry-002",
            request_hash=_digest("dh-session-asset-switch"),
        )
        next_generation = _command(
            command_id="dh-session-open-next",
            runtime_generation=6,
            request_hash=_digest("dh-session-next"),
        )
        late_heartbeat = _command(
            command_id="dh-session-heartbeat-late",
            operation=DigitalHumanSessionLifecycleOperation.HEARTBEAT,
            runtime_generation=5,
            request_hash=_digest("dh-session-late-heartbeat"),
        )

        self.assertTrue(observer.observe(context=_context(), command=opened, enabled=True, now=NOW).runtime_generation_accepted)
        replay_result = observer.observe(context=_context(), command=replay, enabled=True, now=NOW)
        switch_result = observer.observe(context=_context(), command=asset_switch, enabled=True, now=NOW)
        self.assertTrue(observer.observe(context=_context(), command=next_generation, enabled=True, now=NOW).runtime_generation_accepted)
        late_result = observer.observe(context=_context(), command=late_heartbeat, enabled=True, now=NOW)

        self.assertIn("stableSessionCommandReplayObserved", replay_result.reason_codes)
        self.assertIn("stableRuntimeGenerationReplayObserved", replay_result.reason_codes)
        self.assertFalse(switch_result.runtime_generation_accepted)
        self.assertTrue(switch_result.clear_runtime)
        self.assertIn("sameGenerationAssetSwitchConflict", switch_result.reason_codes)
        self.assertFalse(late_result.runtime_generation_accepted)
        self.assertTrue(late_result.clear_runtime)
        self.assertIn("staleRuntimeGeneration", late_result.reason_codes)

    def test_expiry_and_long_ttl_do_not_promote_local_lease(self) -> None:
        expired = _command(
            operation=DigitalHumanSessionLifecycleOperation.HEARTBEAT,
            issued_at=NOW - timedelta(seconds=120),
            lease_expires_at=NOW - timedelta(seconds=1),
        )
        long_ttl = _command(lease_expires_at=NOW + timedelta(seconds=360))

        for command, reason in ((expired, "leaseExpired"), (long_ttl, "leaseTtlExceedsShadowMaximum")):
            with self.subTest(reason=reason):
                result = DigitalHumanSessionLifecycleShadow().observe(
                    context=_context(),
                    command=command,
                    enabled=True,
                    now=NOW,
                )
                self.assertIn(reason, result.reason_codes)
                self.assertFalse(result.local_lease_provider_ready)

    def test_close_and_reconcile_remain_honest_unknown_without_cleanup_receipt(self) -> None:
        observer = DigitalHumanSessionLifecycleShadow()
        opened = _command()
        close = _command(
            command_id="dh-session-close-001",
            operation=DigitalHumanSessionLifecycleOperation.CLOSE,
            request_hash=_digest("dh-session-close-001"),
        )
        reconcile = _command(
            command_id="dh-session-reconcile-001",
            operation=DigitalHumanSessionLifecycleOperation.RECONCILE,
            request_hash=_digest("dh-session-reconcile-001"),
        )

        observer.observe(context=_context(), command=opened, enabled=True, now=NOW)
        close_result = observer.observe(context=_context(), command=close, enabled=True, now=NOW)
        reconcile_result = observer.observe(context=_context(), command=reconcile, enabled=True, now=NOW)

        self.assertIn("providerSessionCloseUnknownRequiresReconcile", close_result.reason_codes)
        self.assertIn("providerSessionUnknownRequiresReconcile", reconcile_result.reason_codes)
        for result in (close_result, reconcile_result):
            self.assertFalse(result.provider_session_closed)
            self.assertFalse(result.cleanup_receipt_persisted)
            self.assertTrue(result.fallback_to_text)

    def test_module_does_not_import_provider_network_persistence_or_legacy_lease_store(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/services/digital_human_session_lifecycle_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services.tts",
            "app.services.in_memory_store",
            "app.services.postgres_store",
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

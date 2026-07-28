"""G0 tests for the default-off Voice/Digital Human rights-exit observer."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import unittest

from app.services.voice_dh_authority import VoiceDHPurpose
from app.services.voice_dh_exit_shadow import (
    VoiceDHExitAction,
    VoiceDHExitAuthorityContext,
    VoiceDHExitCommand,
    VoiceDHExitDisposition,
    VoiceDHExitError,
    VoiceDHExitResource,
    VoiceDHExitShadow,
    VoiceDHProviderExitState,
)


NOW = datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**changes: object) -> VoiceDHExitAuthorityContext:
    values: dict[str, object] = {
        "vault_id": "vault-voice-exit-owner",
        "owner_subject_id": "owner-voice-exit",
        "actor_subject_id": "owner-voice-exit",
        "authority_epoch": 12,
    }
    values.update(changes)
    return VoiceDHExitAuthorityContext(**values)  # type: ignore[arg-type]


def _command(**changes: object) -> VoiceDHExitCommand:
    values: dict[str, object] = {
        "command_id": "voice-exit-delete-001",
        "vault_id": "vault-voice-exit-owner",
        "owner_subject_id": "owner-voice-exit",
        "actor_subject_id": "owner-voice-exit",
        "authority_epoch": 12,
        "profile_id": "voice-profile-exit-001",
        "profile_version": 3,
        "runtime_id": "echo-runtime-exit-001",
        "runtime_generation": 7,
        "action": VoiceDHExitAction.DELETE_PROFILE,
        "purpose": VoiceDHPurpose.DH_AUDIO_DRIVE,
        "requested_resources": tuple(VoiceDHExitResource),
        "issued_at": NOW,
        "request_hash": _digest("voice-exit-delete-001"),
    }
    values.update(changes)
    return VoiceDHExitCommand(**values)  # type: ignore[arg-type]


class VoiceDHExitShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_exit_inputs(self) -> None:
        result = VoiceDHExitShadow().observe(context=object(), command=object())

        self.assertEqual(result.disposition, VoiceDHExitDisposition.SHADOW_DISABLED)
        self.assertFalse(result.access_revocation_required)
        self.assertFalse(result.new_effects_must_be_denied)
        self.assertFalse(result.provider_exit_receipt_persisted)

    def test_delete_requires_access_first_and_never_claims_cleanup_complete(self) -> None:
        command = _command()
        result = VoiceDHExitShadow().observe(
            context=_context(),
            command=command,
            enabled=True,
            now=NOW,
        )

        self.assertEqual(result.disposition, VoiceDHExitDisposition.BLOCKED)
        self.assertTrue(result.authority_context_valid)
        self.assertTrue(result.access_revocation_required)
        self.assertTrue(result.new_effects_must_be_denied)
        self.assertTrue(result.runtime_clear_required)
        self.assertEqual(result.provider_exit_state, VoiceDHProviderExitState.UNKNOWN)
        self.assertFalse(result.provider_exit_receipt_persisted)
        self.assertFalse(result.local_cleanup_performed)
        self.assertFalse(result.server_cleanup_persisted)
        for reason in (
            "accessRevocationMustPrecedeCleanup",
            "g0NoProviderDeleteOrExit",
            "g2ExitDAGAndReceiptStoreRequired",
            "g3ProviderDeleteQueryRequired",
            "noCompletionClaimWithoutReceipt",
        ):
            self.assertIn(reason, result.reason_codes)

        summary = result.value_free_summary()
        for forbidden in (
            command.command_id,
            command.profile_id,
            command.runtime_id,
            command.request_hash,
            command.exit_fingerprint,
        ):
            self.assertNotIn(forbidden, repr(summary))

    def test_non_owner_or_context_mismatch_fails_closed(self) -> None:
        cross_owner = VoiceDHExitShadow().observe(
            context=_context(),
            command=_command(owner_subject_id="other-owner", actor_subject_id="other-owner"),
            enabled=True,
            now=NOW,
        )
        delegated_actor = VoiceDHExitShadow().observe(
            context=_context(actor_subject_id="delegate-user"),
            command=_command(actor_subject_id="delegate-user"),
            enabled=True,
            now=NOW,
        )

        for result, reason in (
            (cross_owner, "ownerVaultAuthorityMismatch"),
            (delegated_actor, "actorNotOwnerForG0"),
        ):
            self.assertFalse(result.authority_context_valid)
            self.assertTrue(result.new_effects_must_be_denied)
            self.assertTrue(result.runtime_clear_required)
            self.assertIn(reason, result.reason_codes)

    def test_stale_authority_runtime_and_conflicting_replay_are_fenced(self) -> None:
        observer = VoiceDHExitShadow()
        current = _command(authority_epoch=12, runtime_generation=7)
        next_epoch = _command(
            command_id="voice-exit-delete-next-epoch",
            authority_epoch=13,
            runtime_generation=8,
            request_hash=_digest("voice-exit-delete-next-epoch"),
        )
        stale = _command(
            command_id="voice-exit-delete-stale",
            authority_epoch=12,
            runtime_generation=6,
            request_hash=_digest("voice-exit-delete-stale"),
        )
        conflicting_replay = _command(request_hash=_digest("voice-exit-delete-conflict"))

        self.assertTrue(observer.observe(context=_context(), command=current, enabled=True, now=NOW).authority_context_valid)
        self.assertTrue(
            observer.observe(
                context=_context(authority_epoch=13),
                command=next_epoch,
                enabled=True,
                now=NOW,
            ).authority_context_valid
        )
        stale_result = observer.observe(context=_context(), command=stale, enabled=True, now=NOW)
        conflict_result = observer.observe(context=_context(), command=conflicting_replay, enabled=True, now=NOW)

        self.assertFalse(stale_result.authority_context_valid)
        self.assertIn("staleAuthorityEpoch", stale_result.reason_codes)
        self.assertIn("staleRuntimeGeneration", stale_result.reason_codes)
        self.assertFalse(conflict_result.authority_context_valid)
        self.assertIn("stableExitCommandHashConflict", conflict_result.reason_codes)

    def test_command_rejects_provider_ids_and_incomplete_delete_scope(self) -> None:
        with self.assertRaises(VoiceDHExitError):
            _command(profile_id="S_provider-slot")
        with self.assertRaises(VoiceDHExitError):
            _command(runtime_id="S_provider-runtime")
        with self.assertRaises(VoiceDHExitError):
            _command(requested_resources=(VoiceDHExitResource.VOICE_PROFILE,))

    def test_module_does_not_import_provider_network_persistence_or_api_routes(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/services/voice_dh_exit_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services.in_memory_store",
            "app.services.postgres_store",
            "app.async_effects",
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

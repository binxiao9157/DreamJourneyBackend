from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState, AsyncEffectRuntimeStatus, AsyncEffectTarget
from app.async_effects.dead_letter_effects import (
    DeadLetterCause,
    DeadLetterReplayCommand,
    admit_dead_letter,
)
from app.async_effects.readiness_evidence import (
    AsyncEffectReadinessManifestStatus,
    build_async_effect_readiness_manifest_plan,
    build_async_effect_worker_readiness_evidence,
)
from app.async_effects.recovery_evidence import (
    ASYNC_EFFECT_RECOVERY_EVIDENCE_SCHEMA_VERSION,
    DeadLetterRestoreReplayContext,
    DeadLetterRestoreReplayReason,
    authorize_restored_dead_letter_replay,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _admission():
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.recovery",
        target=AsyncEffectTarget(
            owner_subject_id="owner-recovery-001",
            vault_id="vault-recovery-001",
            resource_type="timeLetter",
            resource_id="letter-recovery-001",
            resource_version=4,
            purpose="timeLetterDelivery",
            authority_epoch=3,
        ),
        payload_hash=_hash("recovery-payload"),
    )
    return admit_dead_letter(
        intent=intent,
        job_state=AsyncEffectJobState.FAILED,
        attempt=3,
        max_attempts=3,
        cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
        failure_hash=_hash("recovery-failure"),
        last_receipt_hash=_hash("recovery-receipt"),
    )


def _command(admission):
    return DeadLetterReplayCommand(
        dead_letter_id=admission.dead_letter_id,
        actor_subject_id="owner-recovery-001",
        owner_subject_id="owner-recovery-001",
        vault_id="vault-recovery-001",
        authority_epoch=3,
        authorization_receipt_hash=_hash("pre-restore-replay-authorization"),
        reason_code="operatorApproved",
    )


def _restore_context(*, authorization: str = "post-restore-replay-authorization"):
    return DeadLetterRestoreReplayContext(
        restore_id="restore-checkpoint-001",
        owner_subject_id="owner-recovery-001",
        vault_id="vault-recovery-001",
        authority_epoch=3,
        restore_checkpoint_hash=_hash("isolated-postgres-restore"),
        recovery_authorization_receipt_hash=_hash(authorization),
    )


class AsyncEffectRecoveryEvidenceTests(unittest.TestCase):
    def test_restored_replay_requires_a_fresh_recovery_receipt_and_preserves_stable_key(self):
        admission = _admission()
        command = _command(admission)
        context = _restore_context()

        first = authorize_restored_dead_letter_replay(admission, command, context)
        replay = authorize_restored_dead_letter_replay(admission, command, context)
        summary = first.value_free_summary()

        self.assertTrue(first.authorized)
        self.assertEqual(first.reason, DeadLetterRestoreReplayReason.AUTHORIZED)
        self.assertEqual(first.stable_key, admission.stable_key)
        self.assertEqual(first.next_attempt, 4)
        self.assertEqual(first.replay_id, replay.replay_id)
        self.assertEqual(summary["schemaVersion"], ASYNC_EFFECT_RECOVERY_EVIDENCE_SCHEMA_VERSION)
        self.assertNotIn("owner-recovery", str(summary))
        self.assertNotIn("vault-recovery", str(summary))
        self.assertNotIn("authorization", str(summary).lower())

    def test_restored_replay_rejects_reused_or_mismatched_recovery_authority(self):
        admission = _admission()
        command = _command(admission)
        reused = _restore_context(authorization="pre-restore-replay-authorization")
        wrong_epoch = DeadLetterRestoreReplayContext(
            restore_id="restore-checkpoint-001",
            owner_subject_id="owner-recovery-001",
            vault_id="vault-recovery-001",
            authority_epoch=4,
            restore_checkpoint_hash=_hash("isolated-postgres-restore"),
            recovery_authorization_receipt_hash=_hash("post-restore-replay-authorization"),
        )

        self.assertEqual(
            authorize_restored_dead_letter_replay(admission, command, reused).reason,
            DeadLetterRestoreReplayReason.FRESH_RECOVERY_AUTHORIZATION_REQUIRED,
        )
        self.assertEqual(
            authorize_restored_dead_letter_replay(admission, command, wrong_epoch).reason,
            DeadLetterRestoreReplayReason.RESTORE_AUTHORITY_EPOCH_MISMATCH,
        )

    def test_manifest_plan_never_promotes_skipped_unknown_or_expired_observation(self):
        now = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)
        runtime = AsyncEffectRuntimeStatus(True, True, True, "asyncEffectRuntimeReady")
        skipped = build_async_effect_worker_readiness_evidence(
            runtime_status=runtime,
            worker_id="worker-recovery-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            store_supported=False,
        )
        unknown = build_async_effect_worker_readiness_evidence(
            runtime_status=runtime,
            worker_id="worker-recovery-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            collection_error_code="asyncEffectBacklogObservationFailed",
        )
        expired = build_async_effect_worker_readiness_evidence(
            runtime_status=runtime,
            worker_id="worker-recovery-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=now,
            expires_at=now + timedelta(seconds=1),
        )
        ready = build_async_effect_worker_readiness_evidence(
            runtime_status=runtime,
            worker_id="worker-recovery-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
        )

        self.assertEqual(
            build_async_effect_readiness_manifest_plan(skipped, now=now).status,
            AsyncEffectReadinessManifestStatus.NOT_RUN,
        )
        self.assertEqual(
            build_async_effect_readiness_manifest_plan(unknown, now=now).status,
            AsyncEffectReadinessManifestStatus.NOT_RUN,
        )
        self.assertEqual(
            build_async_effect_readiness_manifest_plan(expired, now=now + timedelta(seconds=2)).status,
            AsyncEffectReadinessManifestStatus.NOT_RUN,
        )
        self.assertEqual(
            build_async_effect_readiness_manifest_plan(ready, now=now).status,
            AsyncEffectReadinessManifestStatus.PASSED,
        )


if __name__ == "__main__":
    unittest.main()

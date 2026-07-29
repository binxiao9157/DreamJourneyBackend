from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.projection_rights import (
    OwnerTruthProjectionRightsAccessDenied,
    OwnerTruthProjectionRightsRevisionCommand,
    OwnerTruthProjectionRightsRevisionConflict,
    ProjectionRightsState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_context_shadow import OwnerTruthContextShadowReadService
from app.services.owner_truth_kblite_compatibility import (
    OwnerTruthKBLiteCompatibilityReadService,
    compatibility_read_envelope,
)
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)
from app.services.owner_truth_projection_rights import (
    InMemoryOwnerTruthProjectionRightsRepository,
    OwnerTruthProjectionRightsService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.rights_repository = InMemoryOwnerTruthProjectionRightsRepository()
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository,
            rights_repository=self.rights_repository,
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_projection_rights_repository(self):
        return self.rights_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository


class OwnerTruthProjectionRightsFenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-projection-rights-fence"
        self.owner_id = "subject-projection-rights-fence"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)
        self.rights_service = OwnerTruthProjectionRightsService(self.store)

    def _candidate(self) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"claim": "仅在权利状态可用时进入 Projection 的确认事实"}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _activate(self) -> OwnerTruthCandidateSnapshot:
        candidate = self._candidate()
        self.store.review_repository.seed(candidate)
        self.review_service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="projection-rights-fence-accept-001",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        return candidate

    def _record_rights(
        self,
        *,
        expected_revision: int,
        state: ProjectionRightsState,
        suffix: str,
    ):
        return self.rights_service.record(
            context=self.context,
            command=OwnerTruthProjectionRightsRevisionCommand(
                command_id=f"projection-rights-fence-{suffix}",
                authority_epoch=0,
                expected_revision=expected_revision,
                state=state,
                event_hash=_hash({"event": suffix, "state": state.value}),
            ),
        )

    def test_active_rights_revision_invalidates_checkpoint_until_rebuilt(self) -> None:
        self._activate()
        baseline = self.projection_service.rebuild(context=self.context).snapshot

        changed = self._record_rights(
            expected_revision=0,
            state=ProjectionRightsState.ACTIVE,
            suffix="active-revision-001",
        )
        stale = self.projection_service.read(context=self.context)
        rebuilt = self.projection_service.rebuild(context=self.context).snapshot

        self.assertEqual(baseline["rightsRevision"], 0)
        self.assertEqual(changed.outcome, "recorded")
        self.assertEqual(changed.snapshot.revision, 1)
        self.assertEqual(stale["state"], "rebuilding")
        self.assertEqual(stale["rebuildReason"], "rightsRevisionChanged")
        self.assertEqual(stale["entries"], [])
        self.assertEqual(rebuilt["state"], "ready")
        self.assertEqual(rebuilt["rightsRevision"], 1)
        self.assertNotEqual(baseline["checkpoint"], rebuilt["checkpoint"])

    def test_revocation_fails_closed_for_projection_context_and_kblite_cache(self) -> None:
        candidate = self._activate()
        self.projection_service.rebuild(context=self.context)
        ready_context = OwnerTruthContextShadowReadService(self.store, enabled=True).read(
            context=self.context
        )
        self.assertEqual(ready_context["state"], "ready")
        self.assertEqual(len(ready_context["selectedContext"]), 1)

        self._record_rights(
            expected_revision=0,
            state=ProjectionRightsState.REVOKED,
            suffix="revoke-001",
        )

        projection = self.projection_service.read(context=self.context)
        context = OwnerTruthContextShadowReadService(self.store, enabled=True).read(
            context=self.context
        )
        kblite = OwnerTruthKBLiteCompatibilityReadService(self.store, enabled=True).read(
            context=self.context
        )
        envelope = compatibility_read_envelope(kblite)
        blocked_rebuild = self.projection_service.rebuild(context=self.context)

        self.assertEqual(projection["state"], "rebuilding")
        self.assertEqual(projection["rebuildReason"], "rightsRevoked")
        self.assertEqual(projection["entries"], [])
        self.assertEqual(context["state"], "rebuilding")
        self.assertEqual(context["selectedContext"], [])
        self.assertEqual(kblite["state"], "rebuilding")
        self.assertEqual(envelope["cacheDisposition"], "discard")
        self.assertEqual(envelope["graph"]["facts"], [])
        self.assertEqual(blocked_rebuild.outcome, "blocked")
        self.assertNotIn(candidate.content["claim"], str(context))
        self.assertNotIn(candidate.content["claim"], str(envelope))

    def test_non_owner_cannot_change_rights_and_revocation_is_terminal(self) -> None:
        outsider = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="subject-projection-rights-outsider",
        )
        command = OwnerTruthProjectionRightsRevisionCommand(
            command_id="projection-rights-fence-outsider",
            authority_epoch=0,
            expected_revision=0,
            state=ProjectionRightsState.REVOKED,
            event_hash=_hash({"event": "outsider"}),
        )
        with self.assertRaises(OwnerTruthProjectionRightsAccessDenied):
            self.rights_service.record(context=outsider, command=command)

        self._record_rights(
            expected_revision=0,
            state=ProjectionRightsState.REVOKED,
            suffix="terminal-revoke",
        )
        with self.assertRaises(OwnerTruthProjectionRightsRevisionConflict):
            self._record_rights(
                expected_revision=1,
                state=ProjectionRightsState.ACTIVE,
                suffix="invalid-reactivation",
            )


class OwnerTruthProjectionRightsFenceMigrationContractTests(unittest.TestCase):
    def test_migration_binds_projection_checkpoint_to_current_rights_revision(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sql = (root / "db/migrations/0061_owner_truth_projection_rights_fence.sql").read_text(
            encoding="utf-8"
        )
        metadata = json.loads(
            (root / "db/migrations/0061_owner_truth_projection_rights_fence.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("CREATE TABLE owner_truth.projection_rights_events", sql)
        self.assertIn("rights_revision", sql)
        self.assertIn("rights_event_hash", sql)
        self.assertIn("current_rights_state IS DISTINCT FROM 'active'", sql)
        self.assertIn("owner truth projection checkpoint rights fence is stale", sql)
        self.assertIn("owner_truth_memory_projection_checkpoints_validate_vault", sql)
        self.assertEqual(metadata["version"], "0061")
        self.assertFalse(metadata["releaseFlags"]["ownerTruthProjectionRightsIngressV1"])
        self.assertFalse(metadata["releaseFlags"]["memoryProjectionV1"])


if __name__ == "__main__":
    unittest.main()

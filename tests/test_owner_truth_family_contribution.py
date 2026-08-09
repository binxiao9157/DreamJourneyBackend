from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.delegated_access import DelegatedAccessService, RelationshipLifecycleCommand, RelationshipOperation
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_family_contribution import (
    CreateFamilyContributionGrantCommand,
    OwnerTruthFamilyContributionError,
    OwnerTruthFamilyContributionService,
    ReviewFamilyContributionSubmissionCommand,
    RevokeFamilyContributionGrantCommand,
    SubmitFamilyContributionForReviewCommand,
    SubmitFamilyContributionTextCommand,
)
from app.services.owner_truth_source import OwnerTruthSourceCommandService


class OwnerTruthFamilyContributionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
        self.store = InMemoryStore()
        self.owner = "subject_owner"
        self.contributor = "subject_family"
        self.other_subject = "subject_other"
        self.vault_id = "vault-family-contribution"
        self.delegated_access = DelegatedAccessService(
            self.store,
            now_provider=lambda: self.now,
        )
        self.service = OwnerTruthFamilyContributionService(
            self.store,
            now_provider=lambda: self.now,
        )

    def owner_context(self) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner,
            actor_subject_id=self.owner,
        )

    def contributor_context(self) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner,
            actor_subject_id=self.contributor,
        )

    def seed_vault(self) -> None:
        OwnerTruthSourceCommandService(self.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="owner-bootstrap-source",
                source_id=str(uuid4()),
                expected_version=0,
                text="Owner private source establishes the Vault.",
                metadata={"origin": "test"},
            ),
            context=self.owner_context(),
        )

    def accepted_relationship(self):
        return self.delegated_access.ensure_relationship(
            owner_subject_id=self.owner,
            family_member_id="family_member_1",
            member_subject_id=self.contributor,
            status="accepted",
        )

    def create_grant(self, relationship_id: str):
        return self.service.create_grant(
            command=CreateFamilyContributionGrantCommand(
                command_id="family-contribution-grant-001",
                relationship_id=relationship_id,
                contributor_subject_id=self.contributor,
            ),
            context=self.owner_context(),
        )

    def test_grant_requires_existing_owner_vault_and_accepted_relationship(self) -> None:
        relationship = self.accepted_relationship()

        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionVaultNotFound",
        ):
            self.create_grant(relationship["id"])

        self.seed_vault()
        pending = self.delegated_access.ensure_relationship(
            owner_subject_id=self.owner,
            family_member_id="family_member_pending",
            member_subject_id="subject_pending",
            status="pending",
        )
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionRelationshipInactive",
        ):
            self.create_grant(pending["id"])

    def test_grant_is_idempotent_and_does_not_turn_relationship_into_vault_access(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()

        created = self.create_grant(relationship["id"])
        replay = self.create_grant(relationship["id"])

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(created.grant["id"], replay.grant["id"])
        self.assertEqual(created.grant["scope"], "submitTextSource")
        self.assertEqual(created.grant["relationshipEpoch"], relationship["relationshipEpoch"])
        self.assertEqual(self.store.list_access_grants(owner_subject_id=self.owner), [])
        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 1)

    def test_contributor_can_submit_one_static_source_and_receives_no_private_content(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()
        grant = self.create_grant(relationship["id"]).grant
        source_id = str(uuid4())

        result = self.service.submit_text_source(
            command=SubmitFamilyContributionTextCommand(
                grant_id=grant["id"],
                expected_grant_version=grant["rowVersion"],
                source_command_id="family-report-source-001",
                source_id=source_id,
                text="我记得他年轻时常在雨后修理自行车。",
            ),
            context=self.contributor_context(),
        )

        self.assertEqual(result.source.outcome, "created")
        self.assertEqual(result.source.source_id, source_id)
        contract = result.public_contract()
        self.assertNotIn("自行车", str(contract))
        source = self.store._owner_truth_sources[(self.vault_id, source_id)]
        self.assertEqual(source["ownerSubjectId"], self.owner)
        self.assertEqual(source["metadata"]["origin"], "familyContributionGrant")
        self.assertEqual(source["metadata"]["perspectiveType"], "familyReport")
        self.assertEqual(source["metadata"]["candidateExtraction"], "defaultOff")
        self.assertEqual(contract["candidateExtraction"], {"status": "notRequested"})
        receipt = next(
            item
            for item in self.store._owner_truth_source_receipts.values()
            if item["id"] == result.source.receipt_id
        )
        self.assertEqual(receipt["actorSubjectId"], self.contributor)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 0)
        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 2)

    def test_formal_submission_requires_owner_review_and_revoke_hides_accepted_source(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()
        grant = self.create_grant(relationship["id"]).grant
        submission_id = str(uuid4())

        pending = self.service.submit_for_review(
            command=SubmitFamilyContributionForReviewCommand(
                command_id="family-review-submission-001",
                submission_id=submission_id,
                grant_id=grant["id"],
                expected_grant_version=grant["rowVersion"],
                material_kind="text",
                text="家人记得 Owner 在雨天修理自行车。",
            ),
            context=self.contributor_context(),
        )

        self.assertEqual(pending.outcome, "pendingReview")
        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 1)
        contributor_view = pending.public_contract(include_material=False)
        self.assertNotIn("自行车", str(contributor_view))
        owner_items = self.service.list_submissions_for_owner(
            context=self.owner_context()
        )
        self.assertEqual(owner_items[0]["status"], "pendingReview")
        self.assertIn("自行车", owner_items[0]["text"])

        accepted = self.service.review_submission(
            command=ReviewFamilyContributionSubmissionCommand(
                command_id="family-review-decision-001",
                submission_id=submission_id,
                expected_version=1,
                decision="accepted",
            ),
            context=self.owner_context(),
        )
        self.assertEqual(accepted.outcome, "accepted")
        self.assertIsNotNone(accepted.source)
        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 2)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)
        source_id = str(accepted.submission["sourceId"])
        self.assertEqual(
            self.store._owner_truth_sources[(self.vault_id, source_id)]["state"],
            "active",
        )

        revoked = self.service.revoke_grant(
            command=RevokeFamilyContributionGrantCommand(
                command_id="family-review-revoke-001",
                grant_id=grant["id"],
                expected_version=grant["rowVersion"],
            ),
            context=self.owner_context(),
        )
        self.assertEqual(revoked.outcome, "revoked")
        self.assertEqual(
            self.service.list_submissions_for_owner(context=self.owner_context())[0][
                "status"
            ],
            "withdrawn",
        )
        self.assertEqual(
            self.store._owner_truth_sources[(self.vault_id, source_id)]["state"],
            "deleted",
        )

    def test_image_submission_stays_unprocessed_until_owner_acceptance_and_revoke_hides_object(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()
        grant = self.create_grant(relationship["id"]).grant
        source_object_id = str(uuid4())
        repository = self.store._owner_truth_media_source_object_repository
        repository._objects[(self.vault_id, source_object_id)] = {
            "sourceObjectId": source_object_id,
            "vaultId": self.vault_id,
            "ownerSubjectId": self.owner,
            "mediaKind": "image",
            "state": "verified",
            "accessState": "available",
            "processingStatus": "notQueued",
            "rowVersion": 1,
        }
        submission_id = str(uuid4())

        pending = self.service.submit_for_review(
            command=SubmitFamilyContributionForReviewCommand(
                command_id="family-image-submission-001",
                submission_id=submission_id,
                grant_id=grant["id"],
                expected_grant_version=1,
                material_kind="image",
                source_object_id=source_object_id,
            ),
            context=self.contributor_context(),
        )
        self.assertEqual(pending.outcome, "pendingReview")
        self.assertEqual(
            repository._objects[(self.vault_id, source_object_id)]["processingStatus"],
            "notQueued",
        )

        accepted = self.service.review_submission(
            command=ReviewFamilyContributionSubmissionCommand(
                command_id="family-image-review-001",
                submission_id=submission_id,
                expected_version=1,
                decision="accepted",
            ),
            context=self.owner_context(),
        )
        self.assertEqual(accepted.outcome, "accepted")
        self.assertIsNone(accepted.source)

        self.service.revoke_grant(
            command=RevokeFamilyContributionGrantCommand(
                command_id="family-image-revoke-001",
                grant_id=grant["id"],
                expected_version=1,
            ),
            context=self.owner_context(),
        )
        media = repository._objects[(self.vault_id, source_object_id)]
        self.assertEqual(media["accessState"], "revoked")
        self.assertEqual(media["processingStatus"], "blocked")

    def test_submission_fails_closed_for_wrong_actor_stale_relationship_or_revoke(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()
        grant = self.create_grant(relationship["id"]).grant

        wrong_actor = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner,
            actor_subject_id=self.other_subject,
        )
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionGrantContributorMismatch",
        ):
            self.service.submit_text_source(
                command=SubmitFamilyContributionTextCommand(
                    grant_id=grant["id"],
                    expected_grant_version=1,
                    source_command_id="wrong-actor-source",
                    source_id=str(uuid4()),
                    text="未经授权的家庭报告。",
                ),
                context=wrong_actor,
            )

        paused = self.delegated_access.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner,
                relationshipId=relationship["id"],
                operation=RelationshipOperation.PAUSE,
                expectedEpoch=relationship["relationshipEpoch"],
            )
        )
        self.assertEqual(paused["status"], "paused")
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionRelationshipInactive",
        ):
            self.service.submit_text_source(
                command=SubmitFamilyContributionTextCommand(
                    grant_id=grant["id"],
                    expected_grant_version=1,
                    source_command_id="paused-source",
                    source_id=str(uuid4()),
                    text="暂停关系后不得继续写入。",
                ),
                context=self.contributor_context(),
            )

        revoked = self.service.revoke_grant(
            command=RevokeFamilyContributionGrantCommand(
                command_id="family-contribution-revoke-001",
                grant_id=grant["id"],
                expected_version=1,
            ),
            context=self.owner_context(),
        )
        self.assertEqual(revoked.outcome, "revoked")
        self.assertEqual(revoked.grant["status"], "revoked")
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionGrantInactive",
        ):
            self.service.submit_text_source(
                command=SubmitFamilyContributionTextCommand(
                    grant_id=grant["id"],
                    expected_grant_version=1,
                    source_command_id="revoked-source",
                    source_id=str(uuid4()),
                    text="撤销后不得继续写入。",
                ),
                context=self.contributor_context(),
            )

    def test_relationship_resume_does_not_reactivate_an_old_contribution_grant(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()
        grant = self.create_grant(relationship["id"]).grant
        paused = self.delegated_access.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner,
                relationshipId=relationship["id"],
                operation=RelationshipOperation.PAUSE,
                expectedEpoch=relationship["relationshipEpoch"],
            )
        )
        resumed = self.delegated_access.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner,
                relationshipId=relationship["id"],
                operation=RelationshipOperation.RESUME,
                expectedEpoch=paused["relationshipEpoch"],
            )
        )

        self.assertEqual(resumed["status"], "accepted")
        self.assertGreater(resumed["relationshipEpoch"], grant["relationshipEpoch"])
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionRelationshipEpochMismatch",
        ):
            self.service.submit_text_source(
                command=SubmitFamilyContributionTextCommand(
                    grant_id=grant["id"],
                    expected_grant_version=grant["rowVersion"],
                    source_command_id="resumed-old-grant-source",
                    source_id=str(uuid4()),
                    text="恢复关系后旧授权不得重新生效。",
                ),
                context=self.contributor_context(),
            )

    def test_revoke_is_idempotent_but_command_reuse_with_different_meaning_is_not(self) -> None:
        self.seed_vault()
        relationship = self.accepted_relationship()
        grant = self.create_grant(relationship["id"]).grant
        command = RevokeFamilyContributionGrantCommand(
            command_id="family-contribution-revoke-002",
            grant_id=grant["id"],
            expected_version=1,
        )

        first = self.service.revoke_grant(command=command, context=self.owner_context())
        replay = self.service.revoke_grant(command=command, context=self.owner_context())

        self.assertEqual(first.outcome, "revoked")
        self.assertEqual(replay.outcome, "deduplicated")
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionGrantVersionMismatch",
        ):
            self.service.revoke_grant(
                command=RevokeFamilyContributionGrantCommand(
                    command_id="different-revoke-command",
                    grant_id=grant["id"],
                    expected_version=1,
                    reason="differentReason",
                ),
                context=self.owner_context(),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

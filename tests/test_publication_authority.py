"""P2-S1 owner-only publication authority contract tests.

These tests exercise the closed-pilot writer before a FastAPI route can expose
it.  A selected MemoryVersion is only an authority anchor: the eventual public
copy is explicitly authored for the publication draft and never copied from a
private projection or KBLite read model.
"""

from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.publication_authority import (
    InMemoryPublicationAuthorityRepository,
    PostgresPublicationAuthorityRepository,
    PublicationAuthorityConflict,
    PublicationAuthorityDisabled,
    PublicationAuthorityMemoryVersion,
    PublicationAuthorityNotPublishable,
    PublicationAuthorityService,
    PublicationConfirmCommand,
    PublicationDraftCommand,
    PublicationDraftItemCommand,
)


class _PublicationCursor:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def execute(self, _query: str, _parameters: object) -> None:
        pass

    def fetchone(self) -> dict[str, object]:
        return self._row


class PublicationAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = OwnerTruthCommandContext(
            vault_id="vault-publication-owner",
            owner_subject_id="owner-publication",
            actor_subject_id="owner-publication",
        )
        self.repository = InMemoryPublicationAuthorityRepository()
        self.memory = PublicationAuthorityMemoryVersion(
            memory_version_id=str(uuid4()),
            memory_id=str(uuid4()),
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            authority_epoch=4,
            content_hash="a" * 64,
            is_current=True,
            memory_state="active",
            source_state="active",
            decision="accepted",
            decision_receipt_id=str(uuid4()),
        )
        self.repository.seed_memory_version(self.memory)
        self.service = PublicationAuthorityService(self.repository, enabled=True)

    def _draft_command(self, **overrides: object) -> PublicationDraftCommand:
        values: dict[str, object] = {
            "command_id": str(uuid4()),
            "memory_version_id": self.memory.memory_version_id,
            "public_title": "河边散步",
            "public_body": "那年傍晚，我们沿着河边慢慢走。",
        }
        values.update(overrides)
        return PublicationDraftCommand(**values)  # type: ignore[arg-type]

    def test_default_disabled_writer_fails_closed(self) -> None:
        service = PublicationAuthorityService(self.repository, enabled=False)
        with self.assertRaises(PublicationAuthorityDisabled):
            service.create_draft(context=self.context, command=self._draft_command())

    def test_active_confirmed_current_memory_creates_value_minimized_draft(self) -> None:
        command = self._draft_command(public_body="那年傍晚，我们沿着河边慢慢走。")
        result = self.service.create_draft(context=self.context, command=command)

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.state, "draft")
        self.assertEqual(result.expected_draft_revision, 1)
        self.assertTrue(result.second_confirmation_required)
        self.assertFalse(result.third_party_review_required)
        self.assertNotEqual(result.draft_snapshot_hash, self.memory.content_hash)

        replay = self.service.create_draft(context=self.context, command=command)
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.draft_id, result.draft_id)

    def test_direct_identifiers_are_rejected_before_the_public_copy_is_persisted(self) -> None:
        for body in (
            "联系我：13800138000。",
            "联系我：owner@example.com。",
            "证件号：110101199001011234。",
        ):
            with self.subTest(body=body), self.assertRaises(PublicationAuthorityNotPublishable):
                self._draft_command(public_body=body)

    def test_unconfirmed_or_no_longer_current_memory_cannot_be_published(self) -> None:
        self.repository.seed_memory_version(
            PublicationAuthorityMemoryVersion(
                memory_version_id=str(uuid4()),
                memory_id=str(uuid4()),
                vault_id=self.context.vault_id,
                owner_subject_id=self.context.owner_subject_id,
                authority_epoch=4,
                content_hash="b" * 64,
                is_current=True,
                memory_state="active",
                source_state="active",
                decision="pending",
                decision_receipt_id=None,
            )
        )
        pending_id = next(
            item.memory_version_id
            for item in self.repository.memory_versions()
            if item.decision == "pending"
        )
        with self.assertRaises(PublicationAuthorityNotPublishable):
            self.service.create_draft(
                context=self.context,
                command=self._draft_command(memory_version_id=pending_id),
            )

        self.repository.seed_memory_version(
            PublicationAuthorityMemoryVersion(
                memory_version_id=self.memory.memory_version_id,
                memory_id=self.memory.memory_id,
                vault_id=self.context.vault_id,
                owner_subject_id=self.context.owner_subject_id,
                authority_epoch=4,
                content_hash=self.memory.content_hash,
                is_current=False,
                memory_state="active",
                source_state="active",
                decision="accepted",
                decision_receipt_id=self.memory.decision_receipt_id,
            )
        )
        with self.assertRaises(PublicationAuthorityNotPublishable):
            self.service.create_draft(context=self.context, command=self._draft_command())

    def test_postgres_publication_accepts_initial_authority_epoch_zero(self) -> None:
        """The first Owner Truth epoch is 0, not an absent authority value."""

        repository = PostgresPublicationAuthorityRepository(connection=object())
        row = {
            "memory_version_id": self.memory.memory_version_id,
            "memory_id": self.memory.memory_id,
            "vault_id": self.context.vault_id,
            "is_current": True,
            "content_hash": self.memory.content_hash,
            "payload": {},
            "source_id": str(uuid4()),
            "source_version": 1,
            "decision_receipt_id": self.memory.decision_receipt_id,
            "owner_subject_id": self.context.owner_subject_id,
            "memory_authority_epoch": 0,
            "memory_state": "active",
            "sensitivity": "standard",
            "source_owner_subject_id": self.context.owner_subject_id,
            "source_authority_epoch": 0,
            "live_source_version": 1,
            "source_state": "active",
            "receipt_decision": "accepted",
            "receipt_authority_epoch": 0,
            "candidate_owner_subject_id": self.context.owner_subject_id,
            "candidate_authority_epoch": 0,
            "candidate_decision_status": "accepted",
        }

        result = repository._publishable_memory(
            _PublicationCursor(row),
            context=self.context,
            vault_authority_epoch=0,
            memory_version_id=self.memory.memory_version_id,
        )

        self.assertEqual(result.authority_epoch, 0)
        self.assertEqual(result.memory_version_id, self.memory.memory_version_id)

    def test_third_party_flag_requires_redaction_workflow_before_confirmation(self) -> None:
        self.repository.seed_memory_version(
            PublicationAuthorityMemoryVersion(
                memory_version_id=self.memory.memory_version_id,
                memory_id=self.memory.memory_id,
                vault_id=self.context.vault_id,
                owner_subject_id=self.context.owner_subject_id,
                authority_epoch=4,
                content_hash=self.memory.content_hash,
                is_current=True,
                memory_state="active",
                source_state="active",
                decision="accepted",
                decision_receipt_id=self.memory.decision_receipt_id,
                third_party_review_required=True,
            )
        )
        draft = self.service.create_draft(context=self.context, command=self._draft_command())
        self.assertTrue(draft.third_party_review_required)

        with self.assertRaises(PublicationAuthorityNotPublishable):
            self.service.confirm_draft(
                context=self.context,
                command=PublicationConfirmCommand(
                    command_id=str(uuid4()),
                    publication_id=draft.publication_id,
                    draft_id=draft.draft_id,
                    expected_draft_revision=draft.expected_draft_revision,
                    expected_draft_snapshot_hash=draft.draft_snapshot_hash,
                    second_confirmation=True,
                ),
            )

    def test_confirm_requires_snapshot_binding_and_writes_independent_projection(self) -> None:
        draft = self.service.create_draft(context=self.context, command=self._draft_command())
        command = PublicationConfirmCommand(
            command_id=str(uuid4()),
            publication_id=draft.publication_id,
            draft_id=draft.draft_id,
            expected_draft_revision=draft.expected_draft_revision,
            expected_draft_snapshot_hash=draft.draft_snapshot_hash,
            second_confirmation=True,
        )
        result = self.service.confirm_draft(context=self.context, command=command)

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.publication_state, "confirmed")
        self.assertEqual(result.projection_state, "active")
        self.assertEqual(result.publication_version, 1)
        self.assertTrue(result.ai_disclosure_required)
        self.assertNotEqual(result.public_projection_hash, self.memory.content_hash)
        self.assertEqual(self.repository.public_projection_count(), 1)

        replay = self.service.confirm_draft(context=self.context, command=command)
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.publication_version_id, result.publication_version_id)

        with self.assertRaises(PublicationAuthorityConflict):
            self.service.confirm_draft(
                context=self.context,
                command=PublicationConfirmCommand(
                    command_id=str(uuid4()),
                    publication_id=draft.publication_id,
                    draft_id=draft.draft_id,
                    expected_draft_revision=draft.expected_draft_revision,
                    expected_draft_snapshot_hash="f" * 64,
                    second_confirmation=True,
                ),
            )

    def test_ordered_items_are_bound_into_one_draft_and_projection(self) -> None:
        second_memory = PublicationAuthorityMemoryVersion(
            memory_version_id=str(uuid4()),
            memory_id=str(uuid4()),
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            authority_epoch=4,
            content_hash="b" * 64,
            is_current=True,
            memory_state="active",
            source_state="active",
            decision="accepted",
            decision_receipt_id=str(uuid4()),
        )
        self.repository.seed_memory_version(second_memory)
        items = (
            PublicationDraftItemCommand(
                memory_version_id=self.memory.memory_version_id,
                public_title="第一章",
                public_body="第一段公开正文。",
            ),
            PublicationDraftItemCommand(
                memory_version_id=second_memory.memory_version_id,
                public_title="第二章",
                public_body="第二段公开正文。",
            ),
        )
        draft = self.service.create_draft(
            context=self.context,
            command=PublicationDraftCommand(command_id=str(uuid4()), items=items),
        )

        self.assertEqual(draft.item_count, 2)
        self.assertEqual([item.item_index for item in draft.items], [0, 1])
        self.assertEqual([item.preview_title for item in draft.items], ["第一章", "第二章"])

        reversed_draft = self.service.create_draft(
            context=self.context,
            command=PublicationDraftCommand(
                command_id=str(uuid4()),
                items=tuple(reversed(items)),
            ),
        )
        self.assertNotEqual(draft.draft_snapshot_hash, reversed_draft.draft_snapshot_hash)

        confirmed = self.service.confirm_draft(
            context=self.context,
            command=PublicationConfirmCommand(
                command_id=str(uuid4()),
                publication_id=draft.publication_id,
                draft_id=draft.draft_id,
                expected_draft_revision=draft.expected_draft_revision,
                expected_draft_snapshot_hash=draft.draft_snapshot_hash,
                second_confirmation=True,
            ),
        )
        self.assertEqual(confirmed.item_count, 2)
        projection = self.repository.public_projection_content_snapshot(
            confirmed.publication_id,
            confirmed.publication_version_id,
        )
        self.assertIsNotNone(projection)
        self.assertEqual(
            [item["displayTitle"] for item in projection["items"]],  # type: ignore[index]
            ["第一章", "第二章"],
        )

    def test_duplicate_memory_version_cannot_appear_twice_in_one_draft(self) -> None:
        item = PublicationDraftItemCommand(
            memory_version_id=self.memory.memory_version_id,
            public_title="重复条目",
            public_body="同一正式记忆不能重复发布。",
        )
        with self.assertRaises(PublicationAuthorityConflict):
            PublicationDraftCommand(
                command_id=str(uuid4()),
                items=(item, item),
            )

    def test_confirmation_rechecks_every_memory_version_in_an_ordered_draft(self) -> None:
        second_memory = PublicationAuthorityMemoryVersion(
            memory_version_id=str(uuid4()),
            memory_id=str(uuid4()),
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            authority_epoch=4,
            content_hash="c" * 64,
            is_current=True,
            memory_state="active",
            source_state="active",
            decision="accepted",
            decision_receipt_id=str(uuid4()),
        )
        self.repository.seed_memory_version(second_memory)
        draft = self.service.create_draft(
            context=self.context,
            command=PublicationDraftCommand(
                command_id=str(uuid4()),
                items=(
                    PublicationDraftItemCommand(
                        memory_version_id=self.memory.memory_version_id,
                        public_title="仍有效",
                        public_body="第一条仍然有效。",
                    ),
                    PublicationDraftItemCommand(
                        memory_version_id=second_memory.memory_version_id,
                        public_title="即将过期",
                        public_body="第二条稍后被新版本替代。",
                    ),
                ),
            ),
        )
        self.repository.seed_memory_version(
            PublicationAuthorityMemoryVersion(
                **{**second_memory.__dict__, "is_current": False}
            )
        )

        with self.assertRaises(PublicationAuthorityNotPublishable):
            self.service.confirm_draft(
                context=self.context,
                command=PublicationConfirmCommand(
                    command_id=str(uuid4()),
                    publication_id=draft.publication_id,
                    draft_id=draft.draft_id,
                    expected_draft_revision=draft.expected_draft_revision,
                    expected_draft_snapshot_hash=draft.draft_snapshot_hash,
                    second_confirmation=True,
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

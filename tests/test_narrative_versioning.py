from dataclasses import replace
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.narrative.contracts import (
    BookProjectState,
    BookProjectType,
    NarrativeArtifactRecord,
    NarrativeArtifactState,
    NarrativeArtifactType,
    NarrativeCommandEnvelope,
    NarrativeCommandType,
    NarrativeMemoryRef,
    NarrativeNarratorType,
    NarrativeProjectRecord,
    NarrativeScope,
    NarrativeSnapshotRecord,
)
from app.services.narrative_generation import NarrativeCommandService
from app.services.narrative_generation import NarrativeGenerationError
from app.services.narrative_project import InMemoryNarrativeRepository


def _hash(value):
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class NarrativeVersioningTests(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryNarrativeRepository()
        self.scope = NarrativeScope(
            vault_id="vault-1",
            owner_subject_id="owner-1",
            actor_subject_id="owner-1",
            subject_persona_id="owner-1",
            authority_epoch=1,
        )
        instant = "2026-08-30T00:00:00+00:00"
        self.project = self.repo.create_or_get_project(
            NarrativeProjectRecord(
                project_id=str(uuid4()),
                scope=self.scope,
                project_type=BookProjectType.SELF_AUTOBIOGRAPHY,
                narrator_type=NarrativeNarratorType.SELF_FIRST_PERSON,
                title="我的自传",
                state=BookProjectState.WRITING,
                project_version=0,
                privacy_state="private",
                created_at=instant,
                updated_at=instant,
            )
        )
        content = {"event": "我在北方的一所大学学习计算机。"}
        self.memory_ref = NarrativeMemoryRef(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            content_hash=_hash(content),
            content=content,
            memory_kind="experience",
            perspective_type="ownerRecalled",
            epistemic_status="ownerConfirmed",
            sensitivity="normal",
        )
        snapshot = self.repo.save_snapshot(
            NarrativeSnapshotRecord(
                snapshot_id=str(uuid4()),
                project_id=self.project.project_id,
                vault_id=self.scope.vault_id,
                authority_epoch=1,
                memory_refs=(self.memory_ref,),
                source_fingerprint=_hash([self.memory_ref.memory_version_id]),
                snapshot_hash=_hash(self.memory_ref.public_contract(include_content=True)),
                created_by="owner-1",
                created_at=instant,
            )
        )
        self.project = self.repo.save_project(
            replace(self.project, current_memory_snapshot_id=snapshot.snapshot_id),
            expected_version=0,
        )
        self.chapter = self.repo.append_artifact(
            NarrativeArtifactRecord(
                artifact_version_id=str(uuid4()),
                project_id=self.project.project_id,
                artifact_type=NarrativeArtifactType.CHAPTER,
                artifact_key="chapter-1",
                version_number=1,
                memory_snapshot_id=snapshot.snapshot_id,
                state=NarrativeArtifactState.READY_FOR_REVIEW,
                content_text="我在北方学习。",
                payload={
                    "title": "求学",
                    "claims": [{
                        "claimId": "p1",
                        "text": "我在北方学习。",
                        "memoryVersionIds": [self.memory_ref.memory_version_id],
                    }],
                },
                content_hash=_hash("我在北方学习。"),
                origin="generated",
                created_at=instant,
            )
        )
        self.service = NarrativeCommandService(self.repo, object())

    def command(self, kind, payload, version=None):
        return self.service.execute(
            scope=self.scope,
            project_id=self.project.project_id,
            command=NarrativeCommandEnvelope(
                command_id=str(uuid4()),
                command_type=kind,
                expected_project_version=(
                    self.repo.get_project(
                        scope=self.scope, project_id=self.project.project_id
                    ).project_version
                    if version is None
                    else version
                ),
                confirmed=True,
                payload=payload,
            ),
        )

    def test_user_edit_appends_version_without_overwriting_source(self):
        result = self.command(
            NarrativeCommandType.EDIT_ARTIFACT,
            {
                "artifactVersionId": self.chapter.artifact_version_id,
                "contentText": "那段求学经历发生在北方。",
                "title": "求学经历",
            },
        )
        self.assertEqual(result["artifact"]["versionNumber"], 2)
        self.assertEqual(result["artifact"]["parentVersionId"], self.chapter.artifact_version_id)
        versions = self.repo.list_artifacts(
            project_id=self.project.project_id,
            artifact_type=NarrativeArtifactType.CHAPTER,
        )
        self.assertEqual([item.content_text for item in versions], [
            "我在北方学习。",
            "那段求学经历发生在北方。",
        ])

    def test_restore_historical_version_creates_a_new_current_version(self):
        self.command(
            NarrativeCommandType.EDIT_ARTIFACT,
            {
                "artifactVersionId": self.chapter.artifact_version_id,
                "contentText": "那段求学经历发生在北方。",
            },
        )
        result = self.command(
            NarrativeCommandType.RESTORE_ARTIFACT_VERSION,
            {"artifactVersionId": self.chapter.artifact_version_id},
        )
        self.assertEqual(result["artifact"]["versionNumber"], 3)
        self.assertEqual(result["artifact"]["contentText"], "我在北方学习。")
        self.assertEqual(
            result["artifact"]["payload"]["restoredFromVersionId"],
            self.chapter.artifact_version_id,
        )

    def test_outline_structure_normalizes_order_and_preserves_memory_refs(self):
        nodes = NarrativeCommandService._validated_outline_nodes(
            [
                {
                    "chapterKey": "chapter-two",
                    "title": "成长",
                    "order": 99,
                    "hidden": False,
                    "memoryVersionIds": [self.memory_ref.memory_version_id],
                },
                {
                    "chapterKey": "chapter-one",
                    "title": "出发",
                    "hidden": True,
                    "memoryVersionIds": [self.memory_ref.memory_version_id],
                },
            ]
        )
        self.assertEqual([item["order"] for item in nodes], [1, 2])
        self.assertEqual(nodes[0]["memoryVersionIds"], [self.memory_ref.memory_version_id])
        self.assertTrue(nodes[1]["hidden"])

    def test_outline_structure_rejects_an_entirely_hidden_book(self):
        with self.assertRaises(NarrativeGenerationError):
            NarrativeCommandService._validated_outline_nodes(
                [
                    {
                        "chapterKey": "chapter-one",
                        "title": "出发",
                        "hidden": True,
                        "memoryVersionIds": [self.memory_ref.memory_version_id],
                    }
                ]
            )

    def test_setup_confirmation_requires_reader_private_scope_and_rules_version(self):
        value = NarrativeCommandService._validated_setup_context({
            "primaryReader": "family",
            "privacyScope": "private",
            "privacyConfirmed": True,
            "confirmationRulesVersion": "narrative-setup-v1",
        })
        self.assertEqual(value["primaryReader"], "family")
        with self.assertRaises(NarrativeGenerationError):
            NarrativeCommandService._validated_setup_context({
                "primaryReader": "family",
                "privacyScope": "private",
                "privacyConfirmed": False,
                "confirmationRulesVersion": "narrative-setup-v1",
            })

    def test_pause_and_resume_restore_the_exact_checkpoint(self):
        paused = self.command(NarrativeCommandType.PAUSE_PROJECT, {})["project"]
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(paused["pausedFromState"], "writing")
        resumed = self.command(NarrativeCommandType.RESUME_PROJECT, {})["project"]
        self.assertEqual(resumed["state"], "writing")
        self.assertIsNone(resumed["pausedFromState"])


if __name__ == "__main__":
    unittest.main()

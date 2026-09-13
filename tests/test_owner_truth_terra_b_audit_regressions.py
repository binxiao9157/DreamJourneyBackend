"""Regression coverage for the 2026-09-08 Terra B implementation audit.

These tests intentionally assert the product contracts, rather than the
previous audit observations.  They use only synthetic data and the in-memory
repositories so they are safe to run locally and do not imply PostgreSQL or
provider acceptance.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
)
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.formal_fact_eligibility import evaluate_formal_fact_eligibility
from app.domain.owner_truth.memory_changeset import (
    OwnerTruthMemoryChangeOperationKind,
    build_memory_changeset,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    canonicalize_memory_payload,
)
from app.services.formal_memory_conversation_snapshot import (
    bind_provider_role_text,
    FormalMemoryConversationSnapshotService,
)
from app.services.owner_truth_echo_conversation_context import (
    resolve_owner_truth_retrieval_query,
)
from tests.test_owner_truth_b_memory_quality_fixture import (
    OwnerTruthBMemoryQualityFixtureTests,
    _content_hash,
)
from tests.test_owner_truth_conversation import OwnerTruthConversationTests
from tests.test_owner_truth_memory_changeset import OwnerTruthMemoryChangeSetTests
from tests.test_owner_truth_memory_changeset_review import (
    OwnerTruthMemoryChangeSetReviewTests,
)


class _ProjectionStore:
    """Minimal formal-projection port for the Live snapshot contract test."""

    def __init__(self, projection: dict[str, object]) -> None:
        self._projection = projection

    def owner_truth_memory_projection_repository(self) -> "_ProjectionStore":
        return self

    def read(self, *, context):
        del context
        return self._projection


class OwnerTruthTerraBAuditRegressionTests(unittest.TestCase):
    def _review_fixture(self) -> OwnerTruthMemoryChangeSetReviewTests:
        fixture = OwnerTruthMemoryChangeSetReviewTests()
        fixture.setUp()
        return fixture

    @staticmethod
    def _correct(
        fixture: OwnerTruthMemoryChangeSetReviewTests,
        *,
        candidate,
        value: dict[str, object],
        command_id: str,
        expected_memory_revision: int,
    ):
        proposal = fixture.service.preview_changeset(
            candidate_id=candidate.candidate_id,
            context=fixture.context,
            corrected_value=value,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )
        if proposal is None:
            raise AssertionError("V5 correction must have an Owner-visible ChangeSet preview")
        return fixture.service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                expected_memory_revision=expected_memory_revision,
                expected_change_set_id=proposal.change_set.change_set_id,
                expected_proposal_hash=proposal.proposal_hash,
                action=CandidateReviewAction.CORRECT,
                corrected_value=value,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                reason_code="ownerCorrected",
            ),
            context=fixture.context,
        )

    def test_p01_corrected_new_fact_activates_its_immutable_value(self) -> None:
        fixture = self._review_fixture()
        candidate = fixture._candidate()
        fixture.store.repository.seed(candidate)
        value = deepcopy(candidate.content)
        value["statement"] = "我在杭州时不喜欢吃东坡肉"

        result = self._correct(
            fixture,
            candidate=candidate,
            value=value,
            command_id="terra-audit-correct-add-001",
            expected_memory_revision=0,
        )

        self.assertEqual(result.memory_activation.outcome, "created")
        history = fixture.service.list_memory_version_history(
            memory_id=str(result.memory_activation.memory_id), context=fixture.context
        )
        self.assertEqual(history.versions[0].content["statement"], value["statement"])
        self.assertEqual(history.versions[0].content["qualifiers"]["polarity"], "negative")

    def test_p02_weaker_candidate_preserves_confirmed_stronger_qualifier(self) -> None:
        fixture = self._review_fixture()
        stronger = fixture._candidate()
        stronger_content = deepcopy(stronger.content)
        stronger_content["statement"] = "我在杭州时最喜欢吃东坡肉"
        stronger_content["qualifiers"]["strengthExpression"] = "最喜欢"
        stronger_content["qualifiers"]["superlativeAsserted"] = True
        stronger_content = canonicalize_memory_payload(
            kind=MemoryKind.KNOWLEDGE,
            payload=stronger_content,
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )
        stronger = replace(
            stronger,
            content_hash=_content_hash(stronger_content),
            payload={**stronger.payload, "content": stronger_content},
        )
        weaker = fixture._candidate()
        fixture.store.repository.seed(stronger)
        fixture.store.repository.seed(weaker)
        first = fixture._accept(
            stronger,
            command_id="terra-audit-stronger-001",
            expected_memory_revision=0,
        )
        second = fixture._accept(
            weaker,
            command_id="terra-audit-weaker-001",
            expected_memory_revision=1,
        )

        self.assertEqual(second.memory_activation.outcome, "revised")
        history = fixture.service.list_memory_version_history(
            memory_id=str(first.memory_activation.memory_id), context=fixture.context
        )
        current = history.versions[0].content["qualifiers"]
        self.assertTrue(current["superlativeAsserted"])
        self.assertEqual(current["strengthExpression"], "最喜欢")

    def test_p03_overlapping_opposite_periods_require_dispute(self) -> None:
        fixture = OwnerTruthMemoryChangeSetTests()
        fixture.setUp()
        positive = fixture._content(time="2016年至2020年")
        positive["qualifiers"]["validTime"].update(start="2016-01-01", end="2020-12-31")
        negative = fixture._content(
            statement="2018年至2022年我不喜欢吃东坡肉",
            polarity="negative",
            time="2018年至2022年",
        )
        negative["qualifiers"]["validTime"].update(start="2018-01-01", end="2022-12-31")
        result = build_memory_changeset(
            candidate=fixture._candidate(content=negative),
            current_memories=(fixture._current(content=positive, source_id=str(uuid4())),),
            base_memory_revision=1,
        )
        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.DISPUTE)

    def test_p04_live_retry_with_refreshed_versions_replays_delivery_receipt(self) -> None:
        fixture = OwnerTruthConversationTests()
        fixture.setUp()
        fixture.service.start_session(
            command=fixture.start(entry_mode="live", product_session_id="terra-audit-live"),
            context=fixture.context,
        )
        command = fixture.append(
            capture_mode="live",
            client_sequence_number=1,
            captured_at="2026-09-08T10:00:00Z",
        )
        created = fixture.service.append_message(command=command, context=fixture.context)
        replay = fixture.service.append_message(
            command=replace(
                command,
                expected_thread_version=created.thread_version,
                expected_session_version=created.session_version,
            ),
            context=fixture.context,
        )
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.message_id, created.message_id)
        self.assertEqual(replay.client_sequence_number, 1)
        self.assertEqual(replay.continuous_client_sequence, 1)
        self.assertEqual(replay.delivery_state, "contiguous")

    def test_p05_distinct_claim_subjects_remain_independently_eligible(self) -> None:
        fixture = OwnerTruthMemoryChangeSetTests()
        fixture.setUp()
        own = fixture._content(
            statement="我在杭州时喜欢吃东坡肉", subject_id="person-owner"
        )
        father = fixture._content(
            statement="我父亲在杭州时喜欢吃东坡肉", subject_id="person-father"
        )
        father["memorySubjectId"] = "person-owner"
        projection = self._projection_from_contents([own, father])
        eligibility = evaluate_formal_fact_eligibility(projection)

        self.assertEqual(len(eligibility.eligible_entries), 2)
        groups = projection["personMemoryModel"]["semanticConsolidation"]["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual({item["status"] for item in groups}, {"ready"})

    def test_p06_live_provider_role_is_budgeted_without_dropping_facts(self) -> None:
        fixture = OwnerTruthMemoryChangeSetTests()
        fixture.setUp()
        contents = [
            fixture._content(
                statement=f"我在杭州时喜欢吃第{index}道菜",
                object_label=f"第{index}道菜",
            )
            for index in range(100)
        ]
        projection = self._projection_from_contents(contents)
        store = _ProjectionStore(projection)
        snapshot = FormalMemoryConversationSnapshotService(store).build(
            context=self._projection_context()
        )
        bound = bind_provider_role_text(
            snapshot,
            system_role="你是用户的记忆助手。",
            speaking_style="友善、自然，事实准确。",
        )

        self.assertEqual(snapshot["coverage"]["eligibleFactCount"], 100)
        self.assertEqual(snapshot["coverage"]["includedFactCount"], 100)
        self.assertFalse(snapshot["coverage"]["truncated"])
        self.assertLessEqual(bound["providerRoleCharacterCount"], 32_768)
        self.assertEqual(
            bound["providerContextHash"],
            "sha256:"
            + hashlib.sha256(bound["providerRoleText"].encode("utf-8")).hexdigest(),
        )

    def test_p07_followup_query_is_resolved_from_same_session_history_before_search(self) -> None:
        turns = [
            {"role": "user", "text": "我哪年大学毕业？"},
            {"role": "assistant", "text": "我会从已确认记忆里查一查。"},
        ]
        resolved = resolve_owner_truth_retrieval_query(
            query="那是哪一年？", recent_turns=turns
        )
        self.assertTrue(resolved.used_history)
        self.assertEqual(resolved.retrieval_query, "我哪年大学毕业？")
        self.assertEqual(resolved.source, "sameProductSessionUserTurn")

    def test_p08_corrected_statement_never_persists_opposite_polarity(self) -> None:
        fixture = self._review_fixture()
        first = fixture._candidate()
        fixture.store.repository.seed(first)
        first_result = fixture._accept(
            first,
            command_id="terra-audit-existing-before-001",
            expected_memory_revision=0,
        )
        correction = fixture._candidate()
        fixture.store.repository.seed(correction)
        value = deepcopy(correction.content)
        value["statement"] = "我在杭州时不喜欢吃东坡肉"
        correction_result = self._correct(
            fixture,
            candidate=correction,
            value=value,
            command_id="terra-audit-existing-correct-001",
            expected_memory_revision=1,
        )
        # An opposite assertion for the same period is retained as a dispute
        # rather than silently rewriting the earlier confirmed fact.  The
        # correction itself must still carry the regenerated negative
        # structure in the created formal version.
        self.assertEqual(correction_result.memory_activation.outcome, "created")
        history = fixture.service.list_memory_version_history(
            memory_id=str(correction_result.memory_activation.memory_id), context=fixture.context
        )
        self.assertEqual(history.versions[0].content["statement"], value["statement"])
        self.assertEqual(history.versions[0].content["qualifiers"]["polarity"], "negative")

    @staticmethod
    def _projection_context():
        fixture = OwnerTruthBMemoryQualityFixtureTests()
        fixture.setUp()
        return fixture.context

    @staticmethod
    def _projection_from_contents(contents: list[dict[str, object]]) -> dict[str, object]:
        fixture = OwnerTruthBMemoryQualityFixtureTests()
        fixture.setUp()
        inputs = []
        for content in contents:
            canonical = canonicalize_memory_payload(
                kind=MemoryKind.KNOWLEDGE,
                payload=content,
                schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            )
            source_id = str(uuid4())
            inputs.append(
                OwnerTruthMemoryProjectionInput(
                    memory_id=str(uuid4()),
                    memory_version_id=str(uuid4()),
                    vault_id=fixture.vault_id,
                    owner_subject_id=fixture.owner_id,
                    authority_epoch=2,
                    version_number=1,
                    source_id=source_id,
                    source_version=1,
                    memory_kind="knowledge",
                    perspective_type="firstPerson",
                    epistemic_status="recalled",
                    sensitivity="standard",
                    content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                    content_hash=_content_hash(canonical),
                    content=canonical,
                    evidence_refs=({"sourceId": source_id, "sourceVersion": 1},),
                )
            )
        return build_ready_memory_projection(
            vault_id=fixture.vault_id,
            owner_subject_id=fixture.owner_id,
            authority_epoch=2,
            inputs=inputs,
            memory_revision=len(inputs),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

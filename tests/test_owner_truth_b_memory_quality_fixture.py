"""Regression coverage for the checked-in, independent Chinese quality corpus.

The deterministic branch is deliberately tested only for cases explicitly
marked ``lexical``. The remaining semantic cases are not relabelled as passed
until the separately gated real-embedding evaluator is run.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import unittest
from uuid import uuid4

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
)
from app.domain.owner_truth.search_documents import (
    OwnerTruthSearchDocumentProjection,
    build_owner_truth_search_document_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_search_quality_corpus import (
    OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES,
    load_owner_truth_memory_search_quality_corpus,
)
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures/owner_truth/memory_search_quality_zh_v1.json"
)


def _content_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class _SearchReader:
    def __init__(self, projection: OwnerTruthSearchDocumentProjection) -> None:
        self._projection = projection

    def read(self, *, context: OwnerTruthCommandContext) -> OwnerTruthSearchDocumentProjection:
        del context
        return self._projection


class _Store:
    def __init__(self, projection: OwnerTruthSearchDocumentProjection) -> None:
        self._reader = _SearchReader(projection)

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_memory_search_document_projection_repository(self) -> _SearchReader:
        return self._reader


class OwnerTruthBMemoryQualityFixtureTests(unittest.TestCase):
    """The corpus has no production material and no prompt-only answer key."""

    def setUp(self) -> None:
        self.owner_id = "owner-quality-fixture"
        self.vault_id = "vault-quality-fixture"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.cases = load_owner_truth_memory_search_quality_corpus(FIXTURE_PATH)

    def test_200_independent_chinese_cases_have_split_and_category_boundaries(self) -> None:
        self.assertEqual(len(self.cases), 200)
        self.assertEqual(sum(case.split == "dev" for case in self.cases), 140)
        self.assertEqual(sum(case.split == "holdout" for case in self.cases), 60)
        self.assertEqual(
            {case.category for case in self.cases},
            OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES,
        )
        self.assertEqual(
            {
                category: sum(item.category == category for item in self.cases)
                for category in OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES
            },
            {category: 10 for category in OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES},
        )
        self.assertEqual(len({case.fact_text for case in self.cases}), 200)
        self.assertEqual(len({case.query for case in self.cases}), 200)
        self.assertEqual(sum(case.evaluation_mode == "lexical" for case in self.cases), 40)
        self.assertEqual(sum(case.evaluation_mode == "semantic" for case in self.cases), 160)

    def test_lexical_cases_use_the_actual_owner_scoped_search_projection(self) -> None:
        lexical_cases = tuple(case for case in self.cases if case.evaluation_mode == "lexical")
        for case in lexical_cases:
            with self.subTest(case=case.case_id, category=case.category):
                expected_version = str(uuid4())
                expected_content = {"claim": case.fact_text, "tags": [case.anchor]}
                expected = (
                    OwnerTruthMemoryProjectionInput(
                        memory_id=str(uuid4()),
                        memory_version_id=expected_version,
                        vault_id=self.vault_id,
                        owner_subject_id=self.owner_id,
                        authority_epoch=2,
                        version_number=1,
                        source_id=str(uuid4()),
                        source_version=1,
                        memory_kind="knowledge",
                        perspective_type="firstPerson",
                        epistemic_status="recalled",
                        sensitivity="standard",
                        content_schema_version="owner-truth-v1",
                        content_hash=_content_hash(expected_content),
                        content=expected_content,
                        evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
                    )
                )
                distractor_version = str(uuid4())
                distractor_content = {"claim": case.distractor_text, "tags": [case.case_id]}
                distractor = (
                    OwnerTruthMemoryProjectionInput(
                        memory_id=str(uuid4()),
                        memory_version_id=distractor_version,
                        vault_id=self.vault_id,
                        owner_subject_id=self.owner_id,
                        authority_epoch=2,
                        version_number=1,
                        source_id=str(uuid4()),
                        source_version=1,
                        memory_kind="knowledge",
                        perspective_type="firstPerson",
                        epistemic_status="recalled",
                        sensitivity="standard",
                        content_schema_version="owner-truth-v1",
                        content_hash=_content_hash(distractor_content),
                        content=distractor_content,
                        evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
                    )
                )
                memory_projection = build_ready_memory_projection(
                    vault_id=self.vault_id,
                    owner_subject_id=self.owner_id,
                    authority_epoch=2,
                    inputs=(expected, distractor),
                )
                search_projection = build_owner_truth_search_document_projection(
                    memory_projection=memory_projection
                )
                assert search_projection is not None
                result = OwnerTruthMemorySearchReadService(_Store(search_projection)).read(
                    context=self.context,
                    query=case.query,
                    limit=2,
                )
                self.assertTrue(result.hits)
                self.assertEqual(
                    result.hits[0].document.memory_version_id,
                    expected_version,
                )
                self.assertNotEqual(
                    result.hits[0].document.memory_version_id,
                    distractor_version,
                )
                self.assertFalse(result.semantic_ranking_available)
                self.assertEqual(result.retrieval_mode, "deterministicTextFallback")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

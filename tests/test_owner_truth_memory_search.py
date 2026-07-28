from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
)
from app.domain.owner_truth.search_documents import (
    OWNER_TRUTH_MEMORY_SEARCH_MAX_QUERY_CHARACTERS,
    OwnerTruthMemorySearchReadError,
    OwnerTruthSearchDocumentProjection,
    build_owner_truth_search_document_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_search_read import (
    OwnerTruthMemorySearchReadAccessDenied,
    OwnerTruthMemorySearchReadService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _SearchDocumentProjectionReader:
    def __init__(self, projection: OwnerTruthSearchDocumentProjection | None) -> None:
        self.projection = projection

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjection | None:
        del context
        return self.projection


class _Store:
    def __init__(self, projection: OwnerTruthSearchDocumentProjection | None) -> None:
        self.reader = _SearchDocumentProjectionReader(projection)

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_memory_search_document_projection_repository(self):
        return self.reader


class OwnerTruthMemorySearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-memory-search"
        self.vault_id = "vault-memory-search"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.content = {
            "claim": "我在北京做出了职业选择，留下 private search evidence。",
            "tags": ["职业", "选择"],
        }
        self.memory = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=4,
            version_number=1,
            source_id=str(uuid4()),
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=_hash(self.content),
            content=self.content,
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )
        self.snapshot = build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=4,
            inputs=(self.memory,),
        )
        self.search_projection = build_owner_truth_search_document_projection(
            memory_projection=self.snapshot
        )
        assert self.search_projection is not None

    def test_owner_searches_current_confirmed_memory_without_returning_text_or_source(self) -> None:
        result = OwnerTruthMemorySearchReadService(_Store(self.search_projection)).read(
            context=self.context,
            query="职业选择",
            limit=5,
        )

        self.assertEqual(result.state, "ready")
        self.assertEqual(len(result.hits), 1)
        summary = result.value_free_summary()
        self.assertEqual(summary["queryPlan"]["retrievalMode"], "deterministicTextFallback")
        self.assertFalse(summary["queryPlan"]["semanticRankingAvailable"])
        self.assertEqual(summary["hits"][0]["citation"]["memoryVersionId"], self.memory.memory_version_id)
        rendered = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("private search evidence", rendered)
        self.assertNotIn("职业选择", rendered)
        self.assertNotIn(self.memory.source_id, rendered)
        self.assertNotIn('"searchText":', rendered)
        self.assertNotIn("structuredTerms", rendered)

    def test_search_normalizes_full_width_and_case_without_claiming_vector_semantics(self) -> None:
        result = OwnerTruthMemorySearchReadService(_Store(self.search_projection)).read(
            context=self.context,
            query="PRIVATE　SEARCH",
            limit=1,
        )

        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].match_kind, "structuredTerm")

    def test_missing_or_rebuilding_search_index_returns_no_search_state_or_stale_hits(self) -> None:
        result = OwnerTruthMemorySearchReadService(_Store(None)).read(
            context=self.context,
            query="职业",
        )

        self.assertEqual(result.state, "rebuilding")
        self.assertIsNone(result.projection)
        self.assertIsNone(result.query_plan)
        self.assertEqual(result.hits, ())

    def test_cross_owner_and_oversized_query_fail_closed(self) -> None:
        service = OwnerTruthMemorySearchReadService(_Store(self.search_projection))
        denied_context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="other-owner",
        )
        with self.assertRaises(OwnerTruthMemorySearchReadAccessDenied):
            service.read(context=denied_context, query="职业")
        with self.assertRaises(OwnerTruthMemorySearchReadError):
            service.read(
                context=self.context,
                query="x" * (OWNER_TRUTH_MEMORY_SEARCH_MAX_QUERY_CHARACTERS + 1),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

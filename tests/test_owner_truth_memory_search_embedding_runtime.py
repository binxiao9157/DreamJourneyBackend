from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.services.owner_truth_memory_search_embedding_runtime import (
    OwnerTruthHttpJsonEmbeddingProvider,
    build_configured_embedding_provider,
    embedding_runtime_readiness,
)
from app.services.owner_truth_memory_search_hybrid import OwnerTruthEmbeddingModel


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False


class OwnerTruthMemorySearchEmbeddingRuntimeTests(unittest.TestCase):
    def _ready_settings(self) -> Settings:
        return replace(
            Settings(),
            owner_truth_memory_search_embedding_provider="httpJson",
            owner_truth_memory_search_embedding_http_json_url="https://embedding.test/v1/embeddings",
            owner_truth_memory_search_embedding_http_json_api_key="not-a-real-key",
            owner_truth_memory_search_embedding_provider_model_id="BAAI/bge-m3",
            owner_truth_memory_search_embedding_egress_approved=True,
        )

    def test_disabled_by_default_and_never_builds_provider(self) -> None:
        readiness = embedding_runtime_readiness(Settings())
        self.assertFalse(readiness.provider_ready)
        self.assertEqual(readiness.reason, "runtimeDisabled")
        self.assertIsNone(build_configured_embedding_provider(Settings()))

    def test_egress_approval_and_migration_model_are_required(self) -> None:
        pending = replace(self._ready_settings(), owner_truth_memory_search_embedding_egress_approved=False)
        self.assertEqual(
            embedding_runtime_readiness(pending).reason,
            "privateEmbeddingEgressNotApproved",
        )
        incompatible = replace(
            self._ready_settings(),
            owner_truth_memory_search_embedding_dimensions=768,
        )
        self.assertEqual(
            embedding_runtime_readiness(incompatible).reason,
            "embeddingModelMigrationMismatch",
        )

    def test_http_provider_validates_batch_shape_and_redacts_request_values(self) -> None:
        provider = build_configured_embedding_provider(self._ready_settings())
        assert provider is not None
        with patch(
            "app.services.owner_truth_memory_search_embedding_runtime.urlopen",
            return_value=_Response(
                {"data": [{"embedding": [0.0] * 1024}, {"embedding": [0.1] * 1024}]}
            ),
        ) as mocked:
            vectors = provider.embed_documents(texts=("第一段私密正文", "第二段私密正文"))
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 1024)
        request = mocked.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["model"], "BAAI/bge-m3")
        self.assertNotIn("not-a-real-key", request.full_url)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(provider.model, OwnerTruthEmbeddingModel("bge-m3", "v1", 1024))

    def test_http_provider_rejects_wrong_dimensions(self) -> None:
        provider = OwnerTruthHttpJsonEmbeddingProvider(
            endpoint_url="https://embedding.test/v1/embeddings",
            api_key="not-a-real-key",
            provider_model_id="BAAI/bge-m3",
            model=OwnerTruthEmbeddingModel("bge-m3", "v1", 1024),
            timeout_seconds=1,
        )
        with patch(
            "app.services.owner_truth_memory_search_embedding_runtime.urlopen",
            return_value=_Response({"data": [{"embedding": [0.0]}]}),
        ):
            with self.assertRaisesRegex(Exception, "dimensions"):
                provider.embed_query(query="不会出网的单元测试")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Configuration-driven, privacy-gated embedding runtime for Owner Truth.

The search index is derived only from current, authorized SearchDocuments.
This module deliberately does not make an outbound request at import or
startup.  A caller must explicitly select a provider, configure its model
contract, and record the private-data egress approval before a ranker can be
created.  Missing evidence keeps the request path on the observable lexical
fallback rather than silently using a test-only ``app.state`` injection.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings
from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthEmbeddingModel,
    OwnerTruthMemorySearchHybridUnavailable,
    OwnerTruthQueryEmbedding,
)


OWNER_TRUTH_EMBEDDING_PROVIDER_DISABLED = "disabled"
OWNER_TRUTH_EMBEDDING_PROVIDER_HTTP_JSON = "httpJson"
OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL = OwnerTruthEmbeddingModel(
    "bge-m3",
    "v1",
    1024,
)


@dataclass(frozen=True)
class OwnerTruthEmbeddingRuntimeReadiness:
    """Value-free runtime state for diagnostics and readiness endpoints."""

    enabled: bool
    provider_ready: bool
    provider: str
    model_id: str
    model_version: str
    dimensions: int
    reason: str
    configuration_status: str
    egress_approved: bool

    def public_descriptor(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "providerReady": self.provider_ready,
            "provider": self.provider,
            "modelId": self.model_id,
            "modelVersion": self.model_version,
            "dimensions": self.dimensions,
            "reason": self.reason,
            "configurationStatus": self.configuration_status,
            "egressApproved": self.egress_approved,
        }


class OwnerTruthHttpJsonEmbeddingProvider:
    """Small provider adapter with a locked OpenAI-compatible JSON contract.

    The endpoint receives ``{"model": ..., "input": [text, ...]}`` and must
    return ``{"data": [{"embedding": [...]}, ...]}``.  Error messages are
    intentionally value-free so query text, endpoint credentials, and provider
    response bodies cannot leak into application diagnostics.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        api_key: str,
        provider_model_id: str,
        model: OwnerTruthEmbeddingModel,
        timeout_seconds: float,
    ) -> None:
        self._endpoint_url = _required_text(endpoint_url, field="embedding endpoint")
        self._api_key = _required_text(api_key, field="embedding API key")
        self._provider_model_id = _required_text(
            provider_model_id,
            field="embedding provider model",
        )
        self._model = model
        if not isinstance(timeout_seconds, (int, float)) or not 0.1 <= float(timeout_seconds) <= 60:
            raise OwnerTruthMemorySearchHybridUnavailable("embedding timeout is invalid")
        self._timeout_seconds = float(timeout_seconds)

    @property
    def model(self) -> OwnerTruthEmbeddingModel:
        return self._model

    def embed_query(self, *, query: str) -> OwnerTruthQueryEmbedding:
        vectors = self.embed_documents(texts=(query,))
        return OwnerTruthQueryEmbedding(model=self._model, values=vectors[0])

    def embed_queries(
        self,
        *,
        queries: Sequence[str],
    ) -> tuple[OwnerTruthQueryEmbedding, ...]:
        """Embed a bounded query batch with the same locked model contract."""

        vectors = self.embed_documents(texts=queries)
        return tuple(
            OwnerTruthQueryEmbedding(model=self._model, values=vector)
            for vector in vectors
        )

    def embed_documents(self, *, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        normalized = tuple(_required_text(text, field="embedding input") for text in texts)
        if not normalized:
            raise OwnerTruthMemorySearchHybridUnavailable("embedding input is empty")
        payload = json.dumps(
            {"model": self._provider_model_id, "input": list(normalized)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self._endpoint_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec B310
                response_payload = response.read()
        except HTTPError as error:
            raise OwnerTruthMemorySearchHybridUnavailable(
                f"embedding provider returned HTTP {int(error.code)}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OwnerTruthMemorySearchHybridUnavailable(
                "embedding provider is unavailable"
            ) from error
        try:
            decoded = json.loads(response_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OwnerTruthMemorySearchHybridUnavailable(
                "embedding provider returned invalid JSON"
            ) from error
        return _embedding_vectors(decoded, model=self._model, expected_count=len(normalized))


def embedding_runtime_readiness(settings: Settings) -> OwnerTruthEmbeddingRuntimeReadiness:
    """Evaluate configuration without contacting a provider or inspecting secrets."""

    provider = _normalized_provider(settings.owner_truth_memory_search_embedding_provider)
    model = _configured_model(settings)
    base = {
        "provider": provider,
        "model_id": model.model_id,
        "model_version": model.model_version,
        "dimensions": model.dimensions,
        "egress_approved": bool(settings.owner_truth_memory_search_embedding_egress_approved),
    }
    if provider == OWNER_TRUTH_EMBEDDING_PROVIDER_DISABLED:
        return OwnerTruthEmbeddingRuntimeReadiness(
            enabled=False,
            provider_ready=False,
            reason="runtimeDisabled",
            configuration_status="disabled",
            **base,
        )
    if provider != OWNER_TRUTH_EMBEDDING_PROVIDER_HTTP_JSON:
        return OwnerTruthEmbeddingRuntimeReadiness(
            enabled=False,
            provider_ready=False,
            reason="embeddingProviderUnsupported",
            configuration_status="unsupported",
            **base,
        )
    if not bool(settings.owner_truth_memory_search_embedding_egress_approved):
        return OwnerTruthEmbeddingRuntimeReadiness(
            enabled=False,
            provider_ready=False,
            reason="privateEmbeddingEgressNotApproved",
            configuration_status="blockedByPrivacyApproval",
            **base,
        )
    if not (
        _present(settings.owner_truth_memory_search_embedding_http_json_url)
        and _present(settings.owner_truth_memory_search_embedding_http_json_api_key)
        and _present(settings.owner_truth_memory_search_embedding_provider_model_id)
    ):
        return OwnerTruthEmbeddingRuntimeReadiness(
            enabled=False,
            provider_ready=False,
            reason="embeddingProviderConfigurationIncomplete",
            configuration_status="incomplete",
            **base,
        )
    if model != OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL:
        return OwnerTruthEmbeddingRuntimeReadiness(
            enabled=False,
            provider_ready=False,
            reason="embeddingModelMigrationMismatch",
            configuration_status="migrationRequired",
            **base,
        )
    return OwnerTruthEmbeddingRuntimeReadiness(
        enabled=True,
        provider_ready=True,
        reason="embeddingRuntimeReady",
        configuration_status="valid",
        **base,
    )


def build_configured_embedding_provider(
    settings: Settings,
) -> OwnerTruthHttpJsonEmbeddingProvider | None:
    """Build the provider only when every local admission condition is met."""

    readiness = embedding_runtime_readiness(settings)
    if not readiness.provider_ready:
        return None
    return OwnerTruthHttpJsonEmbeddingProvider(
        endpoint_url=str(settings.owner_truth_memory_search_embedding_http_json_url),
        api_key=str(settings.owner_truth_memory_search_embedding_http_json_api_key),
        provider_model_id=str(
            settings.owner_truth_memory_search_embedding_provider_model_id
        ),
        model=_configured_model(settings),
        timeout_seconds=settings.owner_truth_memory_search_embedding_timeout_seconds,
    )


def _embedding_vectors(
    response: object,
    *,
    model: OwnerTruthEmbeddingModel,
    expected_count: int,
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(response, Mapping) or not isinstance(response.get("data"), list):
        raise OwnerTruthMemorySearchHybridUnavailable(
            "embedding provider response misses data"
        )
    rows = response["data"]
    if len(rows) != expected_count:
        raise OwnerTruthMemorySearchHybridUnavailable(
            "embedding provider response count does not match request"
        )
    values: list[tuple[float, ...]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise OwnerTruthMemorySearchHybridUnavailable("embedding provider row is invalid")
        embedding = row.get("embedding")
        if not isinstance(embedding, list):
            raise OwnerTruthMemorySearchHybridUnavailable("embedding provider row misses embedding")
        values.append(OwnerTruthQueryEmbedding(model=model, values=tuple(embedding)).values)
    return tuple(values)


def _configured_model(settings: Settings) -> OwnerTruthEmbeddingModel:
    return OwnerTruthEmbeddingModel(
        str(settings.owner_truth_memory_search_embedding_model_id),
        str(settings.owner_truth_memory_search_embedding_model_version),
        int(settings.owner_truth_memory_search_embedding_dimensions),
    )


def _normalized_provider(value: object) -> str:
    raw = str(value or "").strip()
    if raw.lower() in {"", "disabled"}:
        return OWNER_TRUTH_EMBEDDING_PROVIDER_DISABLED
    if raw.lower() == "httpjson":
        return OWNER_TRUTH_EMBEDDING_PROVIDER_HTTP_JSON
    return raw


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OwnerTruthMemorySearchHybridUnavailable(f"{field} is required")
    return text


def _present(value: object) -> bool:
    return bool(str(value or "").strip())


__all__ = [
    "OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL",
    "OWNER_TRUTH_EMBEDDING_PROVIDER_DISABLED",
    "OWNER_TRUTH_EMBEDDING_PROVIDER_HTTP_JSON",
    "OwnerTruthEmbeddingRuntimeReadiness",
    "OwnerTruthHttpJsonEmbeddingProvider",
    "build_configured_embedding_provider",
    "embedding_runtime_readiness",
]

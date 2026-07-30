"""QA-only, value-free comparison between legacy and Owner Truth Context plans.

The legacy ``/context/build`` route remains the public Context authority until
a future cohort promotion.  This service never changes its response and never
uses the legacy result as V4 input.  It only builds both paths from one
normalized Owner request and emits comparison metadata that cannot contain the
query, archive text, KBLite facts, care content, or generation text.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionError
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.context_packet import ContextPacketBuilder
from app.services.owner_truth_context_shadow_build import OwnerTruthContextShadowBuildService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionStore


OWNER_TRUTH_CONTEXT_SHADOW_COMPARE_SCHEMA_VERSION = "owner-truth-context-shadow-compare-v1"
OWNER_TRUTH_CONTEXT_SHADOW_COMPARE_POLICY_VERSION = "owner-truth-context-shadow-compare-policy-v1"
_ALLOWED_INTENT = "echo_chat"
_NO_PERSONAL_MEMORY_FALLBACKS = frozenset(
    {
        "owner_truth_context_unavailable_no_personal_memory",
        "owner_truth_context_no_eligible_personal_memory",
        "owner_truth_context_search_unavailable_no_personal_memory",
        "owner_truth_context_no_query_match_no_personal_memory",
    }
)


class OwnerTruthContextShadowCompareError(OwnerTruthMemoryProjectionError):
    """The value-free Context comparison could not preserve its invariants."""


def _normalized_request(payload: Mapping[str, Any] | None) -> dict[str, str]:
    if payload is not None and not isinstance(payload, Mapping):
        raise OwnerTruthContextShadowCompareError("context comparison payload must be an object")

    raw_payload = payload or {}
    raw_intent = raw_payload.get("intent")
    intent = raw_intent.strip() if isinstance(raw_intent, str) else ""
    if intent not in {"", _ALLOWED_INTENT}:
        raise OwnerTruthContextShadowCompareError("context comparison intent is unsupported")

    raw_query = raw_payload.get("query")
    query = raw_query.strip() if isinstance(raw_query, str) else ""
    return {"intent": _ALLOWED_INTENT, "query": query}


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthContextShadowCompareError(f"{field} must be an object")
    return value


def _list(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise OwnerTruthContextShadowCompareError(f"{field} must be a list")
    return value


def _nonblank_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OwnerTruthContextShadowCompareError(f"{field} must be a nonblank string")
    return value.strip()


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthContextShadowCompareError(f"{field} must be a non-negative integer")
    return value


def _optional_hash(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _nonblank_text(value, field=field)


def _request_correlation(value: Any, *, field: str) -> dict[str, Any]:
    correlation = _mapping(value, field=field)
    return {
        "schemaVersion": _nonblank_text(
            correlation.get("schemaVersion"),
            field=f"{field}.schemaVersion",
        ),
        "intent": _nonblank_text(correlation.get("intent"), field=f"{field}.intent"),
        "queryHash": _optional_hash(correlation.get("queryHash"), field=f"{field}.queryHash"),
        "queryLength": _nonnegative_int(
            correlation.get("queryLength"),
            field=f"{field}.queryLength",
        ),
    }


def _shadow_request_correlation(value: Any) -> dict[str, Any]:
    request = _mapping(value, field="contextShadow.request")
    return {
        "schemaVersion": ContextPacketBuilder.request_correlation_schema_version,
        "intent": _nonblank_text(request.get("intent"), field="contextShadow.request.intent"),
        "queryHash": _optional_hash(
            request.get("queryHash"),
            field="contextShadow.request.queryHash",
        ),
        "queryLength": _nonnegative_int(
            request.get("queryLength"),
            field="contextShadow.request.queryLength",
        ),
    }


def _has_typed_citation(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False
    citation = item.get("citation")
    source_ref = item.get("sourceRef")
    if not isinstance(citation, Mapping) or not isinstance(source_ref, Mapping):
        return False
    return all(
        isinstance(citation.get(field), str) and bool(citation.get(field).strip())
        for field in ("memoryId", "memoryVersionId", "sourceId", "contentHash", "vaultId")
    ) and isinstance(citation.get("memoryVersion"), int) and not isinstance(
        citation.get("memoryVersion"), bool
    ) and isinstance(citation.get("sourceVersion"), int) and not isinstance(
        citation.get("sourceVersion"), bool
    )


class OwnerTruthContextShadowCompareService:
    """Read both Context plans as an Owner-only QA observation.

    The implementation intentionally keeps the two results in-process and
    returns only counts, hashes, states, and Boolean citation integrity.  No
    caller can use this endpoint to retrieve the legacy packet or V4 selected
    citation identifiers.
    """

    def __init__(
        self,
        store: OwnerTruthMemoryProjectionStore,
        settings: Any,
        *,
        enabled: bool = False,
    ) -> None:
        self._store = store
        self._settings = settings
        self._enabled = bool(enabled)

    def compare(
        self,
        *,
        context: OwnerTruthCommandContext,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not self._enabled:
            raise OwnerTruthContextShadowCompareError("context comparison is disabled")

        request = _normalized_request(payload)
        # Read the V4 Projection first.  This proves the owner/vault boundary
        # before the legacy builder can inspect even the authenticated owner's
        # old Context data.
        shadow = OwnerTruthContextShadowBuildService(
            self._store,
            enabled=True,
        ).build(context=context, payload=request)

        legacy_packet = ContextPacketBuilder(self._store, self._settings).build(
            {
                "userId": context.owner_subject_id,
                "intent": request["intent"],
                "query": request["query"],
                "personaScope": "personal",
                "digitalHumanId": context.owner_subject_id,
                "lifecycleMode": "sunlight",
            }
        )

        legacy_correlation = _request_correlation(
            legacy_packet.get("requestCorrelation"),
            field="legacy.requestCorrelation",
        )
        v4_correlation = _shadow_request_correlation(shadow.get("request"))
        correlation_matches = legacy_correlation == v4_correlation

        legacy_selected = _list(legacy_packet.get("selectedContext"), field="legacy.selectedContext")
        legacy_filtered = _list(legacy_packet.get("filteredContext"), field="legacy.filteredContext")
        legacy_fallbacks = _list(legacy_packet.get("fallbacks"), field="legacy.fallbacks")
        v4_selected = _list(shadow.get("selectedContext"), field="contextShadow.selectedContext")
        v4_filtered = _list(shadow.get("filteredContext"), field="contextShadow.filteredContext")
        v4_fallbacks = _list(shadow.get("fallbacks"), field="contextShadow.fallbacks")
        authority = _mapping(shadow.get("authority"), field="contextShadow.authority")
        v4_state = _nonblank_text(authority.get("state"), field="contextShadow.authority.state")
        v4_typed_citations = all(_has_typed_citation(item) for item in v4_selected)
        v4_fallback_codes = tuple(
            fallback
            for fallback in v4_fallbacks
            if isinstance(fallback, str) and fallback in _NO_PERSONAL_MEMORY_FALLBACKS
        )

        if not correlation_matches:
            disposition = "request_mismatch"
        elif not v4_typed_citations:
            disposition = "v4_typed_citation_incomplete"
        elif v4_state != "ready" or not v4_selected or v4_fallback_codes:
            disposition = "v4_no_personal_memory"
        else:
            disposition = "observed"

        return {
            "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_COMPARE_SCHEMA_VERSION,
            "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_COMPARE_POLICY_VERSION,
            "shadowOnly": True,
            "legacyContextUnchanged": True,
            "legacyContextRead": True,
            "requestCorrelation": legacy_correlation,
            "requestCorrelationMatches": correlation_matches,
            "disposition": disposition,
            "legacy": {
                "schemaVersion": legacy_packet.get("schemaVersion"),
                "contextVersion": legacy_packet.get("contextVersion"),
                "selectedContextCount": len(legacy_selected),
                "filteredContextCount": len(legacy_filtered),
                "fallbackCount": len(legacy_fallbacks),
            },
            "v4": {
                "schemaVersion": shadow.get("schemaVersion"),
                "contextVersion": shadow.get("contextVersion"),
                "policyVersion": shadow.get("policyVersion"),
                "state": v4_state,
                "selectedContextCount": len(v4_selected),
                "filteredContextCount": len(v4_filtered),
                "fallbackCount": len(v4_fallbacks),
                "allSelectedItemsHaveTypedCitation": v4_typed_citations,
                "authorityEpochPresent": isinstance(authority.get("authorityEpoch"), int)
                and not isinstance(authority.get("authorityEpoch"), bool),
                "projectionCheckpointPresent": isinstance(
                    authority.get("projectionCheckpoint"), str
                )
                and bool(str(authority.get("projectionCheckpoint") or "").strip()),
            },
        }


__all__ = [
    "OWNER_TRUTH_CONTEXT_SHADOW_COMPARE_SCHEMA_VERSION",
    "OWNER_TRUTH_CONTEXT_SHADOW_COMPARE_POLICY_VERSION",
    "OwnerTruthContextShadowCompareError",
    "OwnerTruthContextShadowCompareService",
]

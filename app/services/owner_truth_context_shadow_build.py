"""QA-only Context V4 shadow build over confirmed Owner Truth memory.

This adapter deliberately does not change the public ``/context/build``
contract.  It proves the first V4 Context invariant in isolation: when the
Owner Truth projection is available, Context selection comes only from current
confirmed MemoryVersions and every selected item has a typed citation.  When
it is unavailable, the result explicitly falls back to a no-personal-memory
plan instead of reading legacy KBLite or Archive data.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionError
from app.domain.owner_truth.search_documents import (
    OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE,
    OwnerTruthMemorySearchReadError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_context_shadow import (
    OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
    OwnerTruthContextShadowReadService,
)
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionStore


OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION = "owner-truth-context-shadow-build-v1"
OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION = "owner-truth-context-shadow-build-policy-v1"
OWNER_TRUTH_CONTEXT_SHADOW_BUILD_VERSION = "echo-context-v4-shadow"
OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_CITATION_ORDER = "projectionCitationOrder"
OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK = (
    OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE
)

_FALLBACK_PROJECTION_UNAVAILABLE = "owner_truth_context_unavailable_no_personal_memory"
_FALLBACK_NO_ELIGIBLE_MEMORY = "owner_truth_context_no_eligible_personal_memory"
_FALLBACK_SEARCH_UNAVAILABLE = "owner_truth_context_search_unavailable_no_personal_memory"
_FALLBACK_QUERY_NO_MATCH = "owner_truth_context_no_query_match_no_personal_memory"


class OwnerTruthContextShadowBuildError(OwnerTruthMemoryProjectionError):
    """The QA-only Context shadow cannot build a safe selection plan."""


def _optional_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _selection_mode(payload: Mapping[str, Any]) -> str:
    selection_mode = _optional_text(payload.get("selectionMode"))
    if not selection_mode:
        return OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_CITATION_ORDER
    if selection_mode not in {
        OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_CITATION_ORDER,
        OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK,
    }:
        raise OwnerTruthContextShadowBuildError("context selectionMode is unsupported")
    return selection_mode


def _request_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    query = _optional_text(payload.get("query"))
    intent = _optional_text(payload.get("intent")) or "echo_chat"
    return {
        "intent": intent,
        "queryHash": sha256(query.encode("utf-8")).hexdigest() if query else None,
        "queryLength": len(query),
        "selectionMode": _selection_mode(payload),
    }


def _context_build_hash(
    *,
    request: Mapping[str, Any],
    authority: Mapping[str, Any],
    selected_context: list[dict[str, Any]],
    filtered_context: list[dict[str, Any]],
    fallbacks: list[str],
) -> str:
    """Bind the QA selection plan without retaining raw query or memory text."""

    payload = {
        "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION,
        "contextVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_VERSION,
        "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION,
        "request": dict(request),
        "authority": dict(authority),
        "selectedContext": selected_context,
        "filteredContext": filtered_context,
        "fallbacks": fallbacks,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _citation_proof(selected_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize the already-validated selected citation without memory content."""

    proof: list[dict[str, Any]] = []
    for item in selected_context:
        citation = item.get("citation")
        source_ref = item.get("sourceRef")
        if not isinstance(citation, Mapping) or not isinstance(source_ref, Mapping):
            raise OwnerTruthMemoryProjectionError("selected Context item lacks typed citation")
        proof.append(
            {
                "refId": str(item.get("refId") or ""),
                "source": str(item.get("source") or ""),
                "resolved": True,
                "resolution": "current_confirmed_projection_entry",
                "citation": deepcopy(dict(citation)),
                "sourceRef": deepcopy(dict(source_ref)),
            }
        )
    return proof


class OwnerTruthContextShadowBuildService:
    """Build a citation-only Context V4 shadow plan.

    It intentionally has no legacy store dependency.  The legacy public
    Context Packet may continue to run unchanged while this contract gathers
    policy and citation evidence behind the existing Owner Truth QA gate.
    """

    def __init__(self, store: OwnerTruthMemoryProjectionStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def build(
        self,
        *,
        context: OwnerTruthCommandContext,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if payload is not None and not isinstance(payload, Mapping):
            raise OwnerTruthMemoryProjectionError("context shadow payload must be an object")
        request_payload = payload or {}
        request = _request_summary(request_payload)
        selection_mode = str(request["selectionMode"])
        raw_query = _optional_text(request_payload.get("query"))
        shadow = OwnerTruthContextShadowReadService(
            self._store,
            enabled=self._enabled,
        ).read(context=context)
        selected_context = deepcopy(list(shadow.get("selectedContext") or []))
        filtered_context = deepcopy(list(shadow.get("filteredContext") or []))
        state = str(shadow.get("state") or "")

        fallbacks: list[str] = []
        if state != "ready":
            selected_context = []
            fallbacks.append(_FALLBACK_PROJECTION_UNAVAILABLE)
        elif selection_mode == OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK:
            if not raw_query:
                raise OwnerTruthContextShadowBuildError(
                    "query retrieval selection requires a nonblank query"
                )
            selected_context, filtered_context, fallbacks = self._query_ranked_context(
                context=context,
                query=raw_query,
                authority_epoch=shadow.get("authorityEpoch"),
                projection_checkpoint=shadow.get("projectionCheckpoint"),
                selected_context=selected_context,
                filtered_context=filtered_context,
            )
        elif not selected_context:
            fallbacks.append(_FALLBACK_NO_ELIGIBLE_MEMORY)

        ranking_trace = [
            {
                "refId": str(item.get("refId") or ""),
                "source": str(item.get("source") or ""),
                "selected": True,
                "reason": str(item.get("reason") or "confirmed_current_memory_version"),
                "rank": deepcopy(dict(item.get("rank") or {})),
            }
            for item in selected_context
        ]
        citation_proof = _citation_proof(selected_context)
        source_counts = (
            {OWNER_TRUTH_CONTEXT_SHADOW_SOURCE: len(selected_context)}
            if state == "ready"
            else {}
        )
        authority = {
            "source": OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
            "state": state,
            "vaultId": str(shadow.get("vaultId") or ""),
            "authorityEpoch": shadow.get("authorityEpoch"),
            "projectionCheckpoint": shadow.get("projectionCheckpoint"),
        }
        context_hash = _context_build_hash(
            request=request,
            authority=authority,
            selected_context=selected_context,
            filtered_context=filtered_context,
            fallbacks=fallbacks,
        )

        return {
            "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION,
            "contextVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_VERSION,
            "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION,
            "shadowOnly": True,
            "legacyContextUnchanged": True,
            "legacyContextRead": False,
            "contextHash": context_hash,
            "request": request,
            "authority": authority,
            "selectedContext": selected_context,
            "filteredContext": filtered_context,
            "rankingTrace": ranking_trace,
            "citationProof": citation_proof,
            "selectedContextSourceCounts": source_counts,
            "fallbacks": fallbacks,
            "trace": {
                "selectedContextCount": len(selected_context),
                "filteredContextCount": len(filtered_context),
                "rankingTraceCount": len(ranking_trace),
                "citationProofCount": len(citation_proof),
                "fallbackCount": len(fallbacks),
            },
        }

    def _query_ranked_context(
        self,
        *,
        context: OwnerTruthCommandContext,
        query: str,
        authority_epoch: Any,
        projection_checkpoint: Any,
        selected_context: list[dict[str, Any]],
        filtered_context: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """Use the current private SearchDocument projection without widening Context.

        The SearchDocument reader is a derived, owner-scoped index.  It can
        narrow the already policy-eligible Context set, but it cannot make an
        ineligible memory visible or revive a stale Projection checkpoint.
        """

        if not selected_context:
            return selected_context, filtered_context, [_FALLBACK_NO_ELIGIBLE_MEMORY]

        repository_factory = getattr(
            self._store,
            "owner_truth_memory_search_document_projection_repository",
            None,
        )
        if not callable(repository_factory):
            return self._unavailable_query_context(selected_context, filtered_context)

        try:
            search = OwnerTruthMemorySearchReadService(self._store).read(
                context=context,
                query=query,
            )
        except OwnerTruthMemorySearchReadError as error:
            raise OwnerTruthContextShadowBuildError(
                "query retrieval could not build a safe Context selection"
            ) from error

        query_plan = search.query_plan
        if (
            search.state != "ready"
            or query_plan is None
            or query_plan.authority_epoch != authority_epoch
            or query_plan.projection_checkpoint != projection_checkpoint
        ):
            return self._unavailable_query_context(selected_context, filtered_context)

        selected_by_version = {
            str(item.get("memoryVersionId") or ""): item for item in selected_context
        }
        if (
            not all(selected_by_version)
            or len(selected_by_version) != len(selected_context)
        ):
            raise OwnerTruthContextShadowBuildError(
                "Context selection contains invalid or duplicate MemoryVersion references"
            )

        ranked_selected: list[dict[str, Any]] = []
        selected_versions: set[str] = set()
        for hit in search.hits:
            memory_version_id = hit.document.memory_version_id
            item = selected_by_version.get(memory_version_id)
            if item is None:
                # A SearchDocument for a restricted or otherwise ineligible
                # MemoryVersion never overrides the Context policy filter.
                continue
            citation = item.get("citation")
            if (
                not isinstance(citation, Mapping)
                or str(item.get("memoryId") or "") != hit.document.memory_id
                or str(citation.get("contentHash") or "") != hit.document.content_hash
            ):
                raise OwnerTruthContextShadowBuildError(
                    "SearchDocument hit does not match the current Context projection"
                )
            ranked = deepcopy(item)
            ranked["reason"] = "confirmed_current_memory_version_query_match"
            ranked["rank"] = {
                "position": len(ranked_selected) + 1,
                "strategy": OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK,
            }
            ranked_selected.append(ranked)
            selected_versions.add(memory_version_id)

        query_filtered = deepcopy(filtered_context)
        for memory_version_id, item in selected_by_version.items():
            if memory_version_id in selected_versions:
                continue
            filtered = deepcopy(item)
            filtered.pop("rank", None)
            filtered["reason"] = "query_not_matched"
            query_filtered.append(filtered)

        fallbacks = [] if ranked_selected else [_FALLBACK_QUERY_NO_MATCH]
        return ranked_selected, query_filtered, fallbacks

    @staticmethod
    def _unavailable_query_context(
        selected_context: list[dict[str, Any]],
        filtered_context: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        unavailable_filtered = deepcopy(filtered_context)
        for item in selected_context:
            filtered = deepcopy(item)
            filtered.pop("rank", None)
            filtered["reason"] = "query_retrieval_unavailable"
            unavailable_filtered.append(filtered)
        return [], unavailable_filtered, [_FALLBACK_SEARCH_UNAVAILABLE]


def context_shadow_build_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return the QA-safe build evidence; raw query and memory content stay absent."""

    for field in ("selectedContext", "filteredContext", "rankingTrace", "citationProof"):
        if not isinstance(result.get(field), list):
            raise OwnerTruthMemoryProjectionError(f"context shadow build {field} must be a list")
    request = result.get("request")
    authority = result.get("authority")
    trace = result.get("trace")
    if not isinstance(request, Mapping) or not isinstance(authority, Mapping) or not isinstance(trace, Mapping):
        raise OwnerTruthMemoryProjectionError("context shadow build has invalid metadata")
    return {
        "schemaVersion": str(result.get("schemaVersion") or ""),
        "contextVersion": str(result.get("contextVersion") or ""),
        "policyVersion": str(result.get("policyVersion") or ""),
        "shadowOnly": bool(result.get("shadowOnly")),
        "legacyContextUnchanged": bool(result.get("legacyContextUnchanged")),
        "legacyContextRead": bool(result.get("legacyContextRead")),
        "contextHash": str(result.get("contextHash") or ""),
        "request": deepcopy(dict(request)),
        "authority": deepcopy(dict(authority)),
        "selectedContext": deepcopy(list(result["selectedContext"])),
        "filteredContext": deepcopy(list(result["filteredContext"])),
        "rankingTrace": deepcopy(list(result["rankingTrace"])),
        "citationProof": deepcopy(list(result["citationProof"])),
        "selectedContextSourceCounts": dict(result.get("selectedContextSourceCounts") or {}),
        "fallbacks": list(result.get("fallbacks") or []),
        "trace": deepcopy(dict(trace)),
    }


__all__ = [
    "OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION",
    "OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION",
    "OWNER_TRUTH_CONTEXT_SHADOW_BUILD_VERSION",
    "OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_CITATION_ORDER",
    "OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK",
    "OwnerTruthContextShadowBuildError",
    "OwnerTruthContextShadowBuildService",
    "context_shadow_build_summary",
]

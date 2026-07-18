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
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_context_shadow import (
    OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
    OwnerTruthContextShadowReadService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionStore


OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION = "owner-truth-context-shadow-build-v1"
OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION = "owner-truth-context-shadow-build-policy-v1"
OWNER_TRUTH_CONTEXT_SHADOW_BUILD_VERSION = "echo-context-v4-shadow"

_FALLBACK_PROJECTION_UNAVAILABLE = "owner_truth_context_unavailable_no_personal_memory"
_FALLBACK_NO_ELIGIBLE_MEMORY = "owner_truth_context_no_eligible_personal_memory"


def _optional_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _request_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    query = _optional_text(payload.get("query"))
    intent = _optional_text(payload.get("intent")) or "echo_chat"
    return {
        "intent": intent,
        "queryHash": sha256(query.encode("utf-8")).hexdigest() if query else None,
        "queryLength": len(query),
    }


def _fallback_context_hash(
    *,
    request: Mapping[str, Any],
    shadow: Mapping[str, Any],
) -> str:
    """Bind an explicit no-personal-memory plan without retaining raw text.

    A ready projection already publishes its stable Context hash.  A stale or
    rebuilding projection still needs a deterministic evidence key so a later
    Answer/Citation receipt can prove that it intentionally used the normal
    no-personal-memory fallback rather than silently reading legacy data.
    """

    payload = {
        "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION,
        "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION,
        "request": dict(request),
        "state": str(shadow.get("state") or ""),
        "vaultId": str(shadow.get("vaultId") or ""),
        "authorityEpoch": shadow.get("authorityEpoch"),
        "projectionCheckpoint": shadow.get("projectionCheckpoint"),
        "selectedContext": list(shadow.get("selectedContext") or []),
        "filteredContext": list(shadow.get("filteredContext") or []),
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
        request = _request_summary(payload or {})
        shadow = OwnerTruthContextShadowReadService(
            self._store,
            enabled=self._enabled,
        ).read(context=context)
        selected_context = deepcopy(list(shadow.get("selectedContext") or []))
        filtered_context = deepcopy(list(shadow.get("filteredContext") or []))
        state = str(shadow.get("state") or "")
        context_hash = str(shadow.get("contextHash") or "").strip()
        if not context_hash:
            context_hash = _fallback_context_hash(request=request, shadow=shadow)

        fallbacks: list[str] = []
        if state != "ready":
            fallbacks.append(_FALLBACK_PROJECTION_UNAVAILABLE)
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
        source_counts = dict(shadow.get("selectedContextSourceCounts") or {})

        return {
            "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_SCHEMA_VERSION,
            "contextVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_VERSION,
            "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_BUILD_POLICY_VERSION,
            "shadowOnly": True,
            "legacyContextUnchanged": True,
            "legacyContextRead": False,
            "contextHash": context_hash,
            "request": request,
            "authority": {
                "source": OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
                "state": state,
                "vaultId": str(shadow.get("vaultId") or ""),
                "authorityEpoch": shadow.get("authorityEpoch"),
                "projectionCheckpoint": shadow.get("projectionCheckpoint"),
            },
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
    "OwnerTruthContextShadowBuildService",
    "context_shadow_build_summary",
]

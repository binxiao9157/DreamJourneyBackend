"""Server-owned Owner Truth Context authority for the authenticated Echo path.

The existing Context V4 shadow/materialization services prove that a current,
confirmed MemoryProjection can be selected safely. This adapter is the small
production bridge for a signed-in Owner: it creates an empty V4 Context
when the Projection is absent or rebuilding and never falls back to legacy
Archive, KBLite, or Care reads.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from app.core.config import Settings
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.context_packet import ContextPacketBuilder
from app.services.owner_truth_context_materialization import (
    OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS,
    OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION,
    OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION,
    OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
    OwnerTruthContextMaterializationService,
)
from app.services.owner_truth_context_shadow import OWNER_TRUTH_CONTEXT_SHADOW_SOURCE
from app.services.owner_truth_context_shadow_build import (
    OWNER_TRUTH_CONTEXT_QUERY_CANDIDATE_LIMIT,
    OWNER_TRUTH_CONTEXT_QUERY_SELECTED_LIMIT,
    OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK,
)


OWNER_TRUTH_CONTEXT_AUTHORITY_MODE = "ownerTruthConfirmedProjection"
OWNER_TRUTH_CONTEXT_AUTHORITY_VERSION = "echo-context-v4-owner"
OWNER_TRUTH_CONTEXT_AUTHORITY_SCHEMA_VERSION = "owner-truth-context-authority-v1"
OWNER_TRUTH_CONTEXT_AUTHORITY_COHORT = "authenticatedOwner"
OWNER_TRUTH_CONTEXT_FALLBACK_POLICY = "failClosedNoLegacy"
_FALLBACK_PROJECTION_UNAVAILABLE = "owner_truth_context_unavailable_no_personal_memory"


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _request_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    query = _text(payload.get("query"))
    return {
        "intent": _text(payload.get("intent"), "echo_chat"),
        "queryHash": sha256(query.encode("utf-8")).hexdigest() if query else None,
        "queryLength": len(query),
        "selectionMode": OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK,
    }


class OwnerTruthContextAuthorityService:
    """Build a normal Echo packet from current confirmed Projection only."""

    def __init__(self, store: Any, *, settings: Settings, enabled: bool = False) -> None:
        self._store = store
        self._settings = settings
        self._enabled = bool(enabled)

    def build_packet(
        self,
        *,
        context: OwnerTruthCommandContext,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Preserve the neutral safety boundary before reading projection data."""

        canonical_payload = dict(payload)
        builder = ContextPacketBuilder(self._store, self._settings)
        query = _text(canonical_payload.get("query"))
        if not builder.safety_allows_persona(query):
            # ``build`` exits through its existing neutral packet before legacy
            # Archive/KBLite/Care or provider reads are attempted.
            return builder.build(canonical_payload)
        return builder.build_from_owner_truth_materialization(
            canonical_payload,
            materialization=self.materialize(context=context, payload=canonical_payload),
        )

    def materialize(
        self,
        *,
        context: OwnerTruthCommandContext,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        vault_reader = getattr(self._store, "get_owner_truth_vault", None)
        vault = vault_reader(context.vault_id) if callable(vault_reader) else None
        if not isinstance(vault, Mapping):
            return self._empty_materialization(context=context, payload=payload)
        if str(vault.get("ownerSubjectId") or "") != context.owner_subject_id:
            raise OwnerTruthMemoryProjectionAccessDenied("Owner Truth Vault owner does not match context")
        if str(vault.get("status") or "active") != "active":
            return self._empty_materialization(context=context, payload=payload)
        # Context selection is server-defined. The client cannot switch this
        # production path back to citation order by submitting selectionMode.
        materialization_payload = {
            "intent": _text(payload.get("intent"), "echo_chat"),
            "query": _text(payload.get("query")),
            "selectionMode": OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK,
        }
        materialization = OwnerTruthContextMaterializationService(
            self._store,
            enabled=self._enabled,
        ).build(
            context=context,
            payload=materialization_payload,
        )
        return self._bind_authority_contract(materialization=materialization, context=context)

    @staticmethod
    def _bind_authority_contract(
        *,
        materialization: Mapping[str, Any],
        context: OwnerTruthCommandContext,
    ) -> dict[str, Any]:
        bound = deepcopy(dict(materialization))
        authority = deepcopy(dict(bound.get("authority") or {}))
        retrieval = bound.get("retrieval")
        if not isinstance(retrieval, Mapping):
            raise OwnerTruthMemoryProjectionError(
                "Owner Truth Context retrieval evidence is missing"
            )
        generation_material = {
            "authorityEpoch": authority.get("authorityEpoch"),
            "materializationHash": bound.get("materializationHash"),
            "ownerSubjectId": context.owner_subject_id,
            "projectionCheckpoint": authority.get("projectionCheckpoint"),
            "vaultId": context.vault_id,
        }
        authority.update(
            {
                "schemaVersion": OWNER_TRUTH_CONTEXT_AUTHORITY_SCHEMA_VERSION,
                "mode": OWNER_TRUTH_CONTEXT_AUTHORITY_MODE,
                "cohort": OWNER_TRUTH_CONTEXT_AUTHORITY_COHORT,
                "fallbackPolicy": OWNER_TRUTH_CONTEXT_FALLBACK_POLICY,
                "mixedAuthorityAllowed": False,
                "retrievalMode": str(retrieval.get("mode") or ""),
                "retrievalOutcome": str(retrieval.get("outcome") or "fallback"),
                "candidateLimit": OWNER_TRUTH_CONTEXT_QUERY_CANDIDATE_LIMIT,
                "selectedLimit": OWNER_TRUTH_CONTEXT_QUERY_SELECTED_LIMIT,
                "retrievalFallbackReason": retrieval.get("fallbackReason"),
                "authorityGeneration": sha256(
                    json.dumps(
                        generation_material,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
        bound["authority"] = authority
        return bound

    @staticmethod
    def _empty_materialization(
        *,
        context: OwnerTruthCommandContext,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = _request_summary(payload)
        authority = {
            "source": OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
            "state": "unavailable",
            "vaultId": context.vault_id,
            "authorityEpoch": None,
            "projectionCheckpoint": None,
        }
        generation_text = ""
        generation_context = {
            "version": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
            "text": generation_text,
            "contentHash": "sha256:" + sha256(generation_text.encode("utf-8")).hexdigest(),
            "sourceCount": 0,
            "maxChars": OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS,
            "truncated": False,
        }
        fallbacks = [_FALLBACK_PROJECTION_UNAVAILABLE]
        retrieval = {
            "mode": OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_QUERY_TEXT_FALLBACK,
            "outcome": "fallback",
            "candidateLimit": OWNER_TRUTH_CONTEXT_QUERY_CANDIDATE_LIMIT,
            "selectedLimit": OWNER_TRUTH_CONTEXT_QUERY_SELECTED_LIMIT,
            "candidateCount": 0,
            "selectedCount": 0,
            "latencyMs": 0,
            "latencyBudgetMs": 300,
            "latencyBudgetMet": True,
            "fallbackReason": _FALLBACK_PROJECTION_UNAVAILABLE,
        }
        materialization_hash = sha256(
            json.dumps(
                {
                    "schemaVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION,
                    "contextVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
                    "policyVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION,
                    "request": request,
                    "authority": authority,
                    "fallbacks": fallbacks,
                    "generationContextContentHash": generation_context["contentHash"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return OwnerTruthContextAuthorityService._bind_authority_contract(
            materialization={
            "schemaVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION,
            "contextVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
            "policyVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION,
            "state": "unavailable",
            "shadowOnly": False,
            "legacyContextUnchanged": False,
            "legacyContextRead": False,
            "contextHash": None,
            "materializationHash": materialization_hash,
            "request": deepcopy(request),
            "authority": deepcopy(authority),
            "selectedContext": [],
            "filteredContext": [],
            "typedCitations": [],
            "generationContext": generation_context,
            "retrieval": retrieval,
            "fallbacks": fallbacks,
            "trace": {
                "selectedContextCount": 0,
                "filteredContextCount": 0,
                "typedCitationCount": 0,
                "generationContextSourceCount": 0,
                "generationContextLength": 0,
                "generationContextTruncated": False,
                "fallbackCount": len(fallbacks),
                "retrievalMode": retrieval["mode"],
                "retrievalOutcome": retrieval["outcome"],
                "retrievalCandidateCount": 0,
                "retrievalSelectedCount": 0,
                "retrievalLatencyMs": 0,
                "retrievalLatencyBudgetMet": True,
            },
            },
            context=context,
        )


__all__ = [
    "OWNER_TRUTH_CONTEXT_AUTHORITY_MODE",
    "OWNER_TRUTH_CONTEXT_AUTHORITY_SCHEMA_VERSION",
    "OWNER_TRUTH_CONTEXT_AUTHORITY_COHORT",
    "OWNER_TRUTH_CONTEXT_FALLBACK_POLICY",
    "OWNER_TRUTH_CONTEXT_AUTHORITY_VERSION",
    "OwnerTruthContextAuthorityService",
]

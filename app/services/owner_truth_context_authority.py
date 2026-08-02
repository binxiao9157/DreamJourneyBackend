"""Server-owned Owner Truth Context authority for the closed-pilot Echo path.

The existing Context V4 shadow/materialization services prove that a current,
confirmed MemoryProjection can be selected safely. This adapter is the small
production bridge for the server-granted pilot: it creates an empty V4 Context
when the Projection is absent or rebuilding and never falls back to legacy
Archive, KBLite, or Care reads.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from app.core.config import Settings
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
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
    OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_CITATION_ORDER,
)


OWNER_TRUTH_CONTEXT_AUTHORITY_MODE = "ownerTruthConfirmedProjection"
OWNER_TRUTH_CONTEXT_AUTHORITY_VERSION = "echo-context-v4-owner"
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
        "selectionMode": OWNER_TRUTH_CONTEXT_SHADOW_SELECTION_MODE_CITATION_ORDER,
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
        # production path to a QA retrieval mode by submitting selectionMode.
        materialization_payload = {
            "intent": _text(payload.get("intent"), "echo_chat"),
            "query": _text(payload.get("query")),
        }
        return OwnerTruthContextMaterializationService(
            self._store,
            enabled=self._enabled,
        ).build(
            context=context,
            payload=materialization_payload,
        )

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
        return {
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
            "fallbacks": fallbacks,
            "trace": {
                "selectedContextCount": 0,
                "filteredContextCount": 0,
                "typedCitationCount": 0,
                "generationContextSourceCount": 0,
                "generationContextLength": 0,
                "generationContextTruncated": False,
                "fallbackCount": len(fallbacks),
            },
        }


__all__ = [
    "OWNER_TRUTH_CONTEXT_AUTHORITY_MODE",
    "OWNER_TRUTH_CONTEXT_AUTHORITY_VERSION",
    "OwnerTruthContextAuthorityService",
]

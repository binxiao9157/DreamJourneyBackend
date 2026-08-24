"""Default-off, citation-only Context shadow over Owner Truth projections.

This module deliberately sits beside the legacy ``/context/build`` path.  It
does not read legacy KBLite, does not assemble generation text, and does not
change the public Echo response.  Its only purpose in this slice is to prove
that a future Context reader can select current confirmed MemoryVersions with
typed citations while failing closed for a missing or stale projection.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION,
    OWNER_TRUTH_SCHEMA_VERSION_V2,
    OWNER_TRUTH_SCHEMA_VERSION_V3,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import (
    OwnerTruthMemoryProjectionService,
    OwnerTruthMemoryProjectionStore,
)


OWNER_TRUTH_CONTEXT_SHADOW_SCHEMA_VERSION = "owner-truth-context-shadow-v1"
OWNER_TRUTH_CONTEXT_SHADOW_SOURCE = "owner-truth-memory-projection"
OWNER_TRUTH_CONTEXT_SHADOW_POLICY_VERSION = "owner-truth-context-shadow-policy-v1"

# The projection may retain Owner-confirmed records with inferred metadata for
# review and audit. That does not make them admissible Context evidence: a
# reply path must not present an AI-only inference as the Owner's memory.
# Keeping this fence at the Shadow boundary makes read, build, materialization,
# and interview-turn preparation share the same decision.
_CONTEXT_ELIGIBLE_PERSPECTIVE_TYPES = frozenset({"firstPerson", "reported"})
_CONTEXT_ELIGIBLE_EPISTEMIC_STATUSES = frozenset(
    {"observed", "recalled", "reported", "uncertain"}
)
_CONTEXT_SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {
        OWNER_TRUTH_SCHEMA_VERSION,
        OWNER_TRUTH_SCHEMA_VERSION_V2,
        OWNER_TRUTH_SCHEMA_VERSION_V3,
    }
)


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthMemoryProjectionError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthMemoryProjectionAccessDenied(
            "only the Vault Owner may read an Owner Truth Context shadow"
        )


def _nonblank_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OwnerTruthMemoryProjectionError(f"{field} must be positive")
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthMemoryProjectionError(f"{field} must be non-negative")
    return value


def _citation_from_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    raw_citation = entry.get("citation")
    if not isinstance(raw_citation, Mapping):
        raise OwnerTruthMemoryProjectionError("projection entry citation must be an object")

    citation: dict[str, Any] = {
        "memoryVersion": _positive_int(entry.get("memoryVersion"), field="memoryVersion"),
    }
    for field in ("memoryId", "memoryVersionId", "sourceId", "contentHash"):
        value = _nonblank_text(raw_citation.get(field))
        if value is None:
            raise OwnerTruthMemoryProjectionError(
                f"projection entry citation {field} must be nonblank"
            )
        citation[field] = value
    citation["sourceVersion"] = _positive_int(
        raw_citation.get("sourceVersion"),
        field="sourceVersion",
    )
    return citation


def _context_hash(
    *,
    authority_epoch: int,
    checkpoint: str,
    selected_context: list[dict[str, Any]],
    filtered_context: list[dict[str, Any]],
) -> str:
    payload = {
        "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_SCHEMA_VERSION,
        "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_POLICY_VERSION,
        "authorityEpoch": authority_epoch,
        "projectionCheckpoint": checkpoint,
        "selectedContext": selected_context,
        "filteredContext": filtered_context,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


class OwnerTruthContextShadowReadService:
    """Build a default-off, owner-only Context selection plan with citations.

    Until persona/sensitivity policy is promoted, only standard, non-inferred
    current memories are selected. Sensitive, restricted, and AI-only records
    remain value-free filter evidence instead of being silently injected into
    Echo.
    """

    def __init__(self, store: OwnerTruthMemoryProjectionStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
        _assert_owner_context(context)
        if not self._enabled:
            return self._base_result(context=context, state="disabled")

        projection = OwnerTruthMemoryProjectionService(self._store).read(context=context)
        if str(projection.get("state") or "") != "ready":
            authority_epoch = projection.get("authorityEpoch")
            if isinstance(authority_epoch, bool) or not isinstance(authority_epoch, int):
                authority_epoch = None
            return self._base_result(
                context=context,
                state="rebuilding",
                authority_epoch=authority_epoch,
            )
        return self._ready_result(context=context, projection=projection)

    @staticmethod
    def _base_result(
        *,
        context: OwnerTruthCommandContext,
        state: str,
        authority_epoch: int | None = None,
        projection_checkpoint: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_CONTEXT_SHADOW_SCHEMA_VERSION,
            "source": OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
            "policyVersion": OWNER_TRUTH_CONTEXT_SHADOW_POLICY_VERSION,
            "state": state,
            "shadowOnly": True,
            "legacyContextUnchanged": True,
            "vaultId": context.vault_id,
            "authorityEpoch": authority_epoch,
            "projectionCheckpoint": projection_checkpoint,
            "selectedContext": [],
            "filteredContext": [],
            "selectedContextSourceCounts": {},
            "contextHash": None,
        }

    def _ready_result(
        self,
        *,
        context: OwnerTruthCommandContext,
        projection: Mapping[str, Any],
    ) -> dict[str, Any]:
        authority_epoch = _nonnegative_int(
            projection.get("authorityEpoch"),
            field="authorityEpoch",
        )
        checkpoint = _nonblank_text(projection.get("checkpoint"))
        entries = projection.get("entries")
        if checkpoint is None:
            raise OwnerTruthMemoryProjectionError("ready projection checkpoint must be nonblank")
        if not isinstance(entries, list):
            raise OwnerTruthMemoryProjectionError("ready projection entries must be a list")

        result = self._base_result(
            context=context,
            state="ready",
            authority_epoch=authority_epoch,
            projection_checkpoint=checkpoint,
        )
        selected_context: list[dict[str, Any]] = []
        filtered_context: list[dict[str, Any]] = []
        typed_entries: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise OwnerTruthMemoryProjectionError("projection entry must be an object")
            citation = _citation_from_entry(entry)
            typed_entries.append((citation, entry))

        # Projection storage ordering must never become an implicit ranking
        # authority.  The shadow uses a deterministic citation order only.
        for citation, entry in sorted(
            typed_entries,
            key=lambda item: str(item[0]["memoryVersionId"]),
        ):
            citation["vaultId"] = context.vault_id
            memory_kind = _nonblank_text(entry.get("memoryKind"))
            perspective_type = _nonblank_text(entry.get("perspectiveType"))
            epistemic_status = _nonblank_text(entry.get("epistemicStatus"))
            sensitivity = _nonblank_text(entry.get("sensitivity"))
            visibility = _nonblank_text(entry.get("visibility"))
            content_schema_version = _nonblank_text(entry.get("contentSchemaVersion"))
            if memory_kind not in {"experience", "knowledge", "emotion"}:
                reason = "memory_kind_not_supported"
            elif perspective_type is None or epistemic_status is None:
                reason = "memory_perspective_or_epistemic_status_missing"
            elif visibility != "owner":
                reason = "visibility_not_owner"
            elif content_schema_version not in _CONTEXT_SUPPORTED_SCHEMA_VERSIONS:
                reason = "content_schema_not_supported"
            elif sensitivity != "standard":
                reason = "sensitivity_not_context_eligible"
            elif perspective_type not in _CONTEXT_ELIGIBLE_PERSPECTIVE_TYPES:
                reason = "ai_only_perspective_not_context_eligible"
            elif epistemic_status not in _CONTEXT_ELIGIBLE_EPISTEMIC_STATUSES:
                reason = "ai_only_epistemic_status_not_context_eligible"
            else:
                position = len(selected_context) + 1
                selected_context.append(
                    {
                        "source": OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
                        "refId": f"memory-version:{citation['memoryVersionId']}",
                        "memoryId": citation["memoryId"],
                        "memoryVersionId": citation["memoryVersionId"],
                        "memoryVersion": citation["memoryVersion"],
                        "memoryKind": memory_kind,
                        "perspectiveType": perspective_type,
                        "epistemicStatus": epistemic_status,
                        "sensitivity": sensitivity,
                        "visibility": visibility,
                        "sourceRef": {
                            "vaultId": context.vault_id,
                            "sourceId": citation["sourceId"],
                            "sourceVersion": citation["sourceVersion"],
                        },
                        "citation": deepcopy(citation),
                        "reason": "confirmed_current_memory_version",
                        # This is trace ordering, not a relevance score.  A
                        # future query ranker must replace the strategy before
                        # this shadow becomes a production Context source.
                        "rank": {
                            "position": position,
                            "strategy": "projectionCitationOrder",
                        },
                    }
                )
                continue

            filtered_context.append(
                {
                    "source": OWNER_TRUTH_CONTEXT_SHADOW_SOURCE,
                    "refId": f"memory-version:{citation['memoryVersionId']}",
                    "memoryId": citation["memoryId"],
                    "memoryVersionId": citation["memoryVersionId"],
                    "memoryKind": memory_kind or "unknown",
                    "sensitivity": sensitivity or "unknown",
                    "sourceRef": {
                        "vaultId": context.vault_id,
                        "sourceId": citation["sourceId"],
                        "sourceVersion": citation["sourceVersion"],
                    },
                    "citation": deepcopy(citation),
                    "reason": reason,
                }
            )

        result["selectedContext"] = selected_context
        result["filteredContext"] = filtered_context
        result["selectedContextSourceCounts"] = {
            OWNER_TRUTH_CONTEXT_SHADOW_SOURCE: len(selected_context)
        }
        result["contextHash"] = _context_hash(
            authority_epoch=authority_epoch,
            checkpoint=checkpoint,
            selected_context=selected_context,
            filtered_context=filtered_context,
        )
        return result


def context_shadow_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a QA-safe summary that contains citations, never memory content."""

    selected_context = result.get("selectedContext")
    filtered_context = result.get("filteredContext")
    source_counts = result.get("selectedContextSourceCounts")
    if not isinstance(selected_context, list) or not isinstance(filtered_context, list):
        raise OwnerTruthMemoryProjectionError("context shadow result has invalid context lists")
    if not isinstance(source_counts, Mapping):
        raise OwnerTruthMemoryProjectionError("context shadow result has invalid source counts")
    return {
        "schemaVersion": str(result.get("schemaVersion") or ""),
        "source": str(result.get("source") or ""),
        "policyVersion": str(result.get("policyVersion") or ""),
        "state": str(result.get("state") or ""),
        "shadowOnly": bool(result.get("shadowOnly")),
        "legacyContextUnchanged": bool(result.get("legacyContextUnchanged")),
        "vaultId": str(result.get("vaultId") or ""),
        "authorityEpoch": result.get("authorityEpoch"),
        "projectionCheckpoint": result.get("projectionCheckpoint"),
        "selectedContext": deepcopy(selected_context),
        "filteredContext": deepcopy(filtered_context),
        "selectedContextSourceCounts": dict(source_counts),
        "contextHash": result.get("contextHash"),
    }


__all__ = [
    "OWNER_TRUTH_CONTEXT_SHADOW_SCHEMA_VERSION",
    "OWNER_TRUTH_CONTEXT_SHADOW_SOURCE",
    "OWNER_TRUTH_CONTEXT_SHADOW_POLICY_VERSION",
    "OwnerTruthContextShadowReadService",
    "context_shadow_summary",
]

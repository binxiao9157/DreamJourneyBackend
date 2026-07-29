"""Default-off materialization of confirmed Owner Truth memory Context.

The existing Context V4 shadow intentionally proves selection and typed
citations without retaining memory values.  This service is the next private
boundary: it turns that selected, current Projection set into bounded model
input while keeping the value-bearing text in-process.  Its QA summary is
strictly value-free and the legacy ``/context/build`` route remains unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionError
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_context_shadow_build import OwnerTruthContextShadowBuildService
from app.services.owner_truth_memory_projection import (
    OwnerTruthMemoryProjectionService,
    OwnerTruthMemoryProjectionStore,
)


OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION = "owner-truth-context-materialization-v1"
OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION = "echo-context-v4-owner-qa"
OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION = "owner-truth-context-materialization-policy-v1"
OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS = 4096

_FALLBACK_PROJECTION_CHANGED = "owner_truth_context_projection_changed_no_personal_memory"
_CONTENT_FIELD_BY_KIND = {
    "experience": "summary",
    "knowledge": "claim",
    "emotion": "label",
}


class OwnerTruthContextMaterializationError(OwnerTruthMemoryProjectionError):
    """A selected Owner Truth Context cannot be safely materialized."""


def _nonblank_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerTruthContextMaterializationError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized:
        raise OwnerTruthContextMaterializationError(f"{field} must be nonblank")
    return normalized


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthContextMaterializationError(f"{field} must be non-negative")
    return value


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _empty_generation_context() -> dict[str, Any]:
    text = ""
    return {
        "version": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
        "text": text,
        "contentHash": "sha256:" + sha256(text.encode("utf-8")).hexdigest(),
        "sourceCount": 0,
        "maxChars": OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS,
        "truncated": False,
    }


class OwnerTruthContextMaterializationService:
    """Build bounded, in-process Context only from current confirmed Projection.

    The resulting ``generationContext.text`` is deliberately not suitable for
    logs, trace export or a public QA response.  A future server-side
    conversation adapter may consume it directly.  Until then this service is
    available only through a value-free QA summary and unit-contract tests.
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
        shadow = OwnerTruthContextShadowBuildService(self._store, enabled=self._enabled).build(
            context=context,
            payload=payload,
        )
        authority = shadow.get("authority")
        if not isinstance(authority, Mapping):
            raise OwnerTruthContextMaterializationError("Context shadow authority is invalid")

        state = str(authority.get("state") or "")
        selected_context = deepcopy(list(shadow.get("selectedContext") or []))
        filtered_context = deepcopy(list(shadow.get("filteredContext") or []))
        fallbacks = list(shadow.get("fallbacks") or [])
        generation_context = _empty_generation_context()
        typed_citations: list[dict[str, Any]] = []

        if state == "ready":
            projection = OwnerTruthMemoryProjectionService(self._store).read(context=context)
            if not self._projection_matches_authority(projection=projection, authority=authority):
                state = "rebuilding"
                selected_context = []
                typed_citations = []
                generation_context = _empty_generation_context()
                fallbacks = [_FALLBACK_PROJECTION_CHANGED]
            else:
                typed_citations, generation_context = self._materialize_selected_context(
                    projection=projection,
                    selected_context=selected_context,
                )
        else:
            selected_context = []

        materialization_hash = _digest(
            {
                "schemaVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION,
                "contextVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
                "policyVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION,
                "contextHash": str(shadow.get("contextHash") or ""),
                "authority": dict(authority),
                "state": state,
                "typedCitations": typed_citations,
                "generationContextContentHash": generation_context["contentHash"],
                "fallbacks": fallbacks,
            }
        )
        return {
            "schemaVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION,
            "contextVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
            "policyVersion": OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION,
            "state": state,
            "shadowOnly": True,
            "legacyContextUnchanged": True,
            "legacyContextRead": False,
            "contextHash": str(shadow.get("contextHash") or ""),
            "materializationHash": materialization_hash,
            "request": deepcopy(dict(shadow.get("request") or {})),
            "authority": deepcopy(dict(authority)),
            "selectedContext": selected_context,
            "filteredContext": filtered_context,
            "typedCitations": typed_citations,
            "generationContext": generation_context,
            "fallbacks": fallbacks,
            "trace": {
                "selectedContextCount": len(selected_context),
                "filteredContextCount": len(filtered_context),
                "typedCitationCount": len(typed_citations),
                "generationContextSourceCount": generation_context["sourceCount"],
                "generationContextLength": len(generation_context["text"]),
                "generationContextTruncated": generation_context["truncated"],
                "fallbackCount": len(fallbacks),
            },
        }

    @staticmethod
    def _projection_matches_authority(
        *,
        projection: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> bool:
        if str(projection.get("state") or "") != "ready":
            return False
        if str(projection.get("vaultId") or "") != str(authority.get("vaultId") or ""):
            return False
        if projection.get("authorityEpoch") != authority.get("authorityEpoch"):
            return False
        return str(projection.get("checkpoint") or "") == str(
            authority.get("projectionCheckpoint") or ""
        )

    def _materialize_selected_context(
        self,
        *,
        projection: Mapping[str, Any],
        selected_context: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        entries = projection.get("entries")
        if not isinstance(entries, list):
            raise OwnerTruthContextMaterializationError("ready Projection entries must be a list")
        entries_by_version: dict[str, Mapping[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise OwnerTruthContextMaterializationError("Projection entry must be an object")
            citation = entry.get("citation")
            if not isinstance(citation, Mapping):
                raise OwnerTruthContextMaterializationError("Projection entry citation must be an object")
            version_id = _nonblank_text(citation.get("memoryVersionId"), field="projection memoryVersionId")
            if version_id in entries_by_version:
                raise OwnerTruthContextMaterializationError("Projection contains duplicate MemoryVersion")
            entries_by_version[version_id] = entry

        typed_citations: list[dict[str, Any]] = []
        rendered_entries: list[tuple[dict[str, Any], str]] = []
        for selected in selected_context:
            citation, entry = self._resolve_selected_entry(
                selected=selected,
                entries_by_version=entries_by_version,
            )
            typed_citations.append(citation)
            rendered_entries.append((citation, self._render_entry(entry=entry, citation=citation)))

        text, source_count, truncated = self._bounded_generation_text(rendered_entries)
        return typed_citations, {
            "version": OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION,
            "text": text,
            "contentHash": "sha256:" + sha256(text.encode("utf-8")).hexdigest(),
            "sourceCount": source_count,
            "maxChars": OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS,
            "truncated": truncated,
        }

    @staticmethod
    def _resolve_selected_entry(
        *,
        selected: Mapping[str, Any],
        entries_by_version: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, Any], Mapping[str, Any]]:
        citation = selected.get("citation")
        source_ref = selected.get("sourceRef")
        if not isinstance(citation, Mapping) or not isinstance(source_ref, Mapping):
            raise OwnerTruthContextMaterializationError("selected Context item lacks typed citation")
        version_id = _nonblank_text(citation.get("memoryVersionId"), field="selected memoryVersionId")
        entry = entries_by_version.get(version_id)
        if entry is None:
            raise OwnerTruthContextMaterializationError("selected MemoryVersion is not in current Projection")
        entry_citation = entry.get("citation")
        if not isinstance(entry_citation, Mapping):
            raise OwnerTruthContextMaterializationError("resolved Projection citation is invalid")

        fields = ("memoryId", "memoryVersionId", "sourceId", "contentHash")
        for field in fields:
            if _nonblank_text(citation.get(field), field=f"selected citation {field}") != _nonblank_text(
                entry_citation.get(field), field=f"projection citation {field}"
            ):
                raise OwnerTruthContextMaterializationError("selected citation no longer matches current Projection")
        selected_version = _nonnegative_int(citation.get("memoryVersion"), field="selected memoryVersion")
        if selected_version < 1 or selected_version != _nonnegative_int(
            entry.get("memoryVersion"), field="projection memoryVersion"
        ):
            raise OwnerTruthContextMaterializationError("selected MemoryVersion number no longer matches Projection")
        if _nonblank_text(source_ref.get("sourceId"), field="selected sourceRef sourceId") != _nonblank_text(
            citation.get("sourceId"), field="selected citation sourceId"
        ):
            raise OwnerTruthContextMaterializationError("selected sourceRef does not match citation")
        if _nonblank_text(source_ref.get("vaultId"), field="selected sourceRef vaultId") != _nonblank_text(
            citation.get("vaultId"), field="selected citation vaultId"
        ):
            raise OwnerTruthContextMaterializationError("selected sourceRef Vault does not match citation")
        if _nonnegative_int(source_ref.get("sourceVersion"), field="selected sourceRef sourceVersion") < 1:
            raise OwnerTruthContextMaterializationError("selected sourceRef version is invalid")
        if _nonnegative_int(source_ref.get("sourceVersion"), field="selected sourceRef sourceVersion") != _nonnegative_int(
            entry_citation.get("sourceVersion"), field="projection citation sourceVersion"):
            raise OwnerTruthContextMaterializationError("selected sourceRef version no longer matches Projection")

        return {
            "vaultId": _nonblank_text(citation.get("vaultId"), field="selected citation vaultId"),
            "memoryId": _nonblank_text(citation.get("memoryId"), field="selected citation memoryId"),
            "memoryVersionId": version_id,
            "memoryVersion": selected_version,
            "sourceId": _nonblank_text(citation.get("sourceId"), field="selected citation sourceId"),
            "sourceVersion": _nonnegative_int(citation.get("sourceVersion"), field="selected citation sourceVersion"),
            "contentHash": _nonblank_text(citation.get("contentHash"), field="selected citation contentHash"),
        }, entry

    @staticmethod
    def _render_entry(*, entry: Mapping[str, Any], citation: Mapping[str, Any]) -> str:
        if str(entry.get("contentSchemaVersion") or "") != OWNER_TRUTH_SCHEMA_VERSION:
            raise OwnerTruthContextMaterializationError("Projection content schema is not supported")
        memory_kind = _nonblank_text(entry.get("memoryKind"), field="Projection memoryKind")
        content_field = _CONTENT_FIELD_BY_KIND.get(memory_kind)
        if content_field is None:
            raise OwnerTruthContextMaterializationError("Projection memory kind is not supported")
        content = entry.get("content")
        if not isinstance(content, Mapping):
            raise OwnerTruthContextMaterializationError("Projection entry content must be an object")
        value = _nonblank_text(content.get(content_field), field=f"Projection content {content_field}")
        return (
            f"[confirmed-memory:{citation['memoryVersionId']}|{memory_kind}]\n"
            f"{value}"
        )

    @staticmethod
    def _bounded_generation_text(
        rendered_entries: list[tuple[dict[str, Any], str]],
    ) -> tuple[str, int, bool]:
        lines: list[str] = []
        source_count = 0
        truncated = False
        for _citation, line in rendered_entries:
            candidate = "\n\n".join([*lines, line])
            if len(candidate) > OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS:
                truncated = True
                break
            lines.append(line)
            source_count += 1
        return "\n\n".join(lines), source_count, truncated


def context_materialization_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return QA-safe metadata without raw query or confirmed memory content."""

    if not isinstance(result, Mapping):
        raise OwnerTruthContextMaterializationError("materialization result must be an object")
    for field in ("selectedContext", "filteredContext", "typedCitations", "fallbacks"):
        if not isinstance(result.get(field), list):
            raise OwnerTruthContextMaterializationError(f"materialization {field} must be a list")
    request = result.get("request")
    authority = result.get("authority")
    generation_context = result.get("generationContext")
    trace = result.get("trace")
    if not all(isinstance(value, Mapping) for value in (request, authority, generation_context, trace)):
        raise OwnerTruthContextMaterializationError("materialization metadata is invalid")
    return {
        "schemaVersion": str(result.get("schemaVersion") or ""),
        "contextVersion": str(result.get("contextVersion") or ""),
        "policyVersion": str(result.get("policyVersion") or ""),
        "state": str(result.get("state") or ""),
        "shadowOnly": bool(result.get("shadowOnly")),
        "legacyContextUnchanged": bool(result.get("legacyContextUnchanged")),
        "legacyContextRead": bool(result.get("legacyContextRead")),
        "contextHash": str(result.get("contextHash") or ""),
        "materializationHash": str(result.get("materializationHash") or ""),
        "request": deepcopy(dict(request)),
        "authority": deepcopy(dict(authority)),
        "selectedContext": deepcopy(list(result["selectedContext"])),
        "filteredContext": deepcopy(list(result["filteredContext"])),
        "typedCitations": deepcopy(list(result["typedCitations"])),
        "generationContext": {
            "version": str(generation_context.get("version") or ""),
            "contentHash": str(generation_context.get("contentHash") or ""),
            "sourceCount": _nonnegative_int(
                generation_context.get("sourceCount"),
                field="generationContext sourceCount",
            ),
            "maxChars": _nonnegative_int(
                generation_context.get("maxChars"),
                field="generationContext maxChars",
            ),
            "truncated": bool(generation_context.get("truncated")),
        },
        "fallbacks": list(result["fallbacks"]),
        "trace": deepcopy(dict(trace)),
    }


__all__ = [
    "OWNER_TRUTH_CONTEXT_MATERIALIZATION_MAX_CHARS",
    "OWNER_TRUTH_CONTEXT_MATERIALIZATION_POLICY_VERSION",
    "OWNER_TRUTH_CONTEXT_MATERIALIZATION_SCHEMA_VERSION",
    "OWNER_TRUTH_CONTEXT_MATERIALIZATION_VERSION",
    "OwnerTruthContextMaterializationError",
    "OwnerTruthContextMaterializationService",
    "context_materialization_summary",
]

"""Rebuildable Owner Truth SearchDocument and QueryPlan contracts.

This is the first retrieval boundary for the Phase 4C secondary review tool.
It derives private search documents only from the current Owner-confirmed
``MemoryVersion`` projection.  The first implementation intentionally uses a
deterministic text fallback, not an unlabelled vector or provider ranking.
It never returns memory text, Source data, Candidate payloads, or a query
string in its value-free QA result.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping
import unicodedata

from .contracts import OwnerTruthContractError, require_nonblank, require_uuid
from .memory_projection import OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION
from .ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V2,
    OWNER_TRUTH_SCHEMA_VERSION_V3,
    flatten_memory_facets,
)


OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION = (
    "owner-truth-search-document-projection-v1"
)
OWNER_TRUTH_SEARCH_DOCUMENT_REBUILD_SCHEMA_VERSION = (
    "owner-truth-search-document-rebuild-v1"
)
OWNER_TRUTH_MEMORY_SEARCH_READ_SCHEMA_VERSION = "owner-truth-memory-search-read-v1"
OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE = "deterministicTextFallback"
OWNER_TRUTH_MEMORY_SEARCH_MAX_LIMIT = 20
OWNER_TRUTH_MEMORY_SEARCH_MAX_QUERY_CHARACTERS = 256
OWNER_TRUTH_MEMORY_SEARCH_MAX_DOCUMENT_CHARACTERS = 16_384

_QUERY_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+")
_QUERY_STOP_TERMS = frozenset(
    {
        "一下",
        "为什么",
        "什么",
        "你还",
        "告诉",
        "哪里",
        "回忆",
        "是否",
        "时光",
        "时候",
        "请问",
        "这段",
        "那段",
    }
)


class OwnerTruthSearchDocumentProjectionError(OwnerTruthContractError):
    """A SearchDocument or QueryPlan cannot be derived safely."""


class OwnerTruthMemorySearchReadError(OwnerTruthSearchDocumentProjectionError):
    """A request cannot safely use the Owner Truth memory-search boundary."""


def _normalized_text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerTruthSearchDocumentProjectionError(f"{field} must be a string")
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized:
        raise OwnerTruthSearchDocumentProjectionError(f"{field} must contain visible text")
    return normalized


def _text_leaves(value: Any) -> Iterable[str]:
    """Read only leaf strings from confirmed content in deterministic order."""

    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item)):
            yield from _text_leaves(value[key])
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _text_leaves(item)


def _private_search_text(
    content: Mapping[str, Any],
    *,
    content_schema_version: str,
) -> tuple[str, tuple[str, ...], bool]:
    if not isinstance(content, Mapping):
        raise OwnerTruthSearchDocumentProjectionError("search document content must be an object")
    pieces: list[str] = []
    structured_terms: list[str] = []
    searchable_content = dict(content)
    facet_terms: tuple[str, ...] = ()
    if content_schema_version in {
        OWNER_TRUTH_SCHEMA_VERSION_V2,
        OWNER_TRUTH_SCHEMA_VERSION_V3,
    }:
        facets = searchable_content.pop("facets", None)
        facet_terms = tuple(
            _normalized_text(term, field="search document facet")
            for term in flatten_memory_facets(facets)
        )
    for raw_value in _text_leaves(searchable_content):
        try:
            normalized = _normalized_text(raw_value, field="search document content")
        except OwnerTruthSearchDocumentProjectionError:
            continue
        pieces.append(normalized)
        if len(normalized) <= 256:
            structured_terms.append(normalized)
    structured_terms.extend(facet_terms)
    unique_terms = tuple(sorted(set(structured_terms)))
    facet_text = [term.partition(":")[2] for term in facet_terms]
    text = " ".join([*pieces, *facet_text])
    was_truncated = len(text) > OWNER_TRUTH_MEMORY_SEARCH_MAX_DOCUMENT_CHARACTERS
    return text[:OWNER_TRUTH_MEMORY_SEARCH_MAX_DOCUMENT_CHARACTERS], unique_terms, was_truncated


@dataclass(frozen=True)
class OwnerTruthSearchDocument:
    """Private, rebuildable search material for exactly one MemoryVersion.

    ``search_text`` and ``structured_terms`` are intentionally private fields.
    They are used only during the request and are never rendered by a QA read.
    """

    memory_id: str
    memory_version_id: str
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    content_hash: str
    content_schema_version: str
    memory_kind: str
    perspective_type: str
    sensitivity: str
    search_text: str
    structured_terms: tuple[str, ...]
    text_was_truncated: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", require_uuid(self.memory_id, field="memory_id"))
        object.__setattr__(
            self,
            "memory_version_id",
            require_uuid(self.memory_version_id, field="memory_version_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "content_hash", require_nonblank(self.content_hash, field="content_hash"))
        for field_name in (
            "content_schema_version",
            "memory_kind",
            "perspective_type",
            "sensitivity",
        ):
            object.__setattr__(
                self,
                field_name,
                require_nonblank(getattr(self, field_name), field=field_name),
            )
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthSearchDocumentProjectionError(
                "authority_epoch must be a non-negative integer"
            )
        if not isinstance(self.search_text, str):
            raise OwnerTruthSearchDocumentProjectionError("search_text must be a string")
        terms = tuple(self.structured_terms)
        if any(not isinstance(item, str) or not item for item in terms):
            raise OwnerTruthSearchDocumentProjectionError("structured_terms must contain text")
        if tuple(sorted(set(terms))) != terms:
            raise OwnerTruthSearchDocumentProjectionError(
                "structured_terms must be stable and unique"
            )
        object.__setattr__(self, "structured_terms", terms)
        if not isinstance(self.text_was_truncated, bool):
            raise OwnerTruthSearchDocumentProjectionError("text_was_truncated must be a boolean")


@dataclass(frozen=True)
class OwnerTruthMemorySearchQueryPlan:
    """The query execution plan with no raw query text or provider claim."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    projection_checkpoint: str
    normalized_query: str
    limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "projection_checkpoint",
            require_nonblank(self.projection_checkpoint, field="projection_checkpoint"),
        )
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthMemorySearchReadError("authority_epoch must be a non-negative integer")
        normalized_query = _normalized_text(self.normalized_query, field="query")
        if len(normalized_query) > OWNER_TRUTH_MEMORY_SEARCH_MAX_QUERY_CHARACTERS:
            raise OwnerTruthMemorySearchReadError("query exceeds the supported character limit")
        object.__setattr__(self, "normalized_query", normalized_query)
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or not 1 <= self.limit <= OWNER_TRUTH_MEMORY_SEARCH_MAX_LIMIT
        ):
            raise OwnerTruthMemorySearchReadError("limit is outside the supported range")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "retrievalMode": OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE,
            "semanticRankingAvailable": False,
            "authorityEpoch": self.authority_epoch,
            "projectionCheckpoint": self.projection_checkpoint,
            "queryCharacterCount": len(self.normalized_query),
            "limit": self.limit,
        }


@dataclass(frozen=True)
class OwnerTruthMemorySearchHit:
    """A citation-shaped hit that omits search material and memory content."""

    document: OwnerTruthSearchDocument
    rank: int
    match_kind: str
    match_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.document, OwnerTruthSearchDocument):
            raise OwnerTruthMemorySearchReadError("search hit requires a typed document")
        if not isinstance(self.rank, int) or isinstance(self.rank, bool) or self.rank < 1:
            raise OwnerTruthMemorySearchReadError("search hit rank must be positive")
        object.__setattr__(self, "match_kind", require_nonblank(self.match_kind, field="match_kind"))
        if (
            not isinstance(self.match_count, int)
            or isinstance(self.match_count, bool)
            or self.match_count < 1
        ):
            raise OwnerTruthMemorySearchReadError("search hit match_count must be positive")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "matchKind": self.match_kind,
            "matchCount": self.match_count,
            "citation": {
                "memoryId": self.document.memory_id,
                "memoryVersionId": self.document.memory_version_id,
                "contentHash": self.document.content_hash,
            },
            "memoryKind": self.document.memory_kind,
            "perspectiveType": self.document.perspective_type,
            "sensitivity": self.document.sensitivity,
            "searchTextTruncated": self.document.text_was_truncated,
        }


@dataclass(frozen=True)
class OwnerTruthSearchDocumentProjection:
    """Current SearchDocument set for one Owner/Vault authority epoch."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    checkpoint: str
    documents: tuple[OwnerTruthSearchDocument, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "checkpoint", require_nonblank(self.checkpoint, field="checkpoint"))
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthSearchDocumentProjectionError(
                "authority_epoch must be a non-negative integer"
            )
        documents = tuple(self.documents)
        if any(not isinstance(item, OwnerTruthSearchDocument) for item in documents):
            raise OwnerTruthSearchDocumentProjectionError("search documents must be typed")
        if any(
            item.vault_id != self.vault_id
            or item.owner_subject_id != self.owner_subject_id
            or item.authority_epoch != self.authority_epoch
            for item in documents
        ):
            raise OwnerTruthSearchDocumentProjectionError("search documents cross authority scope")
        if len({item.memory_version_id for item in documents}) != len(documents):
            raise OwnerTruthSearchDocumentProjectionError(
                "search documents must have one current entry per MemoryVersion"
            )
        object.__setattr__(
            self,
            "documents",
            tuple(sorted(documents, key=lambda item: item.memory_version_id)),
        )

    def value_free_summary(self) -> dict[str, object]:
        return {
            "schemaVersion": OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION,
            "authorityEpoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "documentCount": len(self.documents),
            "textTruncatedDocumentCount": sum(
                1 for item in self.documents if item.text_was_truncated
            ),
        }

    def document_digest(self) -> str:
        """Return an internal integrity digest without exposing search text."""

        return search_document_projection_digest(self)


@dataclass(frozen=True)
class OwnerTruthSearchDocumentProjectionRebuildResult:
    """Result of rebuilding the private, derived SearchDocument index."""

    outcome: str
    projection: OwnerTruthSearchDocumentProjection | None

    def __post_init__(self) -> None:
        outcome = require_nonblank(self.outcome, field="outcome")
        if outcome not in {"rebuilt", "unchanged", "sourceRebuilding"}:
            raise OwnerTruthSearchDocumentProjectionError(
                "search document rebuild outcome is unsupported"
            )
        if outcome == "sourceRebuilding":
            if self.projection is not None:
                raise OwnerTruthSearchDocumentProjectionError(
                    "source-rebuilding search document result must not retain a projection"
                )
        elif self.projection is None:
            raise OwnerTruthSearchDocumentProjectionError(
                "ready search document rebuild result requires a projection"
            )
        object.__setattr__(self, "outcome", outcome)

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_SEARCH_DOCUMENT_REBUILD_SCHEMA_VERSION,
            "state": "rebuilding" if self.projection is None else "ready",
            "outcome": self.outcome,
        }
        if self.projection is not None:
            summary["projection"] = self.projection.value_free_summary()
        return summary


@dataclass(frozen=True)
class OwnerTruthMemorySearchReadResult:
    """Fail-closed owner search result with no stale or raw content output."""

    state: str
    projection: OwnerTruthSearchDocumentProjection | None
    query_plan: OwnerTruthMemorySearchQueryPlan | None
    hits: tuple[OwnerTruthMemorySearchHit, ...]

    def __post_init__(self) -> None:
        state = require_nonblank(self.state, field="state")
        object.__setattr__(self, "state", state)
        hits = tuple(self.hits)
        if any(not isinstance(item, OwnerTruthMemorySearchHit) for item in hits):
            raise OwnerTruthMemorySearchReadError("search hits must be typed")
        if state == "ready":
            if self.projection is None or self.query_plan is None:
                raise OwnerTruthMemorySearchReadError(
                    "ready memory search requires a projection and QueryPlan"
                )
            if (
                self.projection.vault_id != self.query_plan.vault_id
                or self.projection.owner_subject_id != self.query_plan.owner_subject_id
                or self.projection.authority_epoch != self.query_plan.authority_epoch
                or self.projection.checkpoint != self.query_plan.projection_checkpoint
            ):
                raise OwnerTruthMemorySearchReadError("QueryPlan scope is stale")
        elif self.projection is not None or self.query_plan is not None or hits:
            raise OwnerTruthMemorySearchReadError(
                "non-ready memory search must not retain search state or hits"
            )
        if any(hit.rank != index + 1 for index, hit in enumerate(hits)):
            raise OwnerTruthMemorySearchReadError("search hit ranks must be contiguous")
        object.__setattr__(self, "hits", hits)

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_MEMORY_SEARCH_READ_SCHEMA_VERSION,
            "state": self.state,
            "hits": [item.value_free_summary() for item in self.hits],
        }
        if self.projection is not None and self.query_plan is not None:
            summary["projection"] = self.projection.value_free_summary()
            summary["queryPlan"] = self.query_plan.value_free_summary()
        return summary


def build_owner_truth_search_document_projection(
    *,
    memory_projection: Mapping[str, Any],
) -> OwnerTruthSearchDocumentProjection | None:
    """Build current documents from a ready Owner-confirmed memory projection.

    ``None`` represents a non-ready source projection.  It deliberately does
    not reuse an older document set after a rebuild, authority epoch change, or
    source inconsistency.
    """

    if not isinstance(memory_projection, Mapping):
        raise OwnerTruthSearchDocumentProjectionError("memory projection must be an object")
    state = str(memory_projection.get("state") or "")
    if state != "ready":
        return None
    if str(memory_projection.get("schemaVersion") or "") != OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION:
        raise OwnerTruthSearchDocumentProjectionError("memory projection schema is unsupported")
    vault_id = require_nonblank(str(memory_projection.get("vaultId") or ""), field="vault_id")
    owner_subject_id = require_nonblank(
        str(memory_projection.get("ownerSubjectId") or ""),
        field="owner_subject_id",
    )
    checkpoint = require_nonblank(
        str(memory_projection.get("checkpoint") or ""),
        field="projection_checkpoint",
    )
    authority_epoch = memory_projection.get("authorityEpoch")
    if (
        not isinstance(authority_epoch, int)
        or isinstance(authority_epoch, bool)
        or authority_epoch < 0
    ):
        raise OwnerTruthSearchDocumentProjectionError(
            "memory projection authorityEpoch must be a non-negative integer"
        )
    entries = memory_projection.get("entries")
    if not isinstance(entries, list):
        raise OwnerTruthSearchDocumentProjectionError("memory projection entries must be a list")
    documents = tuple(
        _search_document_from_projection_entry(
            entry,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
        )
        for entry in entries
    )
    return OwnerTruthSearchDocumentProjection(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        authority_epoch=authority_epoch,
        checkpoint=checkpoint,
        documents=documents,
    )


def build_owner_truth_memory_search_query_plan(
    *,
    projection: OwnerTruthSearchDocumentProjection,
    query: str,
    limit: int,
) -> OwnerTruthMemorySearchQueryPlan:
    if not isinstance(projection, OwnerTruthSearchDocumentProjection):
        raise TypeError("projection must be an OwnerTruthSearchDocumentProjection")
    return OwnerTruthMemorySearchQueryPlan(
        vault_id=projection.vault_id,
        owner_subject_id=projection.owner_subject_id,
        authority_epoch=projection.authority_epoch,
        projection_checkpoint=projection.checkpoint,
        normalized_query=query,
        limit=limit,
    )


def search_document_projection_digest(
    projection: OwnerTruthSearchDocumentProjection,
) -> str:
    """Hash exact private index material for repository integrity checks.

    The digest binds current citation metadata and private search fields without
    rendering those fields in an API result.  It is a rebuildable projection
    checksum, never an Owner Truth authority record.
    """

    if not isinstance(projection, OwnerTruthSearchDocumentProjection):
        raise TypeError("projection must be an OwnerTruthSearchDocumentProjection")
    payload = {
        "schemaVersion": OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION,
        "vaultId": projection.vault_id,
        "ownerSubjectId": projection.owner_subject_id,
        "authorityEpoch": projection.authority_epoch,
        "sourceCheckpoint": projection.checkpoint,
        "documents": [
            {
                "memoryId": document.memory_id,
                "memoryVersionId": document.memory_version_id,
                "contentHash": document.content_hash,
                "contentSchemaVersion": document.content_schema_version,
                "memoryKind": document.memory_kind,
                "perspectiveType": document.perspective_type,
                "sensitivity": document.sensitivity,
                "searchText": document.search_text,
                "structuredTerms": list(document.structured_terms),
                "textWasTruncated": document.text_was_truncated,
            }
            for document in projection.documents
        ],
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def search_owner_truth_documents(
    *,
    projection: OwnerTruthSearchDocumentProjection,
    query_plan: OwnerTruthMemorySearchQueryPlan,
) -> tuple[OwnerTruthMemorySearchHit, ...]:
    """Run a deterministic, owner-scoped fallback until semantic ranking exists."""

    if not isinstance(projection, OwnerTruthSearchDocumentProjection):
        raise TypeError("projection must be an OwnerTruthSearchDocumentProjection")
    if not isinstance(query_plan, OwnerTruthMemorySearchQueryPlan):
        raise TypeError("query_plan must be an OwnerTruthMemorySearchQueryPlan")
    if (
        projection.vault_id != query_plan.vault_id
        or projection.owner_subject_id != query_plan.owner_subject_id
        or projection.authority_epoch != query_plan.authority_epoch
        or projection.checkpoint != query_plan.projection_checkpoint
    ):
        raise OwnerTruthMemorySearchReadError("QueryPlan does not match the current projection")
    scored: list[tuple[int, int, OwnerTruthSearchDocument, str]] = []
    query = query_plan.normalized_query
    query_terms = _deterministic_query_terms(query)
    for document in projection.documents:
        structured_match_count = sum(
            1 for term in document.structured_terms if query in term
        )
        text_match_count = document.search_text.count(query)
        term_score = 0
        term_match_count = 0
        for term in query_terms:
            structured_hits = sum(1 for value in document.structured_terms if term in value)
            text_hits = document.search_text.count(term)
            if structured_hits == 0 and text_hits == 0:
                continue
            weight = min(len(term), 8) ** 2
            term_score += (structured_hits * 5 + text_hits) * weight
            term_match_count += structured_hits + text_hits
        if structured_match_count == 0 and text_match_count == 0 and term_match_count == 0:
            continue
        match_count = structured_match_count + text_match_count + term_match_count
        if structured_match_count:
            match_kind = "structuredTerm"
        elif text_match_count:
            match_kind = "searchText"
        else:
            match_kind = "queryTerm"
        score = (
            structured_match_count * 10_000
            + min(text_match_count, 99) * 1_000
            + term_score
        )
        scored.append((score, match_count, document, match_kind))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].memory_version_id))
    return tuple(
        OwnerTruthMemorySearchHit(
            document=document,
            rank=index,
            match_kind=match_kind,
            match_count=match_count,
        )
        for index, (_score, match_count, document, match_kind) in enumerate(
            scored[: query_plan.limit],
            start=1,
        )
    )


def _deterministic_query_terms(query: str) -> tuple[str, ...]:
    """Derive bounded lexical terms without provider or cross-scope reads.

    Full-query matches remain authoritative. These terms only make natural
    Chinese questions such as ``请陪我回忆父亲修自行车`` match a confirmed
    memory containing ``父亲修好自行车``. The function is deterministic and
    deliberately does not claim semantic/vector ranking.
    """

    terms: set[str] = set()
    for token in _QUERY_TOKEN_PATTERN.findall(query):
        if token.isascii():
            if len(token) >= 2:
                terms.add(token)
            continue
        if len(token) <= 4:
            if len(token) >= 2 and token not in _QUERY_STOP_TERMS:
                terms.add(token)
            continue
        for size in range(4, 1, -1):
            for start in range(0, len(token) - size + 1):
                term = token[start : start + size]
                if term not in _QUERY_STOP_TERMS:
                    terms.add(term)
                if len(terms) >= 96:
                    break
            if len(terms) >= 96:
                break
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))


def _search_document_from_projection_entry(
    entry: Any,
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
) -> OwnerTruthSearchDocument:
    if not isinstance(entry, Mapping):
        raise OwnerTruthSearchDocumentProjectionError("memory projection entry must be an object")
    if str(entry.get("visibility") or "") != "owner":
        raise OwnerTruthSearchDocumentProjectionError("memory projection entry visibility is unsupported")
    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        raise OwnerTruthSearchDocumentProjectionError("memory projection entry citation is missing")
    content = entry.get("content")
    content_schema_version = str(entry.get("contentSchemaVersion") or "")
    search_text, structured_terms, was_truncated = _private_search_text(
        content,
        content_schema_version=content_schema_version,
    )
    return OwnerTruthSearchDocument(
        memory_id=str(citation.get("memoryId") or ""),
        memory_version_id=str(citation.get("memoryVersionId") or ""),
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        authority_epoch=authority_epoch,
        content_hash=str(citation.get("contentHash") or ""),
        content_schema_version=content_schema_version,
        memory_kind=str(entry.get("memoryKind") or ""),
        perspective_type=str(entry.get("perspectiveType") or ""),
        sensitivity=str(entry.get("sensitivity") or ""),
        search_text=search_text,
        structured_terms=structured_terms,
        text_was_truncated=was_truncated,
    )


__all__ = [
    "OWNER_TRUTH_MEMORY_SEARCH_MAX_LIMIT",
    "OWNER_TRUTH_MEMORY_SEARCH_READ_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE",
    "OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION",
    "OWNER_TRUTH_SEARCH_DOCUMENT_REBUILD_SCHEMA_VERSION",
    "OwnerTruthMemorySearchHit",
    "OwnerTruthMemorySearchQueryPlan",
    "OwnerTruthMemorySearchReadError",
    "OwnerTruthMemorySearchReadResult",
    "OwnerTruthSearchDocument",
    "OwnerTruthSearchDocumentProjection",
    "OwnerTruthSearchDocumentProjectionError",
    "OwnerTruthSearchDocumentProjectionRebuildResult",
    "build_owner_truth_memory_search_query_plan",
    "build_owner_truth_search_document_projection",
    "search_document_projection_digest",
    "search_owner_truth_documents",
]

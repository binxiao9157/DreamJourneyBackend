"""Owner-only person-memory profile derived from current formal memories.

The profile is a rebuildable read model, never a second source of truth.  It
turns the complete current ``MemoryVersion`` set into one continuous plain-text
life record. Stable dimensions remain internal evidence indexes for search and
audit; they are not the primary reading experience.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Iterable, Mapping

from app.domain.owner_truth.person_memory_model import (
    PERSON_BIOGRAPHY_PROJECTION_SCHEMA_VERSION,
    PERSON_MEMORY_MODEL_ALGORITHM_VERSION,
    PERSON_MEMORY_MODEL_SCHEMA_VERSION,
    build_person_memory_model,
    build_person_memory_model_incremental,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemory,
    OwnerTruthFormalMemoryCursor,
    OwnerTruthFormalMemoryError,
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)
from app.services.owner_truth_derived_memory_access import (
    require_owner_truth_derived_memory_access,
)


PERSON_MEMORY_PROFILE_SCHEMA_VERSION = "owner-truth-person-memory-profile-v2"
PERSON_MEMORY_PROFILE_ALGORITHM_VERSION = PERSON_MEMORY_MODEL_ALGORITHM_VERSION
PERSON_LIFE_RECORD_SCHEMA_VERSION = "owner-truth-person-life-record-v2"
PERSON_LIFE_RECORD_ALGORITHM_VERSION = "person-life-record-from-biography-v2"
PERSON_LIFE_STORY_SCHEMA_VERSION = PERSON_BIOGRAPHY_PROJECTION_SCHEMA_VERSION
PERSON_LIFE_STORY_ALGORITHM_VERSION = PERSON_MEMORY_MODEL_ALGORITHM_VERSION
_MAX_FORMAL_MEMORY_COUNT = 10_000
_MAX_DIMENSION_EVIDENCE = 8
_MAX_LIFE_STORY_CHAPTERS = 12
_MAX_CHAPTER_MEMORIES_IN_TEXT = 24
_MAX_CLAUSE_CHARACTERS = 280
_WHITESPACE = re.compile(r"\s+")

_FAMILY_KEYWORDS = (
    "家人", "家庭", "父亲", "母亲", "爸爸", "妈妈", "爷爷", "奶奶",
    "祖父", "祖母", "外祖", "兄弟", "姐妹", "哥哥", "姐姐", "弟弟",
    "妹妹", "丈夫", "妻子", "爱人", "孩子", "儿子", "女儿", "祖孙",
    "亲子", "童年", "小时候", "老家", "成长",
)
_LEARNING_AND_WORK_KEYWORDS = (
    "学校", "上学", "求学", "读书", "大学", "老师", "同学", "毕业",
    "工作", "公司", "职业", "职场", "项目", "创业", "专业", "同事",
    "客户", "岗位", "退休",
)


class OwnerTruthPersonMemoryProfileError(OwnerTruthFormalMemoryError):
    """A person-memory profile cannot be derived safely."""


@dataclass(frozen=True)
class _ProfileCacheEntry:
    """Private, rebuildable derived-read cache; never a memory authority."""

    source_fingerprint: str
    profile: "OwnerTruthPersonMemoryProfile"


class _OwnerTruthPersonMemoryProfileCache:
    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[tuple[str, str], _ProfileCacheEntry] = {}

    def get(self, *, vault_id: str, owner_subject_id: str) -> _ProfileCacheEntry | None:
        with self._lock:
            return self._entries.get((vault_id, owner_subject_id))

    def put(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        entry: _ProfileCacheEntry,
    ) -> None:
        with self._lock:
            self._entries[(vault_id, owner_subject_id)] = entry


@dataclass(frozen=True)
class OwnerTruthPersonMemoryDimension:
    dimension: str
    title: str
    narrative: str | None
    supporting_memory_ids: tuple[str, ...]
    supporting_memory_version_ids: tuple[str, ...] = ()

    def public_contract(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "title": self.title,
            "status": "ready" if self.narrative else "empty",
            "narrative": self.narrative,
            "supportingMemoryCount": len(self.supporting_memory_ids),
            "supportingMemoryIds": list(self.supporting_memory_ids),
            "supportingMemoryVersionIds": list(self.supporting_memory_version_ids),
        }


@dataclass(frozen=True)
class OwnerTruthPersonMemoryProfile:
    vault_id: str
    profile_version: str
    updated_at: str | None
    memory_count: int
    life_record: "OwnerTruthPersonLifeRecord"
    life_story: "OwnerTruthPersonLifeStory"
    dimensions: tuple[OwnerTruthPersonMemoryDimension, ...]
    memory_model: Mapping[str, Any]
    source_fingerprint: str = ""
    derivation_state: str = "ready"
    stale_reason: str | None = None
    rebuild_mode: str = "full"
    affected_dimensions: tuple[str, ...] = ()

    def public_contract(self) -> dict[str, Any]:
        if self.derivation_state not in {"ready", "stale"}:
            raise OwnerTruthPersonMemoryProfileError("person-memory derivation state is invalid")
        if self.rebuild_mode not in {"full", "incremental", "unchanged", "staleFallback"}:
            raise OwnerTruthPersonMemoryProfileError("person-memory rebuild mode is invalid")
        return {
            "schemaVersion": PERSON_MEMORY_PROFILE_SCHEMA_VERSION,
            "algorithmVersion": PERSON_MEMORY_PROFILE_ALGORITHM_VERSION,
            "state": (
                "stale"
                if self.derivation_state == "stale"
                else ("ready" if self.memory_count else "empty")
            ),
            "vaultId": self.vault_id,
            "profileVersion": self.profile_version,
            "updatedAt": self.updated_at,
            "memoryCount": self.memory_count,
            "sourceFingerprint": self.source_fingerprint or None,
            "derivationState": self.derivation_state,
            "staleReason": self.stale_reason,
            "rebuildMode": self.rebuild_mode,
            "affectedDimensions": list(self.affected_dimensions),
            "lifeRecord": self.life_record.public_contract(),
            "lifeStory": self.life_story.public_contract(),
            "dimensions": [item.public_contract() for item in self.dimensions],
            "memoryModel": _memory_model_summary(self.memory_model),
        }


@dataclass(frozen=True)
class OwnerTruthPersonLifeRecord:
    title: str
    paragraphs: tuple[str, ...]
    paragraph_evidence: tuple["OwnerTruthPersonMemoryParagraphEvidence", ...] = ()

    def __post_init__(self) -> None:
        if len(self.paragraph_evidence) not in {0, len(self.paragraphs)}:
            raise OwnerTruthPersonMemoryProfileError(
                "life-record paragraph evidence must align with every paragraph"
            )
        if self.paragraph_evidence:
            expected_indexes = tuple(range(len(self.paragraphs)))
            actual_indexes = tuple(item.paragraph_index for item in self.paragraph_evidence)
            if actual_indexes != expected_indexes:
                raise OwnerTruthPersonMemoryProfileError(
                    "life-record paragraph evidence indexes are invalid"
                )

    @property
    def text(self) -> str | None:
        return "\n\n".join(self.paragraphs) if self.paragraphs else None

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": PERSON_LIFE_RECORD_SCHEMA_VERSION,
            "algorithmVersion": PERSON_LIFE_RECORD_ALGORITHM_VERSION,
            "format": "plainText",
            "state": "ready" if self.paragraphs else "empty",
            "title": self.title,
            "paragraphCount": len(self.paragraphs),
            "paragraphs": list(self.paragraphs),
            "text": self.text,
            "paragraphEvidence": [
                item.public_contract() for item in self.paragraph_evidence
            ],
        }


@dataclass(frozen=True)
class OwnerTruthPersonMemoryParagraphEvidence:
    """Trace one derived paragraph to the current formal facts behind it."""

    paragraph_index: int
    supporting_memory_ids: tuple[str, ...]
    supporting_memory_version_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.paragraph_index < 0:
            raise OwnerTruthPersonMemoryProfileError(
                "person-memory paragraph evidence index is invalid"
            )
        if not self.supporting_memory_ids or (
            len(self.supporting_memory_ids)
            != len(self.supporting_memory_version_ids)
        ):
            raise OwnerTruthPersonMemoryProfileError(
                "person-memory paragraph evidence is incomplete"
            )
        if len(set(self.supporting_memory_ids)) != len(self.supporting_memory_ids):
            raise OwnerTruthPersonMemoryProfileError(
                "person-memory paragraph evidence repeats a formal memory"
            )
        if (
            len(set(self.supporting_memory_version_ids))
            != len(self.supporting_memory_version_ids)
        ):
            raise OwnerTruthPersonMemoryProfileError(
                "person-memory paragraph evidence repeats a formal-memory version"
            )

    def public_contract(self) -> dict[str, Any]:
        return {
            "paragraphIndex": self.paragraph_index,
            "supportingMemoryCount": len(self.supporting_memory_ids),
            "supportingMemoryIds": list(self.supporting_memory_ids),
            "supportingMemoryVersionIds": list(self.supporting_memory_version_ids),
        }


@dataclass(frozen=True)
class OwnerTruthPersonLifeStoryChapter:
    chapter_id: str
    title: str
    paragraphs: tuple[str, ...]
    supporting_memory_ids: tuple[str, ...]
    supporting_memory_version_ids: tuple[str, ...]
    facets: tuple[str, ...] = ()
    paragraph_evidence: tuple[OwnerTruthPersonMemoryParagraphEvidence, ...] = ()

    def __post_init__(self) -> None:
        if len(self.paragraph_evidence) not in {0, len(self.paragraphs)}:
            raise OwnerTruthPersonMemoryProfileError(
                "life-story paragraph evidence must align with every paragraph"
            )
        if self.paragraph_evidence:
            expected_indexes = tuple(range(len(self.paragraphs)))
            actual_indexes = tuple(item.paragraph_index for item in self.paragraph_evidence)
            if actual_indexes != expected_indexes:
                raise OwnerTruthPersonMemoryProfileError(
                    "life-story paragraph evidence indexes are invalid"
                )

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    def public_contract(self) -> dict[str, Any]:
        return {
            "chapterId": self.chapter_id,
            "title": self.title,
            "format": "plainText",
            "paragraphCount": len(self.paragraphs),
            "paragraphs": list(self.paragraphs),
            "text": self.text,
            "supportingMemoryCount": len(self.supporting_memory_ids),
            "supportingMemoryIds": list(self.supporting_memory_ids),
            "supportingMemoryVersionIds": list(self.supporting_memory_version_ids),
            "facets": list(self.facets),
            "paragraphEvidence": [
                item.public_contract() for item in self.paragraph_evidence
            ],
        }


@dataclass(frozen=True)
class OwnerTruthPersonLifeStory:
    title: str
    overview: str | None
    chapters: tuple[OwnerTruthPersonLifeStoryChapter, ...]
    document_version: str | None = None
    source_fingerprint: str | None = None

    def public_contract(self) -> dict[str, Any]:
        supporting_ids = tuple(
            memory_id
            for chapter in self.chapters
            for memory_id in chapter.supporting_memory_ids
        )
        return {
            "schemaVersion": PERSON_LIFE_STORY_SCHEMA_VERSION,
            "algorithmVersion": PERSON_LIFE_STORY_ALGORITHM_VERSION,
            "format": "plainText",
            "state": "ready" if self.chapters else "empty",
            "title": self.title,
            "overview": self.overview,
            "documentVersion": self.document_version,
            "sourceFingerprint": self.source_fingerprint,
            "chapterCount": len(self.chapters),
            "chapters": [chapter.public_contract() for chapter in self.chapters],
            "supportingMemoryCount": len(supporting_ids),
        }


@dataclass(frozen=True)
class _LifeStoryChapterGroup:
    key: str
    title: str
    priority: int
    memories: tuple[OwnerTruthFormalMemory, ...]


class OwnerTruthPersonMemoryProfileService:
    """Build a bounded continuous-text life record from all current memories."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._formal_memory = OwnerTruthFormalMemoryService(store)
        cache = getattr(store, "_owner_truth_person_memory_profile_cache", None)
        if cache is None:
            cache = _OwnerTruthPersonMemoryProfileCache()
            setattr(store, "_owner_truth_person_memory_profile_cache", cache)
        if not isinstance(cache, _OwnerTruthPersonMemoryProfileCache):
            raise OwnerTruthPersonMemoryProfileError("person-memory cache contract is invalid")
        self._cache = cache

    def read(self, *, context: OwnerTruthCommandContext) -> OwnerTruthPersonMemoryProfile:
        memories = self._all_current_memories(context=context)
        if memories:
            require_owner_truth_derived_memory_access(store=self._store, context=context)
        entries = tuple(_person_model_entry(item) for item in memories)
        source_fingerprint = _profile_source_fingerprint(entries)
        cached = self._cache.get(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
        )
        if cached is not None and cached.source_fingerprint == source_fingerprint:
            # Current rights were checked above. A byte-identical current fact
            # set needs no fresh full-document derivation.
            return replace(
                cached.profile,
                derivation_state="ready",
                stale_reason=None,
                rebuild_mode="unchanged",
                affected_dimensions=(),
            )
        try:
            if cached is None:
                memory_model = build_person_memory_model(entries)
                rebuild_mode = "full"
            else:
                # The source set is current formal authority, while the prior
                # model is only a rebuildable cache. The domain helper keeps
                # unaffected dimensions and life-story sections intact rather
                # than calculating the complete profile then relabelling it
                # as incremental.
                memory_model = build_person_memory_model_incremental(
                    previous_model=cached.profile.memory_model,
                    entries=entries,
                )
                rebuild_mode = "incremental"
            dimensions = _dimensions_from_model(memory_model)
            life_story = _life_story_from_model(memory_model)
            life_record = _life_record_from_story(life_story)
            affected_dimensions = _affected_dimensions(
                previous=cached.profile if cached is not None else None,
                current=dimensions,
            )
            if cached is not None:
                dimensions = _reuse_unchanged_dimensions(
                    previous=cached.profile.dimensions,
                    current=dimensions,
                )
                life_story = _reuse_unchanged_life_story(
                    previous=cached.profile.life_story,
                    current=life_story,
                )
                life_record = _life_record_from_story(life_story)
            profile = OwnerTruthPersonMemoryProfile(
                vault_id=context.vault_id,
                profile_version=str(memory_model["modelVersion"]),
                updated_at=max(
                    (item.current_version.created_at for item in memories),
                    default=None,
                ),
                memory_count=len(memories),
                life_record=life_record,
                life_story=life_story,
                dimensions=dimensions,
                memory_model=memory_model,
                source_fingerprint=source_fingerprint,
                derivation_state="ready",
                rebuild_mode=rebuild_mode,
                affected_dimensions=affected_dimensions,
            )
        except OwnerTruthPersonMemoryProfileError:
            if cached is None:
                raise
            # A failed derived rebuild must not masquerade as a current formal
            # view. The last evidence-bound version remains marked stale.
            return replace(
                cached.profile,
                derivation_state="stale",
                stale_reason="derivationFailed",
                rebuild_mode="staleFallback",
                affected_dimensions=(),
            )
        self._cache.put(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            entry=_ProfileCacheEntry(source_fingerprint=source_fingerprint, profile=profile),
        )
        return profile

    def _all_current_memories(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthFormalMemory, ...]:
        items: list[OwnerTruthFormalMemory] = []
        cursor: OwnerTruthFormalMemoryCursor | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self._formal_memory.list(
                context=context,
                query=OwnerTruthFormalMemoryQuery(cursor=cursor, limit=100),
            )
            items.extend(page.items)
            if len(items) > _MAX_FORMAL_MEMORY_COUNT:
                raise OwnerTruthPersonMemoryProfileError(
                    "person-memory profile exceeds the supported formal-memory count"
                )
            if page.next_cursor is None:
                break
            if page.next_cursor in seen_cursors:
                raise OwnerTruthPersonMemoryProfileError(
                    "formal-memory pagination did not advance"
                )
            seen_cursors.add(page.next_cursor)
            cursor = OwnerTruthFormalMemoryCursor.decode(page.next_cursor)
        if len({item.memory_id for item in items}) != len(items):
            raise OwnerTruthPersonMemoryProfileError(
                "person-memory profile contains duplicate formal memories"
            )
        return tuple(items)


def _person_model_entry(memory: OwnerTruthFormalMemory) -> dict[str, Any]:
    version = memory.current_version
    first_ref = next(
        (item for item in version.evidence_refs if isinstance(item, Mapping)),
        {},
    )
    return {
        "memoryId": memory.memory_id,
        "memoryVersionId": version.version_id,
        "memoryVersion": version.version_number,
        "memoryKind": memory.memory_kind,
        "epistemicStatus": memory.epistemic_status,
        "sensitivity": memory.sensitivity,
        "contentSchemaVersion": version.content_schema_version,
        "contentHash": version.content_hash,
        "content": dict(version.content),
        "evidenceRefs": [dict(item) for item in version.evidence_refs],
        "sourceId": first_ref.get("sourceId"),
        "sourceVersion": first_ref.get("sourceVersion"),
    }


def _profile_source_fingerprint(entries: Iterable[Mapping[str, Any]]) -> str:
    """Bind a derived profile to current formal version/hash pairs only."""

    material = [
        {
            "memoryId": str(entry.get("memoryId") or ""),
            "memoryVersionId": str(entry.get("memoryVersionId") or ""),
            "contentHash": str(entry.get("contentHash") or ""),
        }
        for entry in entries
    ]
    return sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _dimension_fingerprint(dimension: OwnerTruthPersonMemoryDimension) -> str:
    return sha256(
        json.dumps(
            {
                "dimension": dimension.dimension,
                "narrative": dimension.narrative,
                "supportingMemoryIds": list(dimension.supporting_memory_ids),
                "supportingMemoryVersionIds": list(dimension.supporting_memory_version_ids),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _affected_dimensions(
    *,
    previous: OwnerTruthPersonMemoryProfile | None,
    current: tuple[OwnerTruthPersonMemoryDimension, ...],
) -> tuple[str, ...]:
    if previous is None:
        return tuple(item.dimension for item in current)
    before = {item.dimension: _dimension_fingerprint(item) for item in previous.dimensions}
    return tuple(
        item.dimension
        for item in current
        if before.get(item.dimension) != _dimension_fingerprint(item)
    )


def _reuse_unchanged_dimensions(
    *,
    previous: tuple[OwnerTruthPersonMemoryDimension, ...],
    current: tuple[OwnerTruthPersonMemoryDimension, ...],
) -> tuple[OwnerTruthPersonMemoryDimension, ...]:
    previous_by_key = {item.dimension: item for item in previous}
    return tuple(
        previous_by_key[item.dimension]
        if (
            item.dimension in previous_by_key
            and _dimension_fingerprint(previous_by_key[item.dimension])
            == _dimension_fingerprint(item)
        )
        else item
        for item in current
    )


def _chapter_fingerprint(chapter: OwnerTruthPersonLifeStoryChapter) -> str:
    return sha256(
        json.dumps(
            {
                "chapterId": chapter.chapter_id,
                "title": chapter.title,
                "paragraphs": list(chapter.paragraphs),
                "supportingMemoryIds": list(chapter.supporting_memory_ids),
                "supportingMemoryVersionIds": list(chapter.supporting_memory_version_ids),
                "paragraphEvidence": [item.public_contract() for item in chapter.paragraph_evidence],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _reuse_unchanged_life_story(
    *,
    previous: OwnerTruthPersonLifeStory,
    current: OwnerTruthPersonLifeStory,
) -> OwnerTruthPersonLifeStory:
    previous_by_id = {chapter.chapter_id: chapter for chapter in previous.chapters}
    chapters = tuple(
        previous_by_id[chapter.chapter_id]
        if (
            chapter.chapter_id in previous_by_id
            and _chapter_fingerprint(previous_by_id[chapter.chapter_id])
            == _chapter_fingerprint(chapter)
        )
        else chapter
        for chapter in current.chapters
    )
    return replace(current, chapters=chapters)


def _memory_model_summary(model: Mapping[str, Any]) -> dict[str, Any]:
    cognitive = model.get("cognitiveProjection")
    relationships = model.get("relationshipProjection")
    biography = model.get("biographyProjection")
    if not all(
        isinstance(value, Mapping)
        for value in (cognitive, relationships, biography)
    ):
        raise OwnerTruthPersonMemoryProfileError("person-memory model is incomplete")
    return {
        "schemaVersion": PERSON_MEMORY_MODEL_SCHEMA_VERSION,
        "algorithmVersion": PERSON_MEMORY_MODEL_ALGORITHM_VERSION,
        "state": str(model.get("state") or ""),
        "modelVersion": str(model.get("modelVersion") or ""),
        "sourceFingerprint": str(model.get("sourceFingerprint") or ""),
        "memoryCount": int(model.get("memoryCount") or 0),
        "consolidatedMemoryCount": int(model.get("consolidatedMemoryCount") or 0),
        "unresolvedConflictCount": int(model.get("unresolvedConflictCount") or 0),
        "cognitiveItemCount": sum(
            len(cognitive.get(name, []))
            for name in ("facts", "experiences", "observations", "mentalModels")
            if isinstance(cognitive.get(name), list)
        ),
        "entityCount": len(relationships.get("entities", [])),
        "relationCount": len(relationships.get("relations", [])),
        "biographyDocumentVersion": str(biography.get("documentVersion") or ""),
    }


def _dimensions_from_model(
    model: Mapping[str, Any],
) -> tuple[OwnerTruthPersonMemoryDimension, ...]:
    raw_dimensions = model.get("dimensions")
    if not isinstance(raw_dimensions, list):
        raise OwnerTruthPersonMemoryProfileError("person-memory model has no dimensions")
    dimensions: list[OwnerTruthPersonMemoryDimension] = []
    for item in raw_dimensions:
        if not isinstance(item, Mapping):
            raise OwnerTruthPersonMemoryProfileError("person-memory dimension is invalid")
        memory_ids = item.get("supportingMemoryIds")
        version_ids = item.get("supportingMemoryVersionIds")
        if not isinstance(memory_ids, list) or not isinstance(version_ids, list):
            raise OwnerTruthPersonMemoryProfileError(
                "person-memory dimension evidence is invalid"
            )
        dimensions.append(
            OwnerTruthPersonMemoryDimension(
                dimension=str(item.get("dimension") or ""),
                title=str(item.get("title") or ""),
                narrative=(
                    str(item["narrative"])
                    if isinstance(item.get("narrative"), str)
                    else None
                ),
                supporting_memory_ids=tuple(str(value) for value in memory_ids),
                supporting_memory_version_ids=tuple(
                    str(value) for value in version_ids
                ),
            )
        )
    return tuple(dimensions)


def _life_story_from_model(model: Mapping[str, Any]) -> OwnerTruthPersonLifeStory:
    biography = model.get("biographyProjection")
    if not isinstance(biography, Mapping):
        raise OwnerTruthPersonMemoryProfileError("person-memory model has no biography")
    raw_sections = biography.get("sections")
    if not isinstance(raw_sections, list):
        raise OwnerTruthPersonMemoryProfileError("biography sections are invalid")
    chapters: list[OwnerTruthPersonLifeStoryChapter] = []
    for section in raw_sections:
        if not isinstance(section, Mapping):
            raise OwnerTruthPersonMemoryProfileError("biography section is invalid")
        blocks = section.get("blocks")
        evidence = section.get("evidence")
        if not isinstance(blocks, list) or not isinstance(evidence, list):
            raise OwnerTruthPersonMemoryProfileError("biography section evidence is invalid")
        block_pairs: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            text = str(block.get("text") or "").strip()
            block_citations = block.get("evidence")
            if not text or not isinstance(block_citations, list):
                continue
            block_memory_ids = tuple(
                str(item.get("memoryId") or "")
                for item in block_citations
                if isinstance(item, Mapping)
            )
            block_version_ids = tuple(
                str(item.get("memoryVersionId") or "")
                for item in block_citations
                if isinstance(item, Mapping)
            )
            if (
                not block_memory_ids
                or len(block_memory_ids) != len(block_version_ids)
                or any(not value for value in (*block_memory_ids, *block_version_ids))
            ):
                raise OwnerTruthPersonMemoryProfileError(
                    "biography block has incomplete evidence"
                )
            block_pairs.append((text, block_memory_ids, block_version_ids))
        paragraphs = tuple(item[0] for item in block_pairs)
        citations = [item for item in evidence if isinstance(item, Mapping)]
        memory_ids = tuple(str(item.get("memoryId") or "") for item in citations)
        version_ids = tuple(
            str(item.get("memoryVersionId") or "") for item in citations
        )
        facets = tuple(
            dict.fromkeys(
                str(facet)
                for block in blocks
                if isinstance(block, Mapping)
                for facet in block.get("facets", [])
            )
        )
        if not paragraphs or any(not value for value in (*memory_ids, *version_ids)):
            raise OwnerTruthPersonMemoryProfileError(
                "biography section has incomplete text or evidence"
            )
        chapters.append(
            OwnerTruthPersonLifeStoryChapter(
                chapter_id=str(section.get("sectionId") or ""),
                title=str(section.get("title") or ""),
                paragraphs=paragraphs,
                supporting_memory_ids=memory_ids,
                supporting_memory_version_ids=version_ids,
                facets=facets,
                paragraph_evidence=tuple(
                    OwnerTruthPersonMemoryParagraphEvidence(
                        paragraph_index=index,
                        supporting_memory_ids=block_memory_ids,
                        supporting_memory_version_ids=block_version_ids,
                    )
                    for index, (_, block_memory_ids, block_version_ids) in enumerate(block_pairs)
                ),
            )
        )
    return OwnerTruthPersonLifeStory(
        title=str(biography.get("title") or "我的人生记录"),
        overview=(
            str(biography["overview"])
            if isinstance(biography.get("overview"), str)
            else None
        ),
        chapters=tuple(chapters),
        document_version=str(biography.get("documentVersion") or "") or None,
        source_fingerprint=str(biography.get("sourceFingerprint") or "") or None,
    )


def _life_record_from_story(
    story: OwnerTruthPersonLifeStory,
) -> OwnerTruthPersonLifeRecord:
    all_memory_versions: dict[str, str] = {}
    for chapter in story.chapters:
        all_memory_versions.update(
            dict(zip(chapter.supporting_memory_ids, chapter.supporting_memory_version_ids))
        )
    all_memory_ids = tuple(all_memory_versions)
    all_version_ids = tuple(all_memory_versions[memory_id] for memory_id in all_memory_ids)
    pairs: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    if story.overview:
        pairs.append((story.overview, all_memory_ids, all_version_ids))
    for chapter in story.chapters:
        if chapter.paragraph_evidence:
            pairs.extend(
                (
                    paragraph,
                    evidence.supporting_memory_ids,
                    evidence.supporting_memory_version_ids,
                )
                for paragraph, evidence in zip(
                    chapter.paragraphs,
                    chapter.paragraph_evidence,
                )
            )
        else:
            pairs.extend(
                (paragraph, chapter.supporting_memory_ids, chapter.supporting_memory_version_ids)
                for paragraph in chapter.paragraphs
            )

    paragraphs: list[str] = []
    paragraph_evidence: list[OwnerTruthPersonMemoryParagraphEvidence] = []
    seen: set[str] = set()
    for paragraph, memory_ids, version_ids in pairs:
        if paragraph in seen:
            continue
        seen.add(paragraph)
        paragraph_evidence.append(
            OwnerTruthPersonMemoryParagraphEvidence(
                paragraph_index=len(paragraphs),
                supporting_memory_ids=memory_ids,
                supporting_memory_version_ids=version_ids,
            )
        )
        paragraphs.append(paragraph)
    return OwnerTruthPersonLifeRecord(
        title=story.title,
        paragraphs=tuple(paragraphs),
        paragraph_evidence=tuple(paragraph_evidence),
    )


def _build_dimensions(
    memories: tuple[OwnerTruthFormalMemory, ...],
) -> tuple[OwnerTruthPersonMemoryDimension, ...]:
    experience = tuple(item for item in memories if item.memory_kind == "experience")
    knowledge = tuple(item for item in memories if item.memory_kind == "knowledge")
    emotion = tuple(
        item
        for item in memories
        if item.memory_kind == "emotion" or _facet_values(item, "emotions")
    )
    relationships = tuple(
        item
        for item in memories
        if _facet_values(item, "people") or _facet_values(item, "relationships")
    )
    personality = tuple(item for item in memories if _facet_values(item, "personality"))
    values = tuple(item for item in memories if _facet_values(item, "values"))

    return (
        _dimension(
            key="lifeExperience",
            title="经历与人生轨迹",
            memories=experience,
            narrative=_experience_narrative(experience),
        ),
        _dimension(
            key="knowledgeAndSkills",
            title="知识与经验",
            memories=knowledge,
            narrative=_knowledge_narrative(knowledge),
        ),
        _dimension(
            key="emotionsAndAttachments",
            title="情感与牵挂",
            memories=emotion,
            narrative=_emotion_narrative(emotion),
        ),
        _dimension(
            key="importantRelationships",
            title="家庭与社会关系",
            memories=relationships,
            narrative=_relationship_narrative(relationships),
        ),
        _dimension(
            key="personality",
            title="性格特征",
            memories=personality,
            narrative=_facet_narrative(
                personality,
                facet="personality",
                prefix="这些记忆呈现出我的性格特征：",
            ),
        ),
        _dimension(
            key="valuesAndChoices",
            title="价值观与人生选择",
            memories=values,
            narrative=_facet_narrative(
                values,
                facet="values",
                prefix="贯穿这些经历的价值取向包括：",
            ),
        ),
    )


def _build_life_record(
    dimensions: tuple[OwnerTruthPersonMemoryDimension, ...],
) -> OwnerTruthPersonLifeRecord:
    by_key = {item.dimension: item for item in dimensions}
    paragraphs: list[str] = []

    for key in ("lifeExperience", "knowledgeAndSkills"):
        narrative = by_key[key].narrative
        if narrative:
            paragraphs.append(narrative)

    relationship_parts = [
        by_key[key].narrative
        for key in ("importantRelationships", "emotionsAndAttachments")
        if by_key[key].narrative
    ]
    if relationship_parts:
        paragraphs.append("".join(relationship_parts))

    identity_parts = [
        by_key[key].narrative
        for key in ("personality", "valuesAndChoices")
        if by_key[key].narrative
    ]
    if identity_parts:
        paragraphs.append("".join(identity_parts))

    return OwnerTruthPersonLifeRecord(
        title="我的人生记录",
        paragraphs=tuple(_unique(paragraphs)),
    )


def _build_life_story(
    memories: tuple[OwnerTruthFormalMemory, ...],
) -> OwnerTruthPersonLifeStory:
    if not memories:
        return OwnerTruthPersonLifeStory(
            title="我的人生记录",
            overview=None,
            chapters=(),
        )

    ordered = tuple(
        sorted(
            memories,
            key=lambda item: (item.current_version.created_at, item.memory_id),
        )
    )
    buckets: dict[str, dict[str, Any]] = {}
    for memory in ordered:
        key, title, priority = _chapter_identity(memory)
        bucket = buckets.setdefault(
            key,
            {"key": key, "title": title, "priority": priority, "memories": []},
        )
        bucket["memories"].append(memory)

    groups = [
        _LifeStoryChapterGroup(
            key=str(bucket["key"]),
            title=str(bucket["title"]),
            priority=int(bucket["priority"]),
            memories=tuple(bucket["memories"]),
        )
        for bucket in buckets.values()
    ]
    groups.sort(
        key=lambda item: (
            item.priority,
            item.memories[0].current_version.created_at,
            item.key,
        )
    )
    if len(groups) > _MAX_LIFE_STORY_CHAPTERS:
        visible = groups[: _MAX_LIFE_STORY_CHAPTERS - 1]
        overflow_memories = tuple(
            memory
            for group in groups[_MAX_LIFE_STORY_CHAPTERS - 1 :]
            for memory in group.memories
        )
        groups = [
            *visible,
            _LifeStoryChapterGroup(
                key="other-life-stories",
                title="更多人生片段",
                priority=99,
                memories=tuple(
                    sorted(
                        overflow_memories,
                        key=lambda item: (
                            item.current_version.created_at,
                            item.memory_id,
                        ),
                    )
                ),
            ),
        ]

    chapters = tuple(_life_story_chapter(group) for group in groups)
    supporting_ids = tuple(
        memory_id
        for chapter in chapters
        for memory_id in chapter.supporting_memory_ids
    )
    if len(supporting_ids) != len(set(supporting_ids)) or set(supporting_ids) != {
        item.memory_id for item in ordered
    }:
        raise OwnerTruthPersonMemoryProfileError(
            "life-story chapters must partition the current formal memories"
        )
    return OwnerTruthPersonLifeStory(
        title="我的人生记录",
        overview=_life_story_overview(chapters=chapters, memories=ordered),
        chapters=chapters,
    )


def _chapter_identity(memory: OwnerTruthFormalMemory) -> tuple[str, str, int]:
    people = _facet_values(memory, "people")
    relationships = _facet_values(memory, "relationships")
    places = _facet_values(memory, "places")
    time_labels = _memory_time_labels(memory)
    domains = _content_values(memory.current_version.content.get("domains"))
    searchable = " ".join(
        (
            _primary_text(memory),
            *people,
            *relationships,
            *places,
            *time_labels,
            *domains,
        )
    )

    if _contains_any(searchable, _FAMILY_KEYWORDS):
        return ("family-and-growth", "家庭与成长", 10)
    if _contains_any(searchable, _LEARNING_AND_WORK_KEYWORDS):
        return ("learning-and-work", "求学与工作", 20)
    if people:
        person = _title_fragment(people[0])
        return (f"person:{person}", f"与{person}的故事", 30)
    if memory.memory_kind == "knowledge":
        if domains:
            domain = _title_fragment(domains[0])
            return (f"wisdom:{domain}", f"{domain}中的经验", 40)
        return ("wisdom-and-beliefs", "经验与信念", 40)
    if memory.memory_kind == "emotion":
        return ("inner-world", "内心世界", 50)
    if places:
        place = _title_fragment(places[0])
        return (f"place:{place}", f"在{place}的日子", 60)
    if time_labels:
        period = _title_fragment(time_labels[0])
        return (f"period:{period}", f"{period}的时光", 70)
    return ("life-fragments", "人生片段", 80)


def _life_story_chapter(
    group: _LifeStoryChapterGroup,
) -> OwnerTruthPersonLifeStoryChapter:
    memories = group.memories
    visible_entries: list[tuple[str, OwnerTruthFormalMemory]] = []
    seen_texts: set[str] = set()
    for memory in memories:
        text = _primary_text(memory)
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        visible_entries.append((text, memory))
        if len(visible_entries) == _MAX_CHAPTER_MEMORIES_IN_TEXT:
            break

    paragraphs: list[str] = []
    paragraph_evidence: list[OwnerTruthPersonMemoryParagraphEvidence] = []

    def append_with_evidence(
        text: str | None,
        evidence_memories: Iterable[OwnerTruthFormalMemory],
    ) -> None:
        if not text or text in paragraphs:
            return
        evidence_items = tuple(evidence_memories)
        paragraph_evidence.append(
            OwnerTruthPersonMemoryParagraphEvidence(
                paragraph_index=len(paragraphs),
                supporting_memory_ids=tuple(item.memory_id for item in evidence_items),
                supporting_memory_version_ids=tuple(
                    item.current_version.version_id for item in evidence_items
                ),
            )
        )
        paragraphs.append(text)

    for index in range(0, len(visible_entries), 3):
        entry_group = visible_entries[index : index + 3]
        append_with_evidence(
            _paragraph(text for text, _ in entry_group),
            (memory for _, memory in entry_group),
        )

    reflection = _life_story_reflection(memories)
    append_with_evidence(reflection, memories)
    hidden_count = max(
        0,
        len(_unique(_primary_text(item) for item in memories)) - len(visible_entries),
    )
    if hidden_count:
        append_with_evidence(
            "还有一些相关片段保留在已确认记忆中，等待以后继续补充进这一章。",
            memories,
        )
    if not paragraphs:
        raise OwnerTruthPersonMemoryProfileError(
            "life-story chapter cannot be built without readable formal-memory content"
        )

    return OwnerTruthPersonLifeStoryChapter(
        chapter_id=f"chapter-{sha256(group.key.encode('utf-8')).hexdigest()[:24]}",
        title=group.title,
        paragraphs=tuple(paragraphs),
        supporting_memory_ids=tuple(item.memory_id for item in memories),
        supporting_memory_version_ids=tuple(
            item.current_version.version_id for item in memories
        ),
        paragraph_evidence=tuple(paragraph_evidence),
    )


def _life_story_reflection(
    memories: Iterable[OwnerTruthFormalMemory],
) -> str | None:
    items = tuple(memories)
    people = _all_facets(items, "people")
    places = _all_facets(items, "places")
    emotions = _all_facets(items, "emotions")
    values = _all_facets(items, "values")
    personality = _all_facets(items, "personality")
    sentences: list[str] = []
    if people and places:
        sentences.append(
            f"这些故事与{'、'.join(people[:3])}相连，也留在了{'、'.join(places[:3])}"
        )
    elif people:
        sentences.append(f"{'、'.join(people[:3])}是这些故事里重要的人")
    elif places:
        sentences.append(f"这些片段留在了{'、'.join(places[:3])}")
    if emotions:
        sentences.append(f"回想起来，我仍会感到{'、'.join(emotions[:3])}")
    traits = _first_person_trait_phrase(values=values[:3], personality=personality[:3])
    if traits:
        sentences.append(traits)
    return _paragraph(sentences)


def _life_story_overview(
    *,
    chapters: tuple[OwnerTruthPersonLifeStoryChapter, ...],
    memories: tuple[OwnerTruthFormalMemory, ...],
) -> str:
    titles = [chapter.title for chapter in chapters]
    if len(titles) == 1:
        opening = f"我的人生记录目前从“{titles[0]}”这一章展开"
    else:
        visible_titles = titles[:4]
        joined = "、".join(visible_titles[:-1]) + f"和{visible_titles[-1]}"
        suffix = "等篇章" if len(titles) > len(visible_titles) else "这些篇章"
        opening = f"回望一路走来的片段，我的故事从{joined}{suffix}展开"

    people = _all_facets(memories, "people")
    values = _all_facets(memories, "values")
    personality = _all_facets(memories, "personality")
    sentences = [opening]
    if people:
        sentences.append(f"{'、'.join(people[:4])}是故事中反复出现的重要人物")
    traits = _first_person_trait_phrase(values=values[:4], personality=personality[:4])
    if traits:
        sentences.append(traits)
    return _paragraph(sentences) or "我的人生记录正在慢慢展开。"


def _memory_time_labels(memory: OwnerTruthFormalMemory) -> tuple[str, ...]:
    labels = list(_facet_values(memory, "time"))
    raw_time = memory.current_version.content.get("time")
    if isinstance(raw_time, Mapping):
        labels.extend(
            normalized
            for field in ("start", "end")
            if (normalized := _normalize_text(raw_time.get(field)))
        )
    return tuple(_unique(labels))


def _contains_any(value: str, keywords: Iterable[str]) -> bool:
    return any(keyword in value for keyword in keywords)


def _title_fragment(value: str) -> str:
    normalized = _normalize_text(value).strip("，。！？；：,.!?;:、 ")
    return normalized[:20] or "那段经历"


def _dimension(
    *,
    key: str,
    title: str,
    memories: Iterable[OwnerTruthFormalMemory],
    narrative: str | None,
) -> OwnerTruthPersonMemoryDimension:
    supporting_ids = tuple(
        dict.fromkeys(item.memory_id for item in memories)
    )
    return OwnerTruthPersonMemoryDimension(
        dimension=key,
        title=title,
        narrative=narrative,
        supporting_memory_ids=supporting_ids,
    )


def _experience_narrative(memories: Iterable[OwnerTruthFormalMemory]) -> str | None:
    items = tuple(memories)
    if not items:
        return None
    people = _all_facets(items, "people")
    places = _all_facets(items, "places")
    times = _all_facets(items, "time")
    values = _all_facets(items, "values")
    personality = _all_facets(items, "personality")
    experiences = _unique(_primary_text(item) for item in items)
    sentences: list[str] = []
    if experiences:
        sentences.append(f"我的人生经历中，{_joined_sentences(experiences, limit=4)}")
    if people and places:
        sentences.append(
            f"我的一些重要经历与{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}相连，"
            f"{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}是这些记忆中反复出现的生活场景"
        )
    elif people:
        sentences.append(
            f"{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}是我人生经历中的重要人物"
        )
    elif places:
        sentences.append(
            f"我的人生记忆常发生在{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}"
        )
    if times:
        sentences.append(
            f"这些经历留下了{'、'.join(times[:_MAX_DIMENSION_EVIDENCE])}等时间线索"
        )
    traits = _first_person_trait_phrase(values=values, personality=personality)
    if traits:
        sentences.append(traits)
    return _paragraph(sentences)


def _knowledge_narrative(memories: Iterable[OwnerTruthFormalMemory]) -> str | None:
    items = tuple(memories)
    if not items:
        return None
    domains = _unique(
        value
        for item in items
        for value in _content_values(item.current_version.content.get("domains"))
    )
    values = _all_facets(items, "values")
    personality = _all_facets(items, "personality")
    statements = _unique(_primary_text(item) for item in items)
    sentences: list[str] = []
    if domains:
        sentences.append(
            f"我积累的知识和经验主要涉及{'、'.join(domains[:_MAX_DIMENSION_EVIDENCE])}"
        )
    if statements:
        sentences.append(f"我常用的方法和判断是：{_joined_sentences(statements, limit=3)}")
    traits = _first_person_trait_phrase(values=values, personality=personality)
    if traits:
        sentences.append(traits)
    return _paragraph(sentences)


def _emotion_narrative(memories: Iterable[OwnerTruthFormalMemory]) -> str | None:
    items = tuple(memories)
    emotions = _all_facets(items, "emotions")
    people = _all_facets(items, "people")
    places = _all_facets(items, "places")
    parts: list[str] = []
    if emotions:
        parts.append(f"我反复提到的感受有{'、'.join(emotions[:_MAX_DIMENSION_EVIDENCE])}")
    if people:
        parts.append(f"这些情感多与{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}相连")
    if places:
        parts.append(f"相关记忆常发生在{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}")
    if not parts:
        expressions = _unique(
            _primary_text(item) for item in items if item.memory_kind == "emotion"
        )
        if expressions:
            parts.append(_joined_sentences(expressions, limit=2))
    return _paragraph(parts)


def _relationship_narrative(memories: Iterable[OwnerTruthFormalMemory]) -> str | None:
    items = tuple(memories)
    people = _all_facets(items, "people")
    relationships = _all_facets(items, "relationships")
    places = _all_facets(items, "places")
    parts: list[str] = []
    if people:
        parts.append(f"{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}是我记忆中的重要人物")
    if relationships:
        parts.append(f"这些记忆包含{'、'.join(relationships[:_MAX_DIMENSION_EVIDENCE])}等关系")
    if places:
        parts.append(f"彼此相处的场景常与{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}有关")
    if not parts:
        contexts = _unique(_primary_text(item) for item in items)
        if contexts:
            parts.append(_joined_sentences(contexts, limit=2))
    return _paragraph(parts)


def _facet_narrative(
    memories: Iterable[OwnerTruthFormalMemory],
    *,
    facet: str,
    prefix: str,
) -> str | None:
    values = _all_facets(memories, facet)
    if not values:
        return None
    return f"{prefix}{'、'.join(values[:_MAX_DIMENSION_EVIDENCE])}。"


def _all_facets(
    memories: Iterable[OwnerTruthFormalMemory],
    facet: str,
) -> list[str]:
    return _unique(value for item in memories for value in _facet_values(item, facet))


def _content_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for item in value:
        candidate = item.get("value") if isinstance(item, Mapping) else item
        normalized = _normalize_text(candidate)
        if normalized:
            result.append(normalized)
    return tuple(_unique(result))


def _first_person_trait_phrase(*, values: list[str], personality: list[str]) -> str | None:
    traits: list[str] = []
    if values:
        traits.append(f"我看重{'、'.join(values[:_MAX_DIMENSION_EVIDENCE])}")
    if personality:
        traits.append(f"这些经历也呈现出我{'、'.join(personality[:_MAX_DIMENSION_EVIDENCE])}的一面")
    return "，".join(traits) if traits else None


def _primary_text(memory: OwnerTruthFormalMemory) -> str:
    content = memory.current_version.content
    keys = {
        "experience": ("event", "summary"),
        "knowledge": ("statement", "claim"),
        "emotion": ("expression", "label", "emotion"),
    }.get(memory.memory_kind, ())
    for key in (*keys, "title", "text"):
        value = content.get(key)
        if isinstance(value, str) and _normalize_text(value):
            return _normalize_text(value)
    return ""


def _facet_values(memory: OwnerTruthFormalMemory, facet: str) -> tuple[str, ...]:
    facets = memory.current_version.content.get("facets")
    if not isinstance(facets, Mapping):
        return ()
    raw_values = facets.get(facet)
    if not isinstance(raw_values, (list, tuple)):
        return ()
    values: list[str] = []
    for raw_value in raw_values:
        if isinstance(raw_value, Mapping):
            value = raw_value.get("value")
        else:
            value = raw_value
        normalized = _normalize_text(value)
        if normalized:
            values.append(normalized)
    return tuple(_unique(values))


def _joined_sentences(values: Iterable[str], *, limit: int) -> str:
    clauses = _unique(values)
    visible = [value.rstrip("。！？!?；;") for value in clauses[:limit]]
    rendered = "。".join(visible)
    hidden_count = len(clauses) - len(visible)
    if hidden_count:
        rendered += f"。其余 {hidden_count} 段记忆仍保留在原始记录中"
    return rendered


def _paragraph(sentences: Iterable[str]) -> str | None:
    normalized = [
        sentence.rstrip("。！？!?；;")
        for sentence in _unique(sentences)
        if sentence
    ]
    return "。".join(normalized) + "。" if normalized else None


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_text(value: Any) -> str:
    normalized = _WHITESPACE.sub(" ", str(value or "")).strip()
    if len(normalized) > _MAX_CLAUSE_CHARACTERS:
        return normalized[: _MAX_CLAUSE_CHARACTERS - 1].rstrip() + "…"
    return normalized


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PERSON_LIFE_RECORD_ALGORITHM_VERSION",
    "PERSON_LIFE_RECORD_SCHEMA_VERSION",
    "PERSON_LIFE_STORY_ALGORITHM_VERSION",
    "PERSON_LIFE_STORY_SCHEMA_VERSION",
    "PERSON_MEMORY_PROFILE_ALGORITHM_VERSION",
    "PERSON_MEMORY_PROFILE_SCHEMA_VERSION",
    "OwnerTruthPersonMemoryDimension",
    "OwnerTruthPersonMemoryParagraphEvidence",
    "OwnerTruthPersonMemoryProfile",
    "OwnerTruthPersonMemoryProfileError",
    "OwnerTruthPersonMemoryProfileService",
    "OwnerTruthPersonLifeRecord",
    "OwnerTruthPersonLifeStory",
    "OwnerTruthPersonLifeStoryChapter",
]

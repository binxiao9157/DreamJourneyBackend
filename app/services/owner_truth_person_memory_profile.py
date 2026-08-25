"""Owner-only person-memory profile derived from current formal memories.

The profile is a rebuildable read model, never a second source of truth.  It
turns the complete current ``MemoryVersion`` set into one narrative per stable
person-memory dimension while retaining opaque supporting Memory ids for
drill-down and audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemory,
    OwnerTruthFormalMemoryCursor,
    OwnerTruthFormalMemoryError,
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)


PERSON_MEMORY_PROFILE_SCHEMA_VERSION = "owner-truth-person-memory-profile-v1"
PERSON_MEMORY_PROFILE_ALGORITHM_VERSION = "person-memory-dimension-summary-v1"
_MAX_FORMAL_MEMORY_COUNT = 10_000
_MAX_DIMENSION_EVIDENCE = 8
_MAX_CLAUSE_CHARACTERS = 280
_WHITESPACE = re.compile(r"\s+")


class OwnerTruthPersonMemoryProfileError(OwnerTruthFormalMemoryError):
    """A person-memory profile cannot be derived safely."""


@dataclass(frozen=True)
class OwnerTruthPersonMemoryDimension:
    dimension: str
    title: str
    narrative: str | None
    supporting_memory_ids: tuple[str, ...]

    def public_contract(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "title": self.title,
            "status": "ready" if self.narrative else "empty",
            "narrative": self.narrative,
            "supportingMemoryCount": len(self.supporting_memory_ids),
            "supportingMemoryIds": list(self.supporting_memory_ids),
        }


@dataclass(frozen=True)
class OwnerTruthPersonMemoryProfile:
    vault_id: str
    profile_version: str
    updated_at: str | None
    memory_count: int
    dimensions: tuple[OwnerTruthPersonMemoryDimension, ...]

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": PERSON_MEMORY_PROFILE_SCHEMA_VERSION,
            "algorithmVersion": PERSON_MEMORY_PROFILE_ALGORITHM_VERSION,
            "state": "ready" if self.memory_count else "empty",
            "vaultId": self.vault_id,
            "profileVersion": self.profile_version,
            "updatedAt": self.updated_at,
            "memoryCount": self.memory_count,
            "dimensions": [item.public_contract() for item in self.dimensions],
        }


class OwnerTruthPersonMemoryProfileService:
    """Build a bounded dimension-level profile from all current memories."""

    def __init__(self, store: Any) -> None:
        self._formal_memory = OwnerTruthFormalMemoryService(store)

    def read(self, *, context: OwnerTruthCommandContext) -> OwnerTruthPersonMemoryProfile:
        memories = self._all_current_memories(context=context)
        dimensions = _build_dimensions(memories)
        profile_version = _digest(
            {
                "schemaVersion": PERSON_MEMORY_PROFILE_SCHEMA_VERSION,
                "algorithmVersion": PERSON_MEMORY_PROFILE_ALGORITHM_VERSION,
                "inputs": [
                    {
                        "memoryId": item.memory_id,
                        "memoryVersionId": item.current_version.version_id,
                        "contentHash": item.current_version.content_hash,
                    }
                    for item in sorted(memories, key=lambda value: value.memory_id)
                ],
                "dimensions": [item.public_contract() for item in dimensions],
            }
        )
        return OwnerTruthPersonMemoryProfile(
            vault_id=context.vault_id,
            profile_version=profile_version,
            updated_at=memories[0].current_version.created_at if memories else None,
            memory_count=len(memories),
            dimensions=dimensions,
        )

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
                prefix="本人已确认的性格特征包括：",
                context_prefix="这些特征主要体现在：",
            ),
        ),
        _dimension(
            key="valuesAndChoices",
            title="价值观与人生选择",
            memories=values,
            narrative=_facet_narrative(
                values,
                facet="values",
                prefix="本人已确认的价值取向包括：",
                context_prefix="这些价值主要体现在：",
            ),
        ),
    )


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
    parts = [f"目前的人生轨迹由 {len(items)} 段已确认经历共同勾勒"]
    anchors = _anchor_phrase(people=people, places=places, times=times)
    if anchors:
        parts.append(anchors)
    traits = _trait_phrase(values=values, personality=personality)
    if traits:
        parts.append(traits)
    if len(parts) == 1:
        contexts = _unique(_primary_text(item) for item in items)
        if contexts:
            parts.append(f"当前可归纳出的核心经历是：{_joined_clauses(contexts, limit=3)}")
    return "；".join(parts) + "。"


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
    parts = [f"已从 {len(items)} 段正式记忆中沉淀出可复用的知识、经验与判断"]
    if domains:
        parts.append(f"主要涉及{'、'.join(domains[:_MAX_DIMENSION_EVIDENCE])}")
    if statements:
        parts.append(f"核心方法可归纳为：{_joined_clauses(statements, limit=3)}")
    traits = _trait_phrase(values=values, personality=personality)
    if traits:
        parts.append(traits)
    return "；".join(parts) + "。"


def _emotion_narrative(memories: Iterable[OwnerTruthFormalMemory]) -> str | None:
    items = tuple(memories)
    emotions = _all_facets(items, "emotions")
    people = _all_facets(items, "people")
    places = _all_facets(items, "places")
    parts: list[str] = []
    if emotions:
        parts.append(f"情感重心主要集中在{'、'.join(emotions[:_MAX_DIMENSION_EVIDENCE])}")
    if people:
        parts.append(f"这些感受主要与{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}相关")
    if places:
        parts.append(f"相关记忆场景包括{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}")
    if not parts:
        expressions = _unique(
            _primary_text(item) for item in items if item.memory_kind == "emotion"
        )
        if expressions:
            parts.append(f"当前确认的情感脉络是：{_joined_clauses(expressions, limit=2)}")
    return "；".join(parts) + "。" if parts else None


def _relationship_narrative(memories: Iterable[OwnerTruthFormalMemory]) -> str | None:
    items = tuple(memories)
    people = _all_facets(items, "people")
    relationships = _all_facets(items, "relationships")
    places = _all_facets(items, "places")
    parts: list[str] = []
    if people:
        parts.append(f"重要关系网络围绕{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}展开")
    if relationships:
        parts.append(f"已确认的关系类型包括{'、'.join(relationships[:_MAX_DIMENSION_EVIDENCE])}")
    if places:
        parts.append(f"这些关系主要出现在{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}等生活场景")
    if not parts:
        contexts = _unique(_primary_text(item) for item in items)
        if contexts:
            parts.append(f"当前可归纳的关系脉络是：{_joined_clauses(contexts, limit=2)}")
    return "；".join(parts) + "。" if parts else None


def _facet_narrative(
    memories: Iterable[OwnerTruthFormalMemory],
    *,
    facet: str,
    prefix: str,
    context_prefix: str,
) -> str | None:
    items = tuple(memories)
    values = _all_facets(items, facet)
    if not values:
        return None
    narrative = f"{prefix}{'、'.join(values[:_MAX_DIMENSION_EVIDENCE])}"
    narrative += f"；{context_prefix}{len(items)} 段已确认正式记忆"
    return narrative + "。"


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


def _anchor_phrase(
    *,
    people: list[str],
    places: list[str],
    times: list[str],
) -> str | None:
    anchors: list[str] = []
    if people:
        anchors.append(f"重要人物为{'、'.join(people[:_MAX_DIMENSION_EVIDENCE])}")
    if places:
        anchors.append(f"主要场景为{'、'.join(places[:_MAX_DIMENSION_EVIDENCE])}")
    if times:
        anchors.append(f"时间线索为{'、'.join(times[:_MAX_DIMENSION_EVIDENCE])}")
    return "，".join(anchors) if anchors else None


def _trait_phrase(*, values: list[str], personality: list[str]) -> str | None:
    traits: list[str] = []
    if values:
        traits.append(f"体现{'、'.join(values[:_MAX_DIMENSION_EVIDENCE])}的价值取向")
    if personality:
        traits.append(f"呈现{'、'.join(personality[:_MAX_DIMENSION_EVIDENCE])}的性格特征")
    return "，并".join(traits) if traits else None


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


def _joined_clauses(values: Iterable[str], *, limit: int = _MAX_DIMENSION_EVIDENCE) -> str:
    clauses = _unique(values)
    visible = clauses[:limit]
    rendered = "；".join(value.rstrip("。！？!?；;") for value in visible)
    hidden_count = len(clauses) - len(visible)
    return f"{rendered}；以及另外 {hidden_count} 段已确认记忆" if hidden_count else rendered


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
    "PERSON_MEMORY_PROFILE_ALGORITHM_VERSION",
    "PERSON_MEMORY_PROFILE_SCHEMA_VERSION",
    "OwnerTruthPersonMemoryDimension",
    "OwnerTruthPersonMemoryProfile",
    "OwnerTruthPersonMemoryProfileError",
    "OwnerTruthPersonMemoryProfileService",
]

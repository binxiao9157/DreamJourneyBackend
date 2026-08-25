"""Owner-only person-memory profile derived from current formal memories.

The profile is a rebuildable read model, never a second source of truth.  It
turns the complete current ``MemoryVersion`` set into one continuous plain-text
life record. Stable dimensions remain internal evidence indexes for search and
audit; they are not the primary reading experience.
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
PERSON_LIFE_RECORD_SCHEMA_VERSION = "owner-truth-person-life-record-v1"
PERSON_LIFE_RECORD_ALGORITHM_VERSION = "person-life-record-plain-text-v1"
PERSON_LIFE_STORY_SCHEMA_VERSION = "owner-truth-person-life-story-v1"
PERSON_LIFE_STORY_ALGORITHM_VERSION = "person-life-story-chapter-projection-v1"
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
    life_record: "OwnerTruthPersonLifeRecord"
    life_story: "OwnerTruthPersonLifeStory"
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
            "lifeRecord": self.life_record.public_contract(),
            "lifeStory": self.life_story.public_contract(),
            "dimensions": [item.public_contract() for item in self.dimensions],
        }


@dataclass(frozen=True)
class OwnerTruthPersonLifeRecord:
    title: str
    paragraphs: tuple[str, ...]

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
        }


@dataclass(frozen=True)
class OwnerTruthPersonLifeStoryChapter:
    chapter_id: str
    title: str
    paragraphs: tuple[str, ...]
    supporting_memory_ids: tuple[str, ...]
    supporting_memory_version_ids: tuple[str, ...]

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
        }


@dataclass(frozen=True)
class OwnerTruthPersonLifeStory:
    title: str
    overview: str | None
    chapters: tuple[OwnerTruthPersonLifeStoryChapter, ...]

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
        self._formal_memory = OwnerTruthFormalMemoryService(store)

    def read(self, *, context: OwnerTruthCommandContext) -> OwnerTruthPersonMemoryProfile:
        memories = self._all_current_memories(context=context)
        dimensions = _build_dimensions(memories)
        life_record = _build_life_record(dimensions)
        life_story = _build_life_story(memories)
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
                "lifeRecord": life_record.public_contract(),
                "lifeStory": life_story.public_contract(),
                "dimensions": [item.public_contract() for item in dimensions],
            }
        )
        return OwnerTruthPersonMemoryProfile(
            vault_id=context.vault_id,
            profile_version=profile_version,
            updated_at=memories[0].current_version.created_at if memories else None,
            memory_count=len(memories),
            life_record=life_record,
            life_story=life_story,
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
    visible_texts = _unique(_primary_text(item) for item in memories)[
        :_MAX_CHAPTER_MEMORIES_IN_TEXT
    ]
    paragraphs = [
        _paragraph(visible_texts[index : index + 3])
        for index in range(0, len(visible_texts), 3)
    ]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]

    reflection = _life_story_reflection(memories)
    if reflection and reflection not in paragraphs:
        paragraphs.append(reflection)
    hidden_count = max(0, len(_unique(_primary_text(item) for item in memories)) - len(visible_texts))
    if hidden_count:
        paragraphs.append("还有一些相关片段保留在已确认记忆中，等待以后继续补充进这一章。")
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
    "OwnerTruthPersonMemoryProfile",
    "OwnerTruthPersonMemoryProfileError",
    "OwnerTruthPersonMemoryProfileService",
    "OwnerTruthPersonLifeRecord",
    "OwnerTruthPersonLifeStory",
    "OwnerTruthPersonLifeStoryChapter",
]

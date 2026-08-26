"""Evidence-bound person-memory projections derived from formal MemoryVersions.

The projection deliberately separates four concerns:

* ``formalMemories`` is a normalized, multi-facet view of authoritative data;
* ``cognitiveProjection`` supports retrieval and cautiously labelled inference;
* ``relationshipProjection`` preserves entities, relations and event time;
* ``biographyProjection`` is the versioned, human-readable life document.

Every item remains traceable to current MemoryVersion identifiers. None of the
derived objects is allowed to become a second source of truth.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping

from .contracts import MemoryKind
from .ontology import (
    OWNER_TRUTH_FACET_NAMES,
    OWNER_TRUTH_SEMANTIC_FACETS,
    enrich_memory_payload_v4,
)


PERSON_MEMORY_MODEL_SCHEMA_VERSION = "owner-truth-person-memory-model-v1"
PERSON_COGNITIVE_PROJECTION_SCHEMA_VERSION = "owner-truth-cognitive-projection-v1"
PERSON_RELATIONSHIP_PROJECTION_SCHEMA_VERSION = "owner-truth-relationship-projection-v1"
PERSON_BIOGRAPHY_PROJECTION_SCHEMA_VERSION = "owner-truth-biography-projection-v1"
PERSON_MEMORY_MODEL_ALGORITHM_VERSION = "evidence-bound-person-model-v1"

_WHITESPACE = re.compile(r"\s+")
_MAX_TEXT = 1_200
_MAX_SECTION_COUNT = 10
_MAX_BLOCK_MEMORIES = 3

_FAMILY_TERMS = (
    "家人", "家庭", "父亲", "母亲", "爸爸", "妈妈", "爷爷", "奶奶",
    "祖父", "祖母", "外祖", "兄弟", "姐妹", "丈夫", "妻子", "爱人",
    "孩子", "儿子", "女儿", "亲子", "童年", "小时候", "老家", "成长",
)
_LEARNING_WORK_TERMS = (
    "学校", "上学", "求学", "读书", "大学", "老师", "同学", "毕业",
    "工作", "公司", "职业", "职场", "项目", "创业", "专业", "同事",
    "客户", "岗位", "退休",
)

_DIMENSION_DEFINITIONS = (
    ("lifeEvent", "经历与人生轨迹"),
    ("knowledge", "知识与经验"),
    ("emotion", "情感与牵挂"),
    ("relationship", "家庭与社会关系"),
    ("personality", "性格特征"),
    ("value", "价值观与人生选择"),
    ("habit", "习惯与偏好"),
    ("goal", "目标与愿望"),
    ("identity", "身份与角色"),
    ("reflection", "反思与人生理解"),
)


class PersonMemoryModelError(ValueError):
    """The authoritative input set cannot produce a safe projection."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PersonMemoryModelError("person memory values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, *, maximum: int = _MAX_TEXT) -> str:
    normalized = _WHITESPACE.sub(" ", str(value or "")).strip()
    return normalized[:maximum]


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _facet_entries(content: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    facets = content.get("facets")
    if not isinstance(facets, Mapping):
        return []
    entries = facets.get(name)
    if not isinstance(entries, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        value = _text(entry.get("value"), maximum=256)
        if not value:
            continue
        result.append(
            {
                "value": value,
                "evidenceMode": _text(entry.get("evidenceMode")) or "ownerStated",
                "confidence": float(entry.get("confidence") or 0.0),
            }
        )
    return result


def _citation(entry: Mapping[str, Any]) -> dict[str, Any]:
    citation = {
        "memoryId": _text(entry.get("memoryId")),
        "memoryVersionId": _text(entry.get("memoryVersionId")),
        "contentHash": _text(entry.get("contentHash")),
    }
    source_id = _text(entry.get("sourceId"))
    source_version = entry.get("sourceVersion")
    if source_id:
        citation["sourceId"] = source_id
    if isinstance(source_version, int) and not isinstance(source_version, bool):
        citation["sourceVersion"] = source_version
    return citation


def _normalize_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    memory_id = _text(entry.get("memoryId"))
    version_id = _text(entry.get("memoryVersionId"))
    if not memory_id or not version_id:
        raise PersonMemoryModelError("formal memory identity is required")
    try:
        kind = MemoryKind(_text(entry.get("memoryKind")))
    except ValueError as exc:
        raise PersonMemoryModelError("formal memory kind is unsupported") from exc
    content = entry.get("content")
    if not isinstance(content, Mapping):
        raise PersonMemoryModelError("formal memory content must be an object")
    enriched = enrich_memory_payload_v4(kind=kind, payload=content)
    semantic = enriched["semantic"]
    evidence_refs = entry.get("evidenceRefs")
    if not isinstance(evidence_refs, (list, tuple)):
        evidence_refs = []
    return {
        "memoryId": memory_id,
        "memoryVersionId": version_id,
        "memoryVersion": int(entry.get("memoryVersion") or 1),
        "contentHash": _text(entry.get("contentHash")),
        "contentSchemaVersion": _text(entry.get("contentSchemaVersion")),
        "primaryKind": semantic["primaryKind"],
        "facets": list(semantic["facets"]),
        "title": semantic["title"],
        "narrative": semantic["narrative"],
        "eventTime": semantic["eventTime"],
        "entities": list(semantic["entities"]),
        "emotionEvidence": list(semantic["emotionEvidence"]),
        "facetEvidence": {
            name: _facet_entries(enriched, name) for name in OWNER_TRUTH_FACET_NAMES
        },
        "epistemicStatus": _text(entry.get("epistemicStatus")) or "uncertain",
        "sensitivity": _text(entry.get("sensitivity")) or "standard",
        "evidenceRefs": [dict(value) for value in evidence_refs if isinstance(value, Mapping)],
        "citation": _citation(entry),
    }


def _projection_item(record: Mapping[str, Any], *, item_kind: str) -> dict[str, Any]:
    return {
        "itemId": f"item-{_digest([item_kind, record['memoryVersionId']])[:24]}",
        "itemKind": item_kind,
        "statement": record["narrative"],
        "epistemicStatus": record["epistemicStatus"],
        "confidence": 1.0,
        "facets": list(record["facets"]),
        "evidence": [record["citation"]],
    }


def _cognitive_projection(records: list[dict[str, Any]]) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    experiences: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for record in records:
        primary = record["primaryKind"]
        if primary == "knowledge":
            facts.append(_projection_item(record, item_kind="fact"))
        elif primary == "lifeEvent":
            experiences.append(_projection_item(record, item_kind="experience"))
        else:
            observations.append(_projection_item(record, item_kind="observation"))

    recurring: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        for facet_name in ("values", "personality", "habits", "goals", "identity"):
            for evidence in record["facetEvidence"].get(facet_name, []):
                recurring.setdefault((facet_name, evidence["value"]), []).append(record)
    mental_models: list[dict[str, Any]] = []
    for (facet_name, value), supporting in sorted(recurring.items()):
        unique_support = {
            record["memoryVersionId"]: record for record in supporting
        }
        if len(unique_support) < 2:
            continue
        label = {
            "values": "价值取向",
            "personality": "性格倾向",
            "habits": "行为习惯",
            "goals": "持续目标",
            "identity": "身份认同",
        }[facet_name]
        mental_models.append(
            {
                "itemId": f"model-{_digest([facet_name, value])[:24]}",
                "itemKind": "mentalModel",
                "statement": f"多条已确认记忆反复呈现出“{value}”这一{label}。",
                "epistemicStatus": "inferred",
                "confidence": min(0.95, 0.55 + 0.1 * len(unique_support)),
                "facets": [
                    {
                        "values": "value",
                        "personality": "personality",
                        "habits": "habit",
                        "goals": "goal",
                        "identity": "identity",
                    }[facet_name]
                ],
                "evidence": [record["citation"] for record in unique_support.values()],
            }
        )
    return {
        "schemaVersion": PERSON_COGNITIVE_PROJECTION_SCHEMA_VERSION,
        "facts": facts,
        "experiences": experiences,
        "observations": observations,
        "mentalModels": mental_models,
    }


def _entity_id(entity_type: str, name: str) -> str:
    return f"entity-{_digest([entity_type, name])[:24]}"


def _relationship_projection(records: list[dict[str, Any]]) -> dict[str, Any]:
    owner_id = "entity-owner"
    entities: dict[str, dict[str, Any]] = {
        owner_id: {
            "entityId": owner_id,
            "entityType": "self",
            "name": "自己",
            "evidence": [],
        }
    }
    relations: list[dict[str, Any]] = []
    seen_relations: set[tuple[str, str, str, str]] = set()
    for record in records:
        people = record["facetEvidence"].get("people", [])
        places = record["facetEvidence"].get("places", [])
        relationship_labels = _unique(
            item["value"] for item in record["facetEvidence"].get("relationships", [])
        ) or ["相关人物"]
        for entity in [*people, *places]:
            entity_type = "person" if entity in people else "place"
            entity_id = _entity_id(entity_type, entity["value"])
            current = entities.setdefault(
                entity_id,
                {
                    "entityId": entity_id,
                    "entityType": entity_type,
                    "name": entity["value"],
                    "evidence": [],
                },
            )
            if record["citation"] not in current["evidence"]:
                current["evidence"].append(record["citation"])
            relation_types = relationship_labels if entity_type == "person" else ["发生于"]
            for relation_type in relation_types:
                key = (owner_id, entity_id, relation_type, record["memoryVersionId"])
                if key in seen_relations:
                    continue
                seen_relations.add(key)
                relations.append(
                    {
                        "relationId": f"relation-{_digest(key)[:24]}",
                        "fromEntityId": owner_id,
                        "toEntityId": entity_id,
                        "relationType": relation_type,
                        "validAt": record["eventTime"],
                        "invalidAt": None,
                        "epistemicStatus": record["epistemicStatus"],
                        "confidence": float(entity.get("confidence") or 0.0),
                        "evidence": [record["citation"]],
                    }
                )
    return {
        "schemaVersion": PERSON_RELATIONSHIP_PROJECTION_SCHEMA_VERSION,
        "entities": sorted(entities.values(), key=lambda value: value["entityId"]),
        "relations": sorted(relations, key=lambda value: value["relationId"]),
    }


def _searchable(record: Mapping[str, Any]) -> str:
    values = [record["narrative"]]
    for entries in record["facetEvidence"].values():
        values.extend(item["value"] for item in entries)
    return " ".join(values)


def _section_identity(record: Mapping[str, Any]) -> tuple[str, str, int]:
    searchable = _searchable(record)
    facets = set(record["facets"])
    if "relationship" in facets or any(term in searchable for term in _FAMILY_TERMS):
        return ("roots-and-relationships", "根脉与重要的人", 10)
    if any(term in searchable for term in _LEARNING_WORK_TERMS):
        return ("learning-and-work", "求学、工作与成长", 20)
    if facets.intersection({"identity", "goal"}):
        return ("identity-and-aspiration", "身份、选择与愿望", 30)
    if facets.intersection({"emotion", "value", "personality", "habit"}):
        return ("inner-world", "内心、性格与坚持", 40)
    if facets.intersection({"knowledge", "reflection"}):
        return ("wisdom-and-reflection", "经验、知识与理解", 50)
    return ("life-fragments", "人生片段", 60)


def _sentences(records: list[dict[str, Any]]) -> list[str]:
    return _unique(record["narrative"].rstrip("。！？!?；;") for record in records)


def _block_text(records: list[dict[str, Any]], *, block_index: int) -> str:
    sentences = _sentences(records)
    if not sentences:
        raise PersonMemoryModelError("biography block has no readable memory")
    opening = "在我的人生记录里，" if block_index == 0 else "后来，"
    body = "。".join(sentences)
    people = _unique(
        item["value"]
        for record in records
        for item in record["facetEvidence"].get("people", [])
    )
    values = _unique(
        item["value"]
        for record in records
        for item in record["facetEvidence"].get("values", [])
    )
    closing: list[str] = []
    if people:
        closing.append(f"{'、'.join(people[:3])}也因此成为这段生命经历里重要的人")
    if values:
        closing.append(f"这些片段让我一直看重{'、'.join(values[:3])}")
    return opening + body + "。" + ("。".join(closing) + "。" if closing else "")


def _biography_projection(records: list[dict[str, Any]], source_fingerprint: str) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        key, title, priority = _section_identity(record)
        bucket = buckets.setdefault(
            key,
            {"sectionId": key, "title": title, "priority": priority, "records": []},
        )
        bucket["records"].append(record)
    ordered = sorted(buckets.values(), key=lambda value: (value["priority"], value["sectionId"]))
    if len(ordered) > _MAX_SECTION_COUNT:
        overflow = [record for value in ordered[_MAX_SECTION_COUNT - 1 :] for record in value["records"]]
        ordered = [
            *ordered[: _MAX_SECTION_COUNT - 1],
            {
                "sectionId": "more-life-stories",
                "title": "更多人生片段",
                "priority": 99,
                "records": overflow,
            },
        ]

    sections: list[dict[str, Any]] = []
    for section in ordered:
        section_records = section["records"]
        blocks: list[dict[str, Any]] = []
        for index in range(0, len(section_records), _MAX_BLOCK_MEMORIES):
            chunk = section_records[index : index + _MAX_BLOCK_MEMORIES]
            blocks.append(
                {
                    "blockId": f"block-{_digest([item['memoryVersionId'] for item in chunk])[:24]}",
                    "blockType": "narrative",
                    "text": _block_text(chunk, block_index=len(blocks)),
                    "facets": [
                        facet
                        for facet in OWNER_TRUTH_SEMANTIC_FACETS
                        if any(facet in item["facets"] for item in chunk)
                    ],
                    "evidence": [item["citation"] for item in chunk],
                }
            )
        sections.append(
            {
                "sectionId": section["sectionId"],
                "title": section["title"],
                "blocks": blocks,
                "evidence": [item["citation"] for item in section_records],
            }
        )

    people = Counter(
        item["value"]
        for record in records
        for item in record["facetEvidence"].get("people", [])
    )
    values = Counter(
        item["value"]
        for record in records
        for item in record["facetEvidence"].get("values", [])
    )
    overview_parts = ["这是一份由我亲自确认的记忆持续整理而成的人生记录"]
    if people:
        overview_parts.append(
            f"{'、'.join(value for value, _ in people.most_common(4))}是故事中反复出现的重要人物"
        )
    if values:
        overview_parts.append(
            f"{'、'.join(value for value, _ in values.most_common(4))}构成了我许多选择背后的坚持"
        )
    overview = "。".join(overview_parts) + "。" if records else None
    document_material = {
        "sourceFingerprint": source_fingerprint,
        "title": "我的人生记录",
        "overview": overview,
        "sections": sections,
    }
    return {
        "schemaVersion": PERSON_BIOGRAPHY_PROJECTION_SCHEMA_VERSION,
        "format": "structuredText",
        "state": "ready" if records else "empty",
        "documentVersion": _digest(document_material),
        **document_material,
        "sectionCount": len(sections),
        "supportingMemoryCount": len(records),
    }


def _dimensions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    for facet, title in _DIMENSION_DEFINITIONS:
        supporting = [record for record in records if facet in record["facets"]]
        narrative = _dimension_narrative(facet=facet, records=supporting)
        dimensions.append(
            {
                "dimension": facet,
                "title": title,
                "status": "ready" if supporting else "empty",
                "narrative": narrative,
                "supportingMemoryCount": len(supporting),
                "supportingMemoryIds": [record["memoryId"] for record in supporting],
                "supportingMemoryVersionIds": [
                    record["memoryVersionId"] for record in supporting
                ],
            }
        )
    return dimensions


def _dimension_narrative(*, facet: str, records: list[dict[str, Any]]) -> str | None:
    if not records:
        return None
    if facet in {"lifeEvent", "knowledge"}:
        statements = _sentences(records)
        return "。".join(statements) + "。" if statements else None

    facet_name = {
        "emotion": "emotions",
        "relationship": "relationships",
        "personality": "personality",
        "value": "values",
        "habit": "habits",
        "goal": "goals",
        "identity": "identity",
        "reflection": "reflections",
    }[facet]
    values = _unique(
        item["value"]
        for record in records
        for item in record["facetEvidence"].get(facet_name, [])
    )
    if facet == "relationship":
        people = _unique(
            item["value"]
            for record in records
            for item in record["facetEvidence"].get("people", [])
        )
        relationship_text = f"，关系包括{'、'.join(values[:8])}" if values else ""
        if people:
            return f"{'、'.join(people[:8])}是我记忆中的重要人物{relationship_text}。"
    if not values:
        statements = _sentences(records)
        return "。".join(statements) + "。" if statements else None
    templates = {
        "emotion": "这些记忆中反复出现的情感有{values}。",
        "personality": "这些记忆呈现出我{values}的一面。",
        "value": "这些经历反复体现出我看重{values}。",
        "habit": "这些记忆记录了我{values}的习惯与偏好。",
        "goal": "这些内容体现了我对{values}的目标与愿望。",
        "identity": "这些记忆中的重要身份与角色包括{values}。",
        "reflection": "这些经历沉淀出的反思包括{values}。",
    }
    return templates[facet].format(values="、".join(values[:8]))


def build_person_memory_model(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build all rebuildable person projections from current formal memory."""

    records = [_normalize_entry(entry) for entry in entries]
    records.sort(key=lambda value: (value["memoryVersion"], value["memoryVersionId"]))
    memory_ids = [record["memoryId"] for record in records]
    version_ids = [record["memoryVersionId"] for record in records]
    if len(memory_ids) != len(set(memory_ids)) or len(version_ids) != len(set(version_ids)):
        raise PersonMemoryModelError("person memory input contains duplicate current versions")
    source_fingerprint = _digest(
        [
            {
                "memoryId": record["memoryId"],
                "memoryVersionId": record["memoryVersionId"],
                "contentHash": record["contentHash"],
            }
            for record in records
        ]
    )
    cognitive = _cognitive_projection(records)
    relationships = _relationship_projection(records)
    biography = _biography_projection(records, source_fingerprint)
    dimensions = _dimensions(records)
    model_material = {
        "sourceFingerprint": source_fingerprint,
        "formalMemories": records,
        "cognitiveProjection": cognitive,
        "relationshipProjection": relationships,
        "biographyProjection": biography,
        "dimensions": dimensions,
    }
    return {
        "schemaVersion": PERSON_MEMORY_MODEL_SCHEMA_VERSION,
        "algorithmVersion": PERSON_MEMORY_MODEL_ALGORITHM_VERSION,
        "state": "ready" if records else "empty",
        "modelVersion": _digest(model_material),
        "memoryCount": len(records),
        **model_material,
    }


__all__ = [
    "PERSON_BIOGRAPHY_PROJECTION_SCHEMA_VERSION",
    "PERSON_COGNITIVE_PROJECTION_SCHEMA_VERSION",
    "PERSON_MEMORY_MODEL_ALGORITHM_VERSION",
    "PERSON_MEMORY_MODEL_SCHEMA_VERSION",
    "PERSON_RELATIONSHIP_PROJECTION_SCHEMA_VERSION",
    "PersonMemoryModelError",
    "build_person_memory_model",
]

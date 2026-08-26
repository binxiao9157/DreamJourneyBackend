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
import unicodedata

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
PERSON_SEMANTIC_CONSOLIDATION_SCHEMA_VERSION = (
    "owner-truth-semantic-consolidation-v1"
)
PERSON_MEMORY_MODEL_ALGORITHM_VERSION = "evidence-bound-person-model-v2"

_WHITESPACE = re.compile(r"\s+")
_SEMANTIC_TEXT_NOISE = re.compile(r"[^0-9a-z\u3400-\u9fff]+")
_MAX_TEXT = 1_200
_MAX_SECTION_COUNT = 10
_MAX_BLOCK_MEMORIES = 3

_SINGLETON_FACT_PATTERNS = (
    (
        "identity.birthPlace",
        re.compile(r"(?:我|本人)?(?:出生于|出生地(?:是|在|为))(?P<value>[^，。；！？]{1,40})"),
    ),
    (
        "identity.birthDate",
        re.compile(r"(?:我|本人)?(?:生日|出生日期)(?:是|为|在)(?P<value>[^，。；！？]{1,32})"),
    ),
    (
        "identity.hometown",
        re.compile(r"(?:我|本人)?(?:老家在|家乡(?:是|在|为)|来自)(?P<value>[^，。；！？]{1,40})"),
    ),
    (
        "identity.currentResidence",
        re.compile(
            r"(?:^|[，。；！？])\s*(?:(?:我|本人)(?:目前|现在|当前)"
            r"(?:住在|居住在)|(?:我|本人)?现居)(?P<value>[^，。；！？]{1,40})"
        ),
    ),
    (
        "identity.currentEmployer",
        re.compile(
            r"(?:^|[，。；！？])\s*(?:我|本人)(?:目前|现在|当前)"
            r"(?:在|就职于|任职于)"
            r"(?P<value>[^，。；！？]{1,48}?)(?:工作|任职|就职|$)"
        ),
    ),
    (
        "identity.currentOccupation",
        re.compile(
            r"(?:^|[，。；！？])\s*(?:我|本人)(?:目前|现在|当前)"
            r"(?:是|从事)(?P<value>[^，。；！？]{1,40})"
        ),
    ),
)
_PREFERENCE_PATTERN = re.compile(
    r"(?:我|本人)?(?P<polarity>不喜欢|不爱|讨厌|喜欢|爱好|热爱)"
    r"(?P<value>[^，。；！？]{1,48})"
)

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
        "memoryKind": kind.value,
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
        "perspectiveType": _text(entry.get("perspectiveType")) or "firstPerson",
        "epistemicStatus": _text(entry.get("epistemicStatus")) or "uncertain",
        "sensitivity": _text(entry.get("sensitivity")) or "standard",
        "semanticSlot": _text(enriched.get("semanticSlot"), maximum=128),
        "semanticValue": _text(enriched.get("semanticValue"), maximum=256),
        "evidenceRefs": [dict(value) for value in evidence_refs if isinstance(value, Mapping)],
        "citation": _citation(entry),
    }


def _semantic_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return _SEMANTIC_TEXT_NOISE.sub("", normalized)


def _character_bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _text_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    left_bigrams = _character_bigrams(left)
    right_bigrams = _character_bigrams(right)
    union = left_bigrams | right_bigrams
    return len(left_bigrams & right_bigrams) / len(union) if union else 0.0


def _record_anchor(record: Mapping[str, Any]) -> set[str]:
    anchors: set[str] = set()
    for facet_name, entries in record.get("facetEvidence", {}).items():
        if facet_name in {"emotions", "values", "personality", "reflections"}:
            continue
        for entry in entries:
            normalized = _semantic_text(entry.get("value"))
            if normalized:
                anchors.add(f"{facet_name}:{normalized}")
    event_time = record.get("eventTime")
    if isinstance(event_time, Mapping):
        for field in ("start", "end"):
            normalized = _semantic_text(event_time.get(field))
            if normalized:
                anchors.add(f"time:{field}:{normalized}")
    return anchors


def _semantic_assertion(record: Mapping[str, Any]) -> tuple[str, str] | None:
    explicit_slot = _semantic_text(record.get("semanticSlot"))
    explicit_value = _semantic_text(record.get("semanticValue"))
    if explicit_slot and explicit_value:
        return explicit_slot, explicit_value

    narrative = _text(record.get("narrative"))
    for slot, pattern in _SINGLETON_FACT_PATTERNS:
        match = pattern.search(narrative)
        if match is None:
            continue
        value = _semantic_text(match.group("value"))
        if value:
            return slot, value

    preference = _PREFERENCE_PATTERN.search(narrative)
    if preference is not None:
        target = _semantic_text(preference.group("value"))
        if target:
            polarity = preference.group("polarity")
            value = "negative" if polarity in {"不喜欢", "不爱", "讨厌"} else "positive"
            return f"preference:{target}", value
    return None


def _records_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if (
        left.get("sensitivity") != right.get("sensitivity")
        or left.get("perspectiveType") != right.get("perspectiveType")
    ):
        return False
    left_assertion = _semantic_assertion(left)
    right_assertion = _semantic_assertion(right)
    return (
        left_assertion is not None
        and right_assertion is not None
        and left_assertion[0] == right_assertion[0]
        and left_assertion[1] != right_assertion[1]
    )


def _records_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if (
        left.get("primaryKind") != right.get("primaryKind")
        or left.get("sensitivity") != right.get("sensitivity")
        or left.get("perspectiveType") != right.get("perspectiveType")
        or _records_conflict(left, right)
    ):
        return False
    left_assertion = _semantic_assertion(left)
    right_assertion = _semantic_assertion(right)
    if left_assertion is not None and left_assertion == right_assertion:
        return True

    left_text = _semantic_text(left.get("narrative"))
    right_text = _semantic_text(right.get("narrative"))
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 8 and shorter in longer and len(shorter) / len(longer) >= 0.72:
        return True
    similarity = _text_similarity(left_text, right_text)
    if similarity >= 0.9:
        return True
    return bool(_record_anchor(left) & _record_anchor(right)) and similarity >= 0.72


def _unique_citations(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    citations: dict[str, dict[str, Any]] = {}
    for record in records:
        candidates = record.get("citations")
        if not isinstance(candidates, (list, tuple)):
            candidates = [record.get("citation")]
        for citation in candidates:
            if not isinstance(citation, Mapping):
                continue
            version_id = _text(citation.get("memoryVersionId"))
            if version_id:
                citations[version_id] = dict(citation)
    return [citations[key] for key in sorted(citations)]


def _merged_facet_evidence(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for facet_name in OWNER_TRUTH_FACET_NAMES:
        values: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            for entry in record.get("facetEvidence", {}).get(facet_name, []):
                key = (_text(entry.get("value")), _text(entry.get("evidenceMode")))
                if not key[0]:
                    continue
                existing = values.get(key)
                if existing is None or float(entry.get("confidence") or 0.0) > float(
                    existing.get("confidence") or 0.0
                ):
                    values[key] = dict(entry)
        result[facet_name] = [values[key] for key in sorted(values)]
    return result


def _consolidated_record(
    records: list[dict[str, Any]],
    *,
    status: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    representative = max(
        records,
        key=lambda item: (
            len(_text(item.get("narrative"))),
            int(item.get("memoryVersion") or 0),
            _text(item.get("memoryVersionId")),
        ),
    )
    citations = _unique_citations(records)
    memory_ids = sorted({_text(item.get("memoryId")) for item in records})
    version_ids = [item["memoryVersionId"] for item in citations]
    facet_evidence = _merged_facet_evidence(records)
    facets = [
        facet
        for facet in OWNER_TRUTH_SEMANTIC_FACETS
        if any(facet in item.get("facets", []) for item in records)
    ]
    alternatives = _unique(item.get("narrative") for item in records)
    if status == "conflict":
        joined = "；".join(value.rstrip("。！？!?；;") for value in alternatives[:4])
        narrative = (
            "以下已确认记忆存在明确冲突，解决前不作为回响回答依据："
            f"{joined}。"
        )
    else:
        narrative = _text(representative.get("narrative"))
    group_id = f"memory-group-{_digest(version_ids)[:24]}"
    search_values = [
        *alternatives,
        *(
            entry["value"]
            for entries in facet_evidence.values()
            for entry in entries
        ),
    ]
    public_group = {
        "groupId": group_id,
        "status": status,
        "primaryKind": representative["primaryKind"],
        "memoryKind": representative["memoryKind"],
        "title": narrative.rstrip("。！？!?；;")[:72],
        "narrative": narrative,
        "facets": facets,
        "supportingMemoryCount": len(memory_ids),
        "supportingMemoryIds": memory_ids,
        "supportingMemoryVersionIds": version_ids,
        "representativeMemoryVersionId": representative["memoryVersionId"],
        "evidence": citations,
        "alternatives": alternatives if status == "conflict" else [],
        "searchText": " ".join(_unique(search_values)),
    }
    consolidated = {
        **representative,
        "groupId": group_id,
        "consolidationStatus": status,
        "title": public_group["title"],
        "narrative": narrative,
        "facets": facets,
        "facetEvidence": facet_evidence,
        "citations": citations,
        "supportingMemoryIds": memory_ids,
        "supportingMemoryVersionIds": version_ids,
        "searchText": public_group["searchText"],
    }
    return public_group, consolidated


def _semantic_consolidation(
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parents = list(range(len(records)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = root(left)
        right_root = root(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(records)):
        for right in range(left + 1, len(records)):
            if _records_equivalent(records[left], records[right]):
                union(left, right)

    equivalent_clusters: dict[int, list[int]] = {}
    for index in range(len(records)):
        equivalent_clusters.setdefault(root(index), []).append(index)
    cluster_roots = sorted(equivalent_clusters)
    conflict_parents = {cluster_root: cluster_root for cluster_root in cluster_roots}
    conflict_edges: set[tuple[int, int]] = set()

    def conflict_root(cluster_root: int) -> int:
        parent = conflict_parents[cluster_root]
        while parent != conflict_parents[parent]:
            conflict_parents[parent] = conflict_parents[conflict_parents[parent]]
            parent = conflict_parents[parent]
        conflict_parents[cluster_root] = parent
        return parent

    def conflict_union(left: int, right: int) -> None:
        left_root = conflict_root(left)
        right_root = conflict_root(right)
        if left_root != right_root:
            conflict_parents[max(left_root, right_root)] = min(left_root, right_root)

    for position, left_root in enumerate(cluster_roots):
        for right_root in cluster_roots[position + 1 :]:
            if any(
                _records_conflict(records[left], records[right])
                for left in equivalent_clusters[left_root]
                for right in equivalent_clusters[right_root]
            ):
                conflict_edges.add((left_root, right_root))
                conflict_union(left_root, right_root)

    components: dict[int, list[int]] = {}
    for cluster_root in cluster_roots:
        components.setdefault(conflict_root(cluster_root), []).append(cluster_root)

    public_groups: list[dict[str, Any]] = []
    consolidated_records: list[dict[str, Any]] = []
    for component_root in sorted(components):
        cluster_members = components[component_root]
        record_indices = sorted(
            index
            for cluster_root in cluster_members
            for index in equivalent_clusters[cluster_root]
        )
        component_records = [records[index] for index in record_indices]
        is_conflict = any(
            left in cluster_members and right in cluster_members
            for left, right in conflict_edges
        )
        status = (
            "conflict"
            if is_conflict
            else "merged"
            if len(component_records) > 1
            else "ready"
        )
        public_group, consolidated = _consolidated_record(
            component_records,
            status=status,
        )
        public_groups.append(public_group)
        consolidated_records.append(consolidated)

    public_groups.sort(key=lambda item: item["groupId"])
    consolidated_records.sort(key=lambda item: item["groupId"])
    return {
        "schemaVersion": PERSON_SEMANTIC_CONSOLIDATION_SCHEMA_VERSION,
        "sourceMemoryCount": len(records),
        "groupCount": len(public_groups),
        "mergedGroupCount": sum(item["status"] == "merged" for item in public_groups),
        "conflictGroupCount": sum(item["status"] == "conflict" for item in public_groups),
        "groups": public_groups,
    }, consolidated_records


def _projection_item(record: Mapping[str, Any], *, item_kind: str) -> dict[str, Any]:
    return {
        "itemId": f"item-{_digest([item_kind, record['groupId']])[:24]}",
        "itemKind": item_kind,
        "statement": record["narrative"],
        "epistemicStatus": record["epistemicStatus"],
        "confidence": 1.0,
        "facets": list(record["facets"]),
        "evidence": list(record["citations"]),
    }


def _cognitive_projection(records: list[dict[str, Any]]) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    experiences: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    resolved_records = [
        record for record in records if record.get("consolidationStatus") != "conflict"
    ]
    for record in resolved_records:
        primary = record["primaryKind"]
        if primary == "knowledge":
            facts.append(_projection_item(record, item_kind="fact"))
        elif primary == "lifeEvent":
            experiences.append(_projection_item(record, item_kind="experience"))
        else:
            observations.append(_projection_item(record, item_kind="observation"))

    recurring: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in resolved_records:
        for facet_name in ("values", "personality", "habits", "goals", "identity"):
            for evidence in record["facetEvidence"].get(facet_name, []):
                recurring.setdefault((facet_name, evidence["value"]), []).append(record)
    mental_models: list[dict[str, Any]] = []
    for (facet_name, value), supporting in sorted(recurring.items()):
        unique_support = {
            record["groupId"]: record for record in supporting
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
                "evidence": _unique_citations(unique_support.values()),
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
        if record.get("consolidationStatus") == "conflict":
            continue
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
            for citation in record["citations"]:
                if citation not in current["evidence"]:
                    current["evidence"].append(citation)
            relation_types = relationship_labels if entity_type == "person" else ["发生于"]
            for relation_type in relation_types:
                key = (owner_id, entity_id, relation_type, record["groupId"])
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
                        "evidence": list(record["citations"]),
                    }
                )
    return {
        "schemaVersion": PERSON_RELATIONSHIP_PROJECTION_SCHEMA_VERSION,
        "entities": sorted(entities.values(), key=lambda value: value["entityId"]),
        "relations": sorted(relations, key=lambda value: value["relationId"]),
    }


def _searchable(record: Mapping[str, Any]) -> str:
    values = [record.get("searchText") or record["narrative"]]
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
                    "blockId": f"block-{_digest([version_id for item in chunk for version_id in item['supportingMemoryVersionIds']])[:24]}",
                    "blockType": "narrative",
                    "text": _block_text(chunk, block_index=len(blocks)),
                    "facets": [
                        facet
                        for facet in OWNER_TRUTH_SEMANTIC_FACETS
                        if any(facet in item["facets"] for item in chunk)
                    ],
                    "evidence": _unique_citations(chunk),
                }
            )
        sections.append(
            {
                "sectionId": section["sectionId"],
                "title": section["title"],
                "blocks": blocks,
                "evidence": _unique_citations(section_records),
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
        "supportingMemoryCount": sum(
            len(record["supportingMemoryIds"]) for record in records
        ),
    }


def _dimensions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    for facet, title in _DIMENSION_DEFINITIONS:
        supporting = [record for record in records if facet in record["facets"]]
        narrative = _dimension_narrative(facet=facet, records=supporting)
        memory_ids = sorted(
            {
                memory_id
                for record in supporting
                for memory_id in record["supportingMemoryIds"]
            }
        )
        version_ids = sorted(
            {
                version_id
                for record in supporting
                for version_id in record["supportingMemoryVersionIds"]
            }
        )
        dimensions.append(
            {
                "dimension": facet,
                "title": title,
                "status": "ready" if supporting else "empty",
                "narrative": narrative,
                "supportingMemoryCount": len(memory_ids),
                "supportingMemoryIds": memory_ids,
                "supportingMemoryVersionIds": version_ids,
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
    semantic_consolidation, consolidated_records = _semantic_consolidation(records)
    cognitive = _cognitive_projection(consolidated_records)
    relationships = _relationship_projection(consolidated_records)
    biography = _biography_projection(consolidated_records, source_fingerprint)
    dimensions = _dimensions(consolidated_records)
    model_material = {
        "sourceFingerprint": source_fingerprint,
        "formalMemories": records,
        "semanticConsolidation": semantic_consolidation,
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
        "consolidatedMemoryCount": len(consolidated_records),
        "unresolvedConflictCount": int(
            semantic_consolidation["conflictGroupCount"]
        ),
        **model_material,
    }


__all__ = [
    "PERSON_BIOGRAPHY_PROJECTION_SCHEMA_VERSION",
    "PERSON_COGNITIVE_PROJECTION_SCHEMA_VERSION",
    "PERSON_MEMORY_MODEL_ALGORITHM_VERSION",
    "PERSON_MEMORY_MODEL_SCHEMA_VERSION",
    "PERSON_RELATIONSHIP_PROJECTION_SCHEMA_VERSION",
    "PERSON_SEMANTIC_CONSOLIDATION_SCHEMA_VERSION",
    "PersonMemoryModelError",
    "build_person_memory_model",
]

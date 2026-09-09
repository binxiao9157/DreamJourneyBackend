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
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v4,
    enrich_memory_payload_v5,
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
    content_schema_version = _text(entry.get("contentSchemaVersion"))
    enriched = (
        enrich_memory_payload_v5(kind=kind, payload=content)
        if content_schema_version == OWNER_TRUTH_SCHEMA_VERSION_V5
        else enrich_memory_payload_v4(kind=kind, payload=content)
    )
    semantic = enriched["semantic"]
    qualifiers = enriched.get("qualifiers")
    typed_time = qualifiers.get("validTime") if isinstance(qualifiers, Mapping) else None
    event_time = typed_time if isinstance(typed_time, Mapping) else semantic["eventTime"]
    typed_object = enriched.get("object")
    typed_object = dict(typed_object) if isinstance(typed_object, Mapping) else None
    evidence_refs = entry.get("evidenceRefs")
    if not isinstance(evidence_refs, (list, tuple)):
        evidence_refs = []
    return {
        "memoryId": memory_id,
        "memoryVersionId": version_id,
        "memoryVersion": int(entry.get("memoryVersion") or 1),
        "memoryKind": kind.value,
        "contentHash": _text(entry.get("contentHash")),
        "contentSchemaVersion": content_schema_version,
        "primaryKind": semantic["primaryKind"],
        "facets": list(semantic["facets"]),
        "title": semantic["title"],
        "narrative": semantic["narrative"],
        "eventTime": event_time,
        "entities": list(semantic["entities"]),
        "emotionEvidence": list(semantic["emotionEvidence"]),
        "facetEvidence": {
            name: _facet_entries(enriched, name) for name in OWNER_TRUTH_FACET_NAMES
        },
        "perspectiveType": _text(entry.get("perspectiveType")) or "firstPerson",
        "epistemicStatus": _text(entry.get("epistemicStatus")) or "uncertain",
        "sensitivity": _text(entry.get("sensitivity")) or "standard",
        "factType": _text(enriched.get("factType"), maximum=80),
        "dimensions": [
            _text(value, maximum=80)
            for value in enriched.get("dimensions", [])
            if _text(value, maximum=80)
        ],
        "predicate": _text(enriched.get("predicate"), maximum=128),
        "object": typed_object,
        "qualifiers": dict(qualifiers) if isinstance(qualifiers, Mapping) else {},
        "affect": (
            dict(enriched["affect"])
            if isinstance(enriched.get("affect"), Mapping)
            else None
        ),
        "provenance": (
            dict(enriched["provenance"])
            if isinstance(enriched.get("provenance"), Mapping)
            else {}
        ),
        "semanticSlot": _text(enriched.get("semanticSlot"), maximum=128)
        or _text(enriched.get("predicate"), maximum=128),
        "semanticValue": _text(enriched.get("semanticValue"), maximum=256)
        or _text(
            typed_object.get("label") if typed_object is not None else None,
            maximum=256,
        ),
        "evidenceRefs": [dict(value) for value in evidence_refs if isinstance(value, Mapping)],
        # Subject identity is part of the asserted fact, not merely display
        # metadata.  It must survive projection normalisation so a statement
        # about a parent can never be merged into the Owner's own fact.
        "memorySubjectId": _text(enriched.get("memorySubjectId"), maximum=160) or None,
        "claimSubjectId": _text(enriched.get("claimSubjectId"), maximum=160) or None,
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


def _claim_subject_key(record: Mapping[str, Any]) -> str:
    """Return a non-guessing subject key for semantic grouping.

    ``unknown`` does not mean Owner. It is only an anonymous conflict bucket:
    opposite assertions without an identified subject must be quarantined
    rather than silently treated as two independently usable owner facts.
    Equivalence remains stricter in ``_records_equivalent`` and only permits
    exact duplicates when both subjects are unknown.
    """

    explicit = _semantic_text(record.get("claimSubjectId") or record.get("memorySubjectId"))
    if explicit:
        return explicit
    return "unknown:unidentified"


def _explicit_claim_subject_key(record: Mapping[str, Any]) -> str | None:
    """Return the declared fact subject without inventing an Owner identity."""

    explicit = _semantic_text(record.get("claimSubjectId") or record.get("memorySubjectId"))
    return explicit or None


def _semantic_assertion(record: Mapping[str, Any]) -> tuple[str, str, str] | None:
    subject = _claim_subject_key(record)
    fact_type = _semantic_text(record.get("factType"))
    predicate = _semantic_text(record.get("predicate"))
    typed_object = record.get("object")
    object_label = _semantic_text(
        typed_object.get("label") if isinstance(typed_object, Mapping) else None
    )
    qualifiers = record.get("qualifiers")
    polarity = _semantic_text(
        qualifiers.get("polarity") if isinstance(qualifiers, Mapping) else None
    )
    if fact_type and predicate and object_label:
        if fact_type == "preference":
            return subject, f"preference:{object_label}", polarity or "unknown"
        return subject, f"{predicate}:{object_label}", polarity or "affirmed"

    explicit_slot = _semantic_text(record.get("semanticSlot"))
    explicit_value = _semantic_text(record.get("semanticValue"))
    if explicit_slot and explicit_value:
        return subject, explicit_slot, explicit_value

    narrative = _text(record.get("narrative"))
    for slot, pattern in _SINGLETON_FACT_PATTERNS:
        match = pattern.search(narrative)
        if match is None:
            continue
        value = _semantic_text(match.group("value"))
        if value:
            return subject, slot, value

    preference = _PREFERENCE_PATTERN.search(narrative)
    if preference is not None:
        target = _semantic_text(preference.group("value"))
        if target:
            polarity = preference.group("polarity")
            value = "negative" if polarity in {"不喜欢", "不爱", "讨厌"} else "positive"
            return subject, f"preference:{target}", value
    return None


def _time_year_bounds(record: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Extract only explicit calendar years for safe interval comparison."""

    event_time = record.get("eventTime")
    if not isinstance(event_time, Mapping):
        return None, None

    def year(value: Any) -> int | None:
        match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
        return int(match.group(1)) if match else None

    start = year(event_time.get("start"))
    end = year(event_time.get("end"))
    if start is not None and end is not None and end < start:
        return None, None
    return start, end


def _records_have_disjoint_explicit_time(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    """Return true only when two explicit year intervals cannot overlap."""

    left_start, left_end = _time_year_bounds(left)
    right_start, right_end = _time_year_bounds(right)
    if left_end is not None and right_start is not None and left_end < right_start:
        return True
    if right_end is not None and left_start is not None and right_end < left_start:
        return True
    return False


def _records_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if (
        left.get("sensitivity") != right.get("sensitivity")
        or left.get("perspectiveType") != right.get("perspectiveType")
    ):
        return False
    left_assertion = _semantic_assertion(left)
    right_assertion = _semantic_assertion(right)
    if _records_have_disjoint_explicit_time(left, right):
        return False
    return (
        left_assertion is not None
        and right_assertion is not None
        and left_assertion[0] == right_assertion[0]
        and left_assertion[1] == right_assertion[1]
        and left_assertion[2] != right_assertion[2]
    )


def _records_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_subject = _explicit_claim_subject_key(left)
    right_subject = _explicit_claim_subject_key(right)
    if (
        left.get("primaryKind") != right.get("primaryKind")
        or left.get("sensitivity") != right.get("sensitivity")
        or left.get("perspectiveType") != right.get("perspectiveType")
        or _records_conflict(left, right)
        or _records_have_disjoint_explicit_time(left, right)
    ):
        return False
    if left_subject != right_subject:
        return False

    left_text = _semantic_text(left.get("narrative"))
    right_text = _semantic_text(right.get("narrative"))
    # A missing subject never means the Owner and must not allow a fuzzy merge.
    # An exact duplicate is different: it is safe to retain one semantic record
    # with both immutable citations, without assigning either fact to a person.
    if left_subject is None and not (left_text and left_text == right_text):
        return False

    left_assertion = _semantic_assertion(left)
    right_assertion = _semantic_assertion(right)
    # Once both entries have a typed assertion, that identity is authoritative.
    # Fuzzy narrative containment (for example "第1道菜" inside "第10道菜")
    # must never collapse two distinct formal facts into one group.
    if left_assertion is not None and right_assertion is not None:
        return left_assertion == right_assertion

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

    sections = [_biography_section(section) for section in ordered]
    overview = _biography_overview(records)
    return _biography_document(
        records=records,
        source_fingerprint=source_fingerprint,
        overview=overview,
        sections=sections,
    )


def _biography_section(section: Mapping[str, Any]) -> dict[str, Any]:
    """Build one independently reusable biography section.

    A section is keyed by its stable semantic bucket.  Keeping this small
    unit separate lets incremental derivation rebuild only the affected
    chapter while retaining the exact prior text/evidence for other chapters.
    """

    section_records = list(section["records"])
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
    return {
        "sectionId": section["sectionId"],
        "title": section["title"],
        "blocks": blocks,
        "evidence": _unique_citations(section_records),
    }


def _biography_overview(records: list[dict[str, Any]]) -> str | None:
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
    return "。".join(overview_parts) + "。" if records else None


def _biography_document(
    *,
    records: list[dict[str, Any]],
    source_fingerprint: str,
    overview: str | None,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
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


def _section_signature(records: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    """Return the current-version identity that makes a section reusable."""

    values = []
    for record in records:
        values.append(
            (
                _text(record.get("memoryId")),
                _text(record.get("memoryVersionId")),
                _text(record.get("contentHash")),
            )
        )
    return tuple(sorted(values))


def _section_signature_from_document(section: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    evidence = section.get("evidence")
    if not isinstance(evidence, list):
        return ()
    values = []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        values.append(
            (
                _text(item.get("memoryId")),
                _text(item.get("memoryVersionId")),
                _text(item.get("contentHash")),
            )
        )
    return tuple(sorted(values))


def _biography_sections(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Partition records exactly as the full biography projection does."""

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
    return ordered


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


def _record_identity(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _text(record.get("memoryId")),
        _text(record.get("memoryVersionId")),
        _text(record.get("contentHash")),
    )


def _clone_json(value: Any) -> Any:
    """Copy projection material without sharing a mutable cache object."""

    return json.loads(_canonical_json(value))


def _current_records_from_entries(
    *,
    previous_model: Mapping[str, Any],
    entries: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[str], dict[str, dict[str, Any]]]:
    """Normalize only new or changed current versions.

    The caller still supplies the current authority set so deletion is visible,
    but byte-identical MemoryVersions are copied from the preceding derived
    model rather than being re-normalized as though every fact were new.
    """

    raw_previous = previous_model.get("formalMemories")
    if not isinstance(raw_previous, list):
        raise PersonMemoryModelError("incremental model has no prior formal memories")
    previous_by_memory_id: dict[str, dict[str, Any]] = {}
    for item in raw_previous:
        if not isinstance(item, Mapping):
            raise PersonMemoryModelError("incremental model has malformed formal memory")
        memory_id = _text(item.get("memoryId"))
        if not memory_id or memory_id in previous_by_memory_id:
            raise PersonMemoryModelError("incremental model has duplicate formal memories")
        previous_by_memory_id[memory_id] = _clone_json(item)

    current_entries: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise PersonMemoryModelError("formal memory entry must be an object")
        memory_id = _text(entry.get("memoryId"))
        if not memory_id or memory_id in current_entries:
            raise PersonMemoryModelError("person memory input contains duplicate current versions")
        current_entries[memory_id] = entry

    changed_memory_ids = set(previous_by_memory_id) - set(current_entries)
    records: list[dict[str, Any]] = []
    for memory_id, entry in current_entries.items():
        prior = previous_by_memory_id.get(memory_id)
        incoming_identity = (
            memory_id,
            _text(entry.get("memoryVersionId")),
            _text(entry.get("contentHash")),
        )
        if prior is not None and _record_identity(prior) == incoming_identity:
            records.append(_clone_json(prior))
            continue
        records.append(_normalize_entry(entry))
        changed_memory_ids.add(memory_id)
    records.sort(key=lambda value: (value["memoryVersion"], value["memoryVersionId"]))
    version_ids = [record["memoryVersionId"] for record in records]
    if len(version_ids) != len(set(version_ids)):
        raise PersonMemoryModelError("person memory input contains duplicate current versions")
    return records, changed_memory_ids, previous_by_memory_id


def _affected_memory_ids_from_semantic_groups(
    *,
    previous_model: Mapping[str, Any],
    current_consolidation: Mapping[str, Any],
    direct_changed_memory_ids: set[str],
) -> set[str]:
    """Include unchanged facts whose consolidation group changed around them."""

    affected = set(direct_changed_memory_ids)
    previous = previous_model.get("semanticConsolidation")
    prior_groups = previous.get("groups") if isinstance(previous, Mapping) else []
    current_groups = current_consolidation.get("groups")
    if not isinstance(prior_groups, list) or not isinstance(current_groups, list):
        # A malformed prior cache never authorizes a partial semantic rebuild.
        return set()

    def add_group_members(groups: Iterable[Any]) -> None:
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            members = {
                _text(value)
                for value in group.get("supportingMemoryIds", [])
                if _text(value)
            }
            if members.intersection(affected):
                affected.update(members)

    # A changed fact can merge with or dispute an older fact. Repeatedly
    # expand both historic and current connected components until stable.
    while True:
        before = set(affected)
        add_group_members(prior_groups)
        add_group_members(current_groups)
        if affected == before:
            return affected


def _facets_for_memory_ids(
    *,
    memory_ids: Iterable[str],
    current_records: Iterable[Mapping[str, Any]],
    previous_records: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    affected: set[str] = set()
    by_current = {str(record.get("memoryId") or ""): record for record in current_records}
    for memory_id in memory_ids:
        for record in (previous_records.get(memory_id), by_current.get(memory_id)):
            if not isinstance(record, Mapping):
                continue
            facets = record.get("facets")
            if isinstance(facets, list):
                affected.update(_text(value) for value in facets if _text(value))
    return affected


def _incremental_dimensions(
    *,
    previous_model: Mapping[str, Any],
    records: list[dict[str, Any]],
    affected_facets: set[str],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    raw_previous = previous_model.get("dimensions")
    previous_by_dimension = {
        _text(item.get("dimension")): item
        for item in raw_previous
        if isinstance(raw_previous, list)
        and isinstance(item, Mapping)
        and _text(item.get("dimension"))
    }
    dimensions: list[dict[str, Any]] = []
    rebuilt: list[str] = []
    for facet, title in _DIMENSION_DEFINITIONS:
        prior = previous_by_dimension.get(facet)
        if facet not in affected_facets and prior is not None:
            dimensions.append(_clone_json(prior))
            continue
        supporting = [record for record in records if facet in record["facets"]]
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
                "narrative": _dimension_narrative(facet=facet, records=supporting),
                "supportingMemoryCount": len(memory_ids),
                "supportingMemoryIds": memory_ids,
                "supportingMemoryVersionIds": version_ids,
            }
        )
        rebuilt.append(facet)
    return dimensions, tuple(rebuilt)


def _incremental_biography_projection(
    *,
    previous_model: Mapping[str, Any],
    records: list[dict[str, Any]],
    source_fingerprint: str,
    affected_memory_ids: set[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    previous_biography = previous_model.get("biographyProjection")
    previous_sections = (
        previous_biography.get("sections")
        if isinstance(previous_biography, Mapping)
        else []
    )
    previous_by_id = {
        _text(section.get("sectionId")): section
        for section in previous_sections
        if isinstance(section, Mapping) and _text(section.get("sectionId"))
    }
    sections: list[dict[str, Any]] = []
    rebuilt: list[str] = []
    for section in _biography_sections(records):
        section_id = _text(section.get("sectionId"))
        prior = previous_by_id.get(section_id)
        signature = _section_signature(section["records"])
        section_memory_ids = {memory_id for memory_id, _, _ in signature}
        if (
            prior is not None
            and not section_memory_ids.intersection(affected_memory_ids)
            and _section_signature_from_document(prior) == signature
        ):
            sections.append(_clone_json(prior))
            continue
        sections.append(_biography_section(section))
        rebuilt.append(section_id)
    return (
        _biography_document(
            records=records,
            source_fingerprint=source_fingerprint,
            overview=_biography_overview(records),
            sections=sections,
        ),
        tuple(rebuilt),
    )


def build_person_memory_model_incremental(
    *,
    previous_model: Mapping[str, Any],
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Update current-formal projections without rebuilding unaffected dimensions.

    The authoritative input remains the full current formal-memory set. This
    function uses the prior *derived* model only as a rebuildable cache: it
    re-normalizes changed versions, recomputes the semantic safety pass and
    global machine projections, then rebuilds only the dimensions and
    biography sections whose supporting facts changed. No previous narrative
    is accepted as a new fact, and a malformed prior cache fails closed.
    """

    if not isinstance(previous_model, Mapping):
        raise PersonMemoryModelError("incremental model requires a prior projection")
    records, direct_changed, previous_records = _current_records_from_entries(
        previous_model=previous_model,
        entries=entries,
    )
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
    affected_memory_ids = _affected_memory_ids_from_semantic_groups(
        previous_model=previous_model,
        current_consolidation=semantic_consolidation,
        direct_changed_memory_ids=direct_changed,
    )
    if not affected_memory_ids and direct_changed:
        # Missing semantic history makes selective reuse unsafe.
        affected_memory_ids = {record["memoryId"] for record in records}
    affected_facets = _facets_for_memory_ids(
        memory_ids=affected_memory_ids,
        current_records=records,
        previous_records=previous_records,
    )
    dimensions, rebuilt_dimensions = _incremental_dimensions(
        previous_model=previous_model,
        records=consolidated_records,
        affected_facets=affected_facets,
    )
    biography, rebuilt_sections = _incremental_biography_projection(
        previous_model=previous_model,
        records=consolidated_records,
        source_fingerprint=source_fingerprint,
        affected_memory_ids=affected_memory_ids,
    )
    cognitive = _cognitive_projection(consolidated_records)
    relationships = _relationship_projection(consolidated_records)
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
        "unresolvedConflictCount": int(semantic_consolidation["conflictGroupCount"]),
        **model_material,
        "incrementalDerivation": {
            "mode": "incremental",
            "changedMemoryCount": len(direct_changed),
            "affectedMemoryCount": len(affected_memory_ids),
            "rebuiltDimensions": list(rebuilt_dimensions),
            "reusedDimensionCount": len(_DIMENSION_DEFINITIONS) - len(rebuilt_dimensions),
            "rebuiltBiographySections": list(rebuilt_sections),
            "globalComponentsRebuilt": [
                "semanticConsolidation",
                "cognitiveProjection",
                "relationshipProjection",
                "biographyOverview",
            ],
        },
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
    "build_person_memory_model_incremental",
]

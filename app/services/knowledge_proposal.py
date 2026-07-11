import hashlib
import re
import unicodedata
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.knowledge_store import KNOWLEDGE_ENTITY_TYPES, normalize_kb_mutation_v2
from app.services.knowledge_source_refs import source_ref_title
from app.services.privacy import SYNCABLE_SCOPES


PERSONA_SCOPES = {"personal", "family"}
ENTITY_SINGULAR = {
    "people": "person",
    "places": "place",
    "events": "event",
    "facts": "fact",
}
ENTITY_ID_PREFIX = {
    "people": "kb_person",
    "places": "kb_place",
    "events": "kb_event",
    "facts": "kb_fact",
}
ENTITY_FIELDS = {
    "people": {
        "name",
        "aliases",
        "relation",
        "traits",
        "briefBio",
        "relatedPersonIds",
        "evidenceStatus",
        "createdAt",
        "updatedAt",
    },
    "places": {
        "name",
        "category",
        "latitude",
        "longitude",
        "description",
        "relatedPersonIds",
        "evidenceStatus",
        "createdAt",
        "updatedAt",
    },
    "events": {
        "title",
        "description",
        "year",
        "month",
        "locationId",
        "participantIds",
        "mediaIds",
        "memoirId",
        "evidenceStatus",
        "createdAt",
        "updatedAt",
    },
    "facts": {
        "statement",
        "confidence",
        "relatedPersonIds",
        "relatedPlaceIds",
        "relatedEventIds",
        "evidenceStatus",
        "createdAt",
        "updatedAt",
    },
}
EXTRACTION_FIELDS = {
    "people": {"name", "aliases", "relation", "traits", "briefBio"},
    "places": {"name", "category", "latitude", "longitude", "description"},
    "events": {"title", "description", "year", "month"},
    "facts": {"statement", "confidence"},
}
class KnowledgeProposalValidationError(ValueError):
    pass


def normalize_natural_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def build_knowledge_mutation_proposal(
    *,
    user_id: str,
    persona_scope: Any,
    digital_human_id: Any,
    extraction: Dict[str, Any],
    safe_context: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    owner_user_id, normalized_scope, normalized_digital_human_id = _normalize_identity(
        user_id,
        persona_scope,
        digital_human_id,
    )
    if not isinstance(extraction, dict):
        raise KnowledgeProposalValidationError("extraction must be an object")
    if not isinstance(safe_context, dict):
        raise KnowledgeProposalValidationError("safe context must be an object")

    privacy_metadata = _safe_privacy_metadata(safe_context.get("privacyMetadata"))
    source_session_id = _source_session_id(safe_context)
    source_session_ids = _source_session_ids(safe_context)
    snapshot_record = snapshot if isinstance(snapshot, dict) else {}
    graph = snapshot_record.get("graph")
    if not isinstance(graph, dict):
        graph = {}
    revision = snapshot_record.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        revision = 0

    eligible_snapshot: Dict[str, List[Dict[str, Any]]] = {
        entity_type: [
            entity
            for entity in _object_list(graph.get(entity_type))
            if _entity_matches_identity(
                entity,
                owner_user_id,
                normalized_scope,
                normalized_digital_human_id,
            )
        ]
        for entity_type in KNOWLEDGE_ENTITY_TYPES
    }
    snapshot_by_key = _snapshot_natural_key_index(eligible_snapshot)

    records: Dict[str, List[Dict[str, Any]]] = {
        entity_type: [] for entity_type in KNOWLEDGE_ENTITY_TYPES
    }
    generated_count = 0
    reused_count = 0
    duplicate_count = 0
    skipped_count = 0

    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        records_by_key: Dict[str, Dict[str, Any]] = {}
        for extracted in _object_list(extraction.get(entity_type)):
            natural_key = _natural_key(entity_type, extracted)
            if not natural_key:
                skipped_count += 1
                continue
            previous = records_by_key.get(natural_key)
            if previous is not None:
                duplicate_count += 1
                _merge_duplicate_extraction(previous["extracted"], extracted)
                previous["entity"] = _build_upsert(
                    entity_type=entity_type,
                    entity_id=previous["entity"]["id"],
                    extracted=previous["extracted"],
                    existing=previous["existing"],
                    owner_user_id=owner_user_id,
                    persona_scope=normalized_scope,
                    digital_human_id=normalized_digital_human_id,
                    privacy_metadata=privacy_metadata,
                    source_session_id=source_session_id,
                    source_session_ids=source_session_ids,
                )
                continue

            existing = snapshot_by_key[entity_type].get(natural_key)
            if existing is None:
                entity_id = _stable_entity_id(
                    owner_user_id,
                    normalized_scope,
                    normalized_digital_human_id,
                    entity_type,
                    natural_key,
                )
                generated_count += 1
            else:
                entity_id = str(existing["id"]).strip()
                reused_count += 1

            record = {
                "naturalKey": natural_key,
                "extracted": deepcopy(extracted),
                "existing": deepcopy(existing) if existing is not None else None,
                "entity": _build_upsert(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    extracted=extracted,
                    existing=existing,
                    owner_user_id=owner_user_id,
                    persona_scope=normalized_scope,
                    digital_human_id=normalized_digital_human_id,
                    privacy_metadata=privacy_metadata,
                    source_session_id=source_session_id,
                    source_session_ids=source_session_ids,
                ),
            }
            records_by_key[natural_key] = record
            records[entity_type].append(record)

    relation_counts = _resolve_relationships(records, eligible_snapshot)
    upserts = {
        entity_type: [record["entity"] for record in records[entity_type]]
        for entity_type in KNOWLEDGE_ENTITY_TYPES
    }
    tombstones: List[Dict[str, Any]] = []
    if any(upserts.values()):
        normalized_mutation = normalize_kb_mutation_v2(upserts, tombstones)
        upserts = normalized_mutation["upserts"]
        tombstones = normalized_mutation["tombstones"]

    upsert_counts = {
        entity_type: len(upserts[entity_type]) for entity_type in KNOWLEDGE_ENTITY_TYPES
    }
    snapshot_entity_count = sum(len(_object_list(graph.get(key))) for key in KNOWLEDGE_ENTITY_TYPES)
    eligible_snapshot_count = sum(len(items) for items in eligible_snapshot.values())
    return {
        "proposalSchemaVersion": 1,
        "mutationSchemaVersion": 2,
        "baseRevision": revision,
        "ownerUserId": owner_user_id,
        "personaScope": normalized_scope,
        "digitalHumanId": normalized_digital_human_id,
        "upserts": upserts,
        "tombstones": tombstones,
        "proposalPolicy": {
            "version": 1,
            "snapshotEntityCount": snapshot_entity_count,
            "eligibleSnapshotEntityCount": eligible_snapshot_count,
            "upsertEntityCount": sum(upsert_counts.values()),
            "upsertCounts": upsert_counts,
            "reusedEntityCount": reused_count,
            "generatedEntityCount": generated_count,
            "duplicateEntityCount": duplicate_count,
            "skippedEntityCount": skipped_count,
            "resolvedRelationCount": relation_counts[0],
            "unresolvedRelationCount": relation_counts[1],
        },
    }


def _normalize_identity(user_id: Any, persona_scope: Any, digital_human_id: Any) -> Tuple[str, str, str]:
    owner_user_id = str(user_id or "").strip()
    if not owner_user_id:
        raise KnowledgeProposalValidationError("userId is required")
    normalized_scope = str(persona_scope or "personal").strip()
    if normalized_scope not in PERSONA_SCOPES:
        raise KnowledgeProposalValidationError("personaScope must be personal or family")
    if normalized_scope == "personal":
        return owner_user_id, normalized_scope, owner_user_id

    normalized_digital_human_id = str(digital_human_id or "").strip()
    if not normalized_digital_human_id:
        raise KnowledgeProposalValidationError(
            "digitalHumanId is required when personaScope is family"
        )
    return owner_user_id, normalized_scope, normalized_digital_human_id


def _object_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _natural_key(entity_type: str, entity: Dict[str, Any]) -> str:
    if entity_type == "people":
        return normalize_natural_text(entity.get("name"))
    if entity_type == "places":
        return normalize_natural_text(entity.get("name"))
    if entity_type == "facts":
        return normalize_natural_text(entity.get("statement"))
    if entity_type == "events":
        title = normalize_natural_text(entity.get("title"))
        if not title:
            return ""
        return "|".join((title, _date_component(entity.get("year")), _date_component(entity.get("month"))))
    raise KnowledgeProposalValidationError(f"unsupported entity type: {entity_type}")


def _date_component(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return normalize_natural_text(value)


def _entity_matches_identity(
    entity: Dict[str, Any],
    owner_user_id: str,
    persona_scope: str,
    digital_human_id: str,
) -> bool:
    entity_owner = _optional_text(entity.get("ownerUserId"))
    entity_scope = _optional_text(entity.get("personaScope"))
    entity_digital_human_id = _optional_text(entity.get("digitalHumanId"))

    if persona_scope == "family":
        return (
            entity_owner == owner_user_id
            and entity_scope == "family"
            and entity_digital_human_id == digital_human_id
        )
    if entity_owner is not None and entity_owner != owner_user_id:
        return False
    if entity_scope is not None and entity_scope != "personal":
        return False
    if entity_digital_human_id is not None and entity_digital_human_id != owner_user_id:
        return False
    return True


def _optional_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _snapshot_natural_key_index(
    snapshot: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    result: Dict[str, Dict[str, Dict[str, Any]]] = {
        entity_type: {} for entity_type in KNOWLEDGE_ENTITY_TYPES
    }
    for entity_type, entities in snapshot.items():
        for entity in entities:
            entity_id = _optional_text(entity.get("id"))
            natural_key = _natural_key(entity_type, entity)
            if entity_id is None or not natural_key:
                continue
            result[entity_type].setdefault(natural_key, entity)
    return result


def _stable_entity_id(
    owner_user_id: str,
    persona_scope: str,
    digital_human_id: str,
    entity_type: str,
    natural_key: str,
) -> str:
    seed = "|".join(
        (
            owner_user_id,
            persona_scope,
            digital_human_id,
            ENTITY_SINGULAR[entity_type],
            natural_key,
        )
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{ENTITY_ID_PREFIX[entity_type]}_{digest}"


def _merge_duplicate_extraction(target: Dict[str, Any], duplicate: Dict[str, Any]) -> None:
    for key in ("aliases", "traits", "sourceTurnIndices"):
        if key in target or key in duplicate:
            target[key] = _unique_values(target.get(key), duplicate.get(key))
    for key, value in duplicate.items():
        if key not in target or target[key] in (None, "", []):
            target[key] = deepcopy(value)


def _build_upsert(
    *,
    entity_type: str,
    entity_id: str,
    extracted: Dict[str, Any],
    existing: Optional[Dict[str, Any]],
    owner_user_id: str,
    persona_scope: str,
    digital_human_id: str,
    privacy_metadata: Dict[str, Any],
    source_session_id: int,
    source_session_ids: List[int],
) -> Dict[str, Any]:
    upsert: Dict[str, Any] = {"id": entity_id}
    if existing is not None:
        for key in ENTITY_FIELDS[entity_type]:
            if key in existing:
                upsert[key] = deepcopy(existing[key])
    for key in EXTRACTION_FIELDS[entity_type]:
        if key in extracted and extracted[key] is not None:
            if key in {"aliases", "traits"}:
                upsert[key] = _unique_values(upsert.get(key), extracted.get(key))
            else:
                upsert[key] = deepcopy(extracted[key])

    if entity_type == "people":
        upsert["aliases"] = _string_list(upsert.get("aliases"))
        upsert["traits"] = _string_list(upsert.get("traits"))
    if entity_type == "events":
        upsert["mediaIds"] = _string_list(upsert.get("mediaIds"))
    if entity_type == "facts" and not _optional_text(upsert.get("confidence")):
        upsert["confidence"] = "medium"

    existing_privacy = existing.get("privacyMetadata") if existing is not None else None
    upsert["privacyMetadata"] = _merge_privacy_metadata(
        existing_privacy,
        privacy_metadata,
        source_session_id=source_session_id,
        source_turn_indices=extracted.get("sourceTurnIndices"),
    )
    upsert["ownerUserId"] = owner_user_id
    upsert["personaScope"] = persona_scope
    upsert["digitalHumanId"] = digital_human_id
    upsert["sourceSessionIds"] = _unique_ints(
        (existing or {}).get("sourceSessionIds"),
        source_session_ids,
    )
    upsert["sourceTurnIndices"] = _unique_ints(
        (existing or {}).get("sourceTurnIndices"),
        extracted.get("sourceTurnIndices"),
    )
    confidence = str(upsert.get("confidence") or "").strip().casefold()
    incoming_evidence_status = (
        "observed"
        if entity_type != "facts" or confidence in {"high", "confirmed"}
        else "candidate"
    )
    upsert["evidenceStatus"] = _merge_evidence_status(
        (existing or {}).get("evidenceStatus"),
        incoming_evidence_status,
    )
    return upsert


def _merge_evidence_status(existing: Any, incoming: str) -> str:
    normalized_existing = str(existing or "").strip().casefold()
    normalized_incoming = str(incoming or "candidate").strip().casefold()
    if normalized_existing in {"rejected", "superseded"}:
        return normalized_existing
    if normalized_incoming in {"rejected", "superseded"}:
        return normalized_incoming
    rank = {"candidate": 0, "observed": 1, "confirmed": 2}
    return (
        normalized_existing
        if rank.get(normalized_existing, -1) >= rank.get(normalized_incoming, -1)
        else normalized_incoming
    )


def _safe_privacy_metadata(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise KnowledgeProposalValidationError("safe context requires privacyMetadata")
    scope = value.get("scope")
    if not isinstance(scope, str) or scope not in SYNCABLE_SCOPES:
        raise KnowledgeProposalValidationError("safe context privacyMetadata is not syncable")
    return {
        "scope": scope,
        "sourceRefs": _safe_source_refs(value.get("sourceRefs")),
    }


def _safe_source_refs(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = _optional_text(item.get("kind")) or "unknown"
        source_id = _optional_text(item.get("id"))
        if source_id is None or (kind, source_id) in seen:
            continue
        seen.add((kind, source_id))
        result.append(
            {
                "kind": kind,
                "id": source_id,
                "title": source_ref_title(kind),
            }
        )
    return result


def _merge_privacy_metadata(
    existing: Any,
    current: Dict[str, Any],
    *,
    source_session_id: int,
    source_turn_indices: Any,
) -> Dict[str, Any]:
    existing_refs = _safe_source_refs(existing.get("sourceRefs")) if isinstance(existing, dict) else []
    canonical_turn_refs = [
        {
            "kind": "conversationTurn",
            "id": f"session-{source_session_id}:turn-{turn_index}",
            "title": source_ref_title("conversationTurn"),
        }
        for turn_index in _unique_ints(source_turn_indices)
    ]
    return {
        "scope": current["scope"],
        "sourceRefs": _merge_source_refs(
            existing_refs,
            canonical_turn_refs,
        ),
    }


def _merge_source_refs(*groups: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for group in groups:
        for item in group:
            key = (item["kind"], item["id"])
            if key in seen:
                continue
            seen.add(key)
            result.append(deepcopy(item))
    return result


def _source_session_ids(safe_context: Dict[str, Any]) -> List[int]:
    values: List[Any] = []
    raw_values = safe_context.get("sourceSessionIds")
    if isinstance(raw_values, list):
        values.extend(raw_values)
    if "sessionId" in safe_context:
        values.append(safe_context.get("sessionId"))
    return _unique_ints(values)


def _source_session_id(safe_context: Dict[str, Any]) -> int:
    value = safe_context.get("sessionId")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KnowledgeProposalValidationError(
            "safe context sessionId must be a non-negative integer"
        )
    return value


def _unique_ints(*groups: Any) -> List[int]:
    result: List[int] = []
    seen: Set[int] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for value in group:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value in seen:
                continue
            seen.add(value)
            result.append(value)
    return result


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen: Set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _unique_values(*groups: Any) -> List[Any]:
    result: List[Any] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for value in group:
            if value not in result:
                result.append(deepcopy(value))
    return result


def _resolve_relationships(
    records: Dict[str, List[Dict[str, Any]]],
    snapshot: Dict[str, List[Dict[str, Any]]],
) -> Tuple[int, int]:
    people_lookup: Dict[str, Set[str]] = {}
    place_lookup: Dict[str, Set[str]] = {}
    event_lookup: Dict[str, Set[str]] = {}
    allowed_ids: Dict[str, Set[str]] = {
        entity_type: set() for entity_type in KNOWLEDGE_ENTITY_TYPES
    }

    for entity in snapshot["people"] + [record["entity"] for record in records["people"]]:
        _index_entity(people_lookup, entity.get("name"), entity.get("id"))
        for alias in _string_list(entity.get("aliases")):
            _index_entity(people_lookup, alias, entity.get("id"))
        _add_allowed_id(allowed_ids["people"], entity.get("id"))
    for entity in snapshot["places"] + [record["entity"] for record in records["places"]]:
        _index_entity(place_lookup, entity.get("name"), entity.get("id"))
        _add_allowed_id(allowed_ids["places"], entity.get("id"))
    for entity in snapshot["events"] + [record["entity"] for record in records["events"]]:
        _index_entity(event_lookup, entity.get("title"), entity.get("id"))
        _add_allowed_id(allowed_ids["events"], entity.get("id"))
    for entity in snapshot["facts"] + [record["entity"] for record in records["facts"]]:
        _add_allowed_id(allowed_ids["facts"], entity.get("id"))

    resolved_count = 0
    unresolved_count = 0
    for record in records["people"]:
        record["entity"]["relatedPersonIds"] = _valid_existing_ids(
            record["entity"].get("relatedPersonIds"), allowed_ids["people"]
        )
    for record in records["places"]:
        resolved, unresolved = _resolve_values(
            record["extracted"].get("relatedPeople"), people_lookup, allowed_ids["people"], "name"
        )
        record["entity"]["relatedPersonIds"] = _merge_ids(
            _valid_existing_ids(record["entity"].get("relatedPersonIds"), allowed_ids["people"]),
            resolved,
        )
        resolved_count += len(resolved)
        unresolved_count += unresolved
    for record in records["events"]:
        participants, unresolved = _resolve_values(
            record["extracted"].get("participants"), people_lookup, allowed_ids["people"], "name"
        )
        record["entity"]["participantIds"] = _merge_ids(
            _valid_existing_ids(record["entity"].get("participantIds"), allowed_ids["people"]),
            participants,
        )
        resolved_count += len(participants)
        unresolved_count += unresolved

        location = record["extracted"].get("location")
        if _reference_text(location, "name"):
            location_ids, location_unresolved = _resolve_values(
                [location], place_lookup, allowed_ids["places"], "name"
            )
            if location_ids:
                record["entity"]["locationId"] = location_ids[0]
                resolved_count += 1
            else:
                unresolved_count += location_unresolved
                if record["entity"].get("locationId") not in allowed_ids["places"]:
                    record["entity"].pop("locationId", None)
        elif record["entity"].get("locationId") not in allowed_ids["places"]:
            record["entity"].pop("locationId", None)
    for record in records["facts"]:
        relation_specs = (
            ("relatedPeople", "relatedPersonIds", people_lookup, allowed_ids["people"], "name"),
            ("relatedPlaces", "relatedPlaceIds", place_lookup, allowed_ids["places"], "name"),
            ("relatedEvents", "relatedEventIds", event_lookup, allowed_ids["events"], "title"),
        )
        for source_key, target_key, lookup, valid_ids, dict_key in relation_specs:
            relation_ids, unresolved = _resolve_values(
                record["extracted"].get(source_key), lookup, valid_ids, dict_key
            )
            record["entity"][target_key] = _merge_ids(
                _valid_existing_ids(record["entity"].get(target_key), valid_ids),
                relation_ids,
            )
            resolved_count += len(relation_ids)
            unresolved_count += unresolved
    return resolved_count, unresolved_count


def _index_entity(index: Dict[str, Set[str]], label: Any, entity_id: Any) -> None:
    normalized_label = normalize_natural_text(label)
    normalized_id = _optional_text(entity_id)
    if normalized_label and normalized_id:
        index.setdefault(normalized_label, set()).add(normalized_id)


def _add_allowed_id(target: Set[str], entity_id: Any) -> None:
    normalized_id = _optional_text(entity_id)
    if normalized_id:
        target.add(normalized_id)


def _resolve_values(
    values: Any,
    lookup: Dict[str, Set[str]],
    valid_ids: Set[str],
    dict_key: str,
) -> Tuple[List[str], int]:
    if not isinstance(values, list):
        return [], 0
    resolved: List[str] = []
    unresolved = 0
    for value in values:
        label = _reference_text(value, dict_key)
        if not label:
            continue
        if label in valid_ids:
            matches = {label}
        else:
            matches = lookup.get(normalize_natural_text(label), set())
        if len(matches) != 1:
            unresolved += 1
            continue
        entity_id = next(iter(matches))
        if entity_id not in resolved:
            resolved.append(entity_id)
    return resolved, unresolved


def _reference_text(value: Any, dict_key: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        raw = value.get(dict_key)
        return raw.strip() if isinstance(raw, str) else ""
    return ""


def _valid_existing_ids(values: Any, allowed: Set[str]) -> List[str]:
    if not isinstance(values, list):
        return []
    return [value for value in _string_list(values) if value in allowed]


def _merge_ids(*groups: Iterable[str]) -> List[str]:
    result: List[str] = []
    for group in groups:
        for value in group:
            if value not in result:
                result.append(value)
    return result

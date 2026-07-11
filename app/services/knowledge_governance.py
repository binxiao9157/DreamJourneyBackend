from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.knowledge_store import KNOWLEDGE_ENTITY_TYPES, normalize_kb_mutation_v2


GOVERNANCE_SCHEMA_VERSION = 1
GOVERNANCE_ACTIONS = {"confirm", "reject", "correct", "deleteSource"}
PERSONA_SCOPES = {"personal", "family"}
ENTITY_ID_PREFIX = {
    "people": "kb_person",
    "places": "kb_place",
    "events": "kb_event",
    "facts": "kb_fact",
}
CORRECTION_FIELDS = {
    "people": {"name", "aliases", "relation", "traits", "briefBio", "relatedPersonIds"},
    "places": {
        "name",
        "category",
        "latitude",
        "longitude",
        "description",
        "relatedPersonIds",
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
    },
    "facts": {"statement", "relatedPersonIds", "relatedPlaceIds", "relatedEventIds"},
}
PRIMARY_TEXT_FIELD = {
    "people": "name",
    "places": "name",
    "events": "title",
    "facts": "statement",
}
STRING_FIELDS = {
    "name",
    "relation",
    "briefBio",
    "category",
    "description",
    "title",
    "locationId",
    "memoirId",
    "statement",
}
STRING_LIST_FIELDS = {
    "aliases",
    "traits",
    "relatedPersonIds",
    "participantIds",
    "mediaIds",
    "relatedPlaceIds",
    "relatedEventIds",
}
NUMBER_FIELDS = {"latitude", "longitude"}
INTEGER_FIELDS = {"year", "month"}
RELATION_FIELDS = {
    "people": {"relatedPersonIds": "people"},
    "places": {"relatedPersonIds": "people"},
    "events": {"locationId": "places", "participantIds": "people"},
    "facts": {
        "relatedPersonIds": "people",
        "relatedPlaceIds": "places",
        "relatedEventIds": "events",
    },
}


class KnowledgeGovernanceValidationError(ValueError):
    pass


class KnowledgeGovernanceNotFound(LookupError):
    pass


def build_knowledge_governance_mutation(
    *,
    user_id: Any,
    operation_id: Any,
    base_revision: Any,
    action: Any,
    snapshot: Any,
) -> Dict[str, Any]:
    owner_user_id = _required_text(user_id, "userId")
    normalized_operation_id = _required_text(operation_id, "operationId")
    _validate_base_revision(base_revision)
    normalized_action = _normalize_action(action)
    graph = _snapshot_graph(snapshot)

    if normalized_action["kind"] == "deleteSource":
        upserts, summary = _build_delete_source_upserts(
            graph=graph,
            owner_user_id=owner_user_id,
            operation_id=normalized_operation_id,
            action=normalized_action,
        )
    else:
        upserts, summary = _build_entity_action_upserts(
            graph=graph,
            owner_user_id=owner_user_id,
            operation_id=normalized_operation_id,
            action=normalized_action,
        )

    mutation = normalize_kb_mutation_v2(upserts, [])
    return {
        "governanceSchemaVersion": GOVERNANCE_SCHEMA_VERSION,
        "mutationSchemaVersion": 2,
        "baseRevision": base_revision,
        "ownerUserId": owner_user_id,
        "upserts": mutation["upserts"],
        "tombstones": mutation["tombstones"],
        "summary": summary,
    }


def summarize_knowledge_governance_mutation(
    mutation: Any,
    *,
    operation_id: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(mutation, dict) or not isinstance(mutation.get("upserts"), dict):
        return None

    metadata_items: List[Dict[str, Any]] = []
    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        entities = mutation["upserts"].get(entity_type, [])
        if not isinstance(entities, list):
            return None
        for entity in entities:
            metadata = entity.get("governanceMetadata") if isinstance(entity, dict) else None
            if not isinstance(metadata, dict) or metadata.get("operationId") != operation_id:
                return None
            metadata_items.append(metadata)
    if not metadata_items:
        return None

    first = metadata_items[0]
    action = first.get("action")
    decided_at = first.get("decidedAt")
    if action not in GOVERNANCE_ACTIONS or not isinstance(decided_at, str):
        return None

    summary: Dict[str, Any] = {
        "action": action,
        "decidedAt": decided_at,
        "affectedEntityCount": len(metadata_items),
    }
    if action == "deleteSource":
        source_ref = first.get("sourceRef")
        targets = _unique_links(item.get("target") for item in metadata_items)
        if not isinstance(source_ref, dict) or not targets:
            return None
        summary["sourceRef"] = deepcopy(source_ref)
        summary["targets"] = targets
        return summary

    target = first.get("target")
    if not _is_entity_link(target):
        return None
    summary["target"] = deepcopy(target)
    replacement = first.get("replacement")
    if replacement is not None:
        if not _is_entity_link(replacement):
            return None
        summary["replacement"] = deepcopy(replacement)
    return summary


def _build_entity_action_upserts(
    *,
    graph: Dict[str, Any],
    owner_user_id: str,
    operation_id: str,
    action: Dict[str, Any],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    entity_type = action["entityType"]
    entity_id = action["entityId"]
    target = _find_target(graph, entity_type, entity_id)
    persona_identity = _entity_identity(target, owner_user_id)
    if action["kind"] == "correct":
        _validate_correction_relationships(
            correction=action["correction"],
            entity_type=entity_type,
            graph=graph,
            owner_user_id=owner_user_id,
            persona_identity=persona_identity,
        )

    target_link = _entity_link(entity_type, entity_id)
    metadata: Dict[str, Any] = {
        "action": action["kind"],
        "operationId": operation_id,
        "decidedAt": action["decidedAt"],
        "target": target_link,
    }
    summary: Dict[str, Any] = {
        "action": action["kind"],
        "decidedAt": action["decidedAt"],
        "affectedEntityCount": 1,
        "target": deepcopy(target_link),
    }

    updated_target = deepcopy(target)
    _apply_canonical_identity(updated_target, owner_user_id, persona_identity)
    upsert_entities = [updated_target]
    if action["kind"] == "confirm":
        updated_target["evidenceStatus"] = "confirmed"
        if entity_type == "facts":
            updated_target["confidence"] = "confirmed"
    elif action["kind"] == "reject":
        updated_target["evidenceStatus"] = "rejected"
    else:
        replacement_id = _stable_replacement_id(
            owner_user_id=owner_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            operation_id=operation_id,
        )
        replacement_link = _entity_link(entity_type, replacement_id)
        metadata["replacement"] = replacement_link
        summary["replacement"] = deepcopy(replacement_link)
        summary["affectedEntityCount"] = 2

        updated_target["evidenceStatus"] = "superseded"
        replacement = deepcopy(target)
        replacement["id"] = replacement_id
        _apply_canonical_identity(replacement, owner_user_id, persona_identity)
        _apply_correction(replacement, action["correction"], entity_type)
        replacement["evidenceStatus"] = "confirmed"
        if entity_type == "facts":
            replacement["confidence"] = "confirmed"
        upsert_entities.append(replacement)

    for entity in upsert_entities:
        entity["governanceMetadata"] = deepcopy(metadata)

    return _upserts_for(entity_type, upsert_entities), summary


def _build_delete_source_upserts(
    *,
    graph: Dict[str, Any],
    owner_user_id: str,
    operation_id: str,
    action: Dict[str, Any],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    source_ref = action["sourceRef"]
    upserts = {entity_type: [] for entity_type in KNOWLEDGE_ENTITY_TYPES}
    targets = []

    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        for entity in graph[entity_type]:
            if not _entity_has_source_ref(entity, source_ref):
                continue
            persona_identity = _entity_identity(entity, owner_user_id)
            updated = deepcopy(entity)
            _apply_canonical_identity(updated, owner_user_id, persona_identity)
            privacy_metadata = deepcopy(updated["privacyMetadata"])
            privacy_metadata["sourceRefs"] = [
                deepcopy(item)
                for item in privacy_metadata.get("sourceRefs", [])
                if not _source_refs_match(item, source_ref)
            ]
            updated["privacyMetadata"] = privacy_metadata
            updated["evidenceStatus"] = "superseded"
            target_link = _entity_link(entity_type, str(updated["id"]).strip())
            updated["governanceMetadata"] = {
                "action": "deleteSource",
                "operationId": operation_id,
                "decidedAt": action["decidedAt"],
                "target": deepcopy(target_link),
                "sourceRef": deepcopy(source_ref),
            }
            upserts[entity_type].append(updated)
            targets.append(target_link)

    if not targets:
        raise KnowledgeGovernanceNotFound("sourceRef not found")
    return upserts, {
        "action": "deleteSource",
        "decidedAt": action["decidedAt"],
        "affectedEntityCount": len(targets),
        "sourceRef": deepcopy(source_ref),
        "targets": targets,
    }


def _normalize_action(action: Any) -> Dict[str, Any]:
    if not isinstance(action, dict):
        raise KnowledgeGovernanceValidationError("action must be an object")
    kind = _required_text(action.get("kind"), "action.kind")
    if kind not in GOVERNANCE_ACTIONS:
        raise KnowledgeGovernanceValidationError("action.kind is unsupported")
    decided_at = _timezone_iso(action.get("decidedAt"))

    if kind == "deleteSource":
        return {
            "kind": kind,
            "decidedAt": decided_at,
            "sourceRef": _normalize_source_ref(action.get("sourceRef")),
        }

    entity_type = _required_text(action.get("entityType"), "action.entityType")
    if entity_type not in KNOWLEDGE_ENTITY_TYPES:
        raise KnowledgeGovernanceValidationError("action.entityType is unsupported")
    normalized = {
        "kind": kind,
        "entityType": entity_type,
        "entityId": _required_text(action.get("entityId"), "action.entityId"),
        "decidedAt": decided_at,
    }
    if kind == "correct":
        normalized["correction"] = _normalize_correction(action.get("correction"), entity_type)
    return normalized


def _normalize_correction(value: Any, entity_type: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise KnowledgeGovernanceValidationError("action.correction must be a non-empty object")
    unsupported = sorted(set(value) - CORRECTION_FIELDS[entity_type])
    if unsupported:
        raise KnowledgeGovernanceValidationError(
            f"action.correction field is not allowed for {entity_type}: {unsupported[0]}"
        )
    correction = deepcopy(value)
    for field, field_value in correction.items():
        _validate_correction_value(field, field_value)
    return correction


def _validate_correction_value(field: str, value: Any) -> None:
    if field in STRING_FIELDS:
        if value is not None and not isinstance(value, str):
            raise KnowledgeGovernanceValidationError(f"action.correction.{field} must be a string")
        return
    if field in STRING_LIST_FIELDS:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise KnowledgeGovernanceValidationError(
                f"action.correction.{field} must be an array of non-empty strings"
            )
        return
    if field in NUMBER_FIELDS:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise KnowledgeGovernanceValidationError(f"action.correction.{field} must be a number")
        return
    if field in INTEGER_FIELDS and value is not None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise KnowledgeGovernanceValidationError(f"action.correction.{field} must be an integer")
        if field == "month" and not 1 <= value <= 12:
            raise KnowledgeGovernanceValidationError("action.correction.month must be between 1 and 12")


def _apply_correction(entity: Dict[str, Any], correction: Dict[str, Any], entity_type: str) -> None:
    for field, value in correction.items():
        if value is None:
            entity.pop(field, None)
        else:
            entity[field] = deepcopy(value)
    primary_field = PRIMARY_TEXT_FIELD[entity_type]
    primary_value = entity.get(primary_field)
    if not isinstance(primary_value, str) or not primary_value.strip():
        raise KnowledgeGovernanceValidationError(
            f"corrected {entity_type} requires a non-empty {primary_field}"
        )


def _snapshot_graph(snapshot: Any) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise KnowledgeGovernanceNotFound("knowledge snapshot not found")
    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        raise KnowledgeGovernanceValidationError("knowledge snapshot graph must be an object")
    normalized = deepcopy(graph)
    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        entities = normalized.get(entity_type, [])
        if not isinstance(entities, list) or any(not isinstance(item, dict) for item in entities):
            raise KnowledgeGovernanceValidationError(
                f"knowledge snapshot {entity_type} must be an array of objects"
            )
        normalized[entity_type] = entities
    return normalized


def _find_target(graph: Dict[str, Any], entity_type: str, entity_id: str) -> Dict[str, Any]:
    matches = [item for item in graph[entity_type] if item.get("id") == entity_id]
    if not matches:
        raise KnowledgeGovernanceNotFound("knowledge entity not found")
    if len(matches) > 1:
        raise KnowledgeGovernanceValidationError("knowledge entity id is ambiguous")
    return matches[0]


def _entity_identity(entity: Dict[str, Any], owner_user_id: str) -> Tuple[str, str]:
    explicit_owner = entity.get("ownerUserId")
    persona_scope = entity.get("personaScope")
    digital_human_id = entity.get("digitalHumanId")
    has_explicit_identity = any(
        value is not None and value != ""
        for value in (explicit_owner, persona_scope, digital_human_id)
    )
    if not has_explicit_identity:
        return "personal", owner_user_id
    if explicit_owner != owner_user_id:
        raise KnowledgeGovernanceValidationError("knowledge entity owner does not match userId")
    if persona_scope not in PERSONA_SCOPES:
        raise KnowledgeGovernanceValidationError("knowledge entity personaScope is invalid")
    if not isinstance(digital_human_id, str) or not digital_human_id.strip():
        raise KnowledgeGovernanceValidationError("knowledge entity digitalHumanId is required")
    if persona_scope == "personal" and digital_human_id != owner_user_id:
        raise KnowledgeGovernanceValidationError(
            "personal knowledge entity digitalHumanId must match its owner"
        )
    return persona_scope, digital_human_id


def _apply_canonical_identity(
    entity: Dict[str, Any],
    owner_user_id: str,
    persona_identity: Tuple[str, str],
) -> None:
    entity["ownerUserId"] = owner_user_id
    entity["personaScope"] = persona_identity[0]
    entity["digitalHumanId"] = persona_identity[1]


def _validate_correction_relationships(
    *,
    correction: Dict[str, Any],
    entity_type: str,
    graph: Dict[str, Any],
    owner_user_id: str,
    persona_identity: Tuple[str, str],
) -> None:
    for field, target_type in RELATION_FIELDS[entity_type].items():
        if field not in correction or correction[field] is None:
            continue
        raw_value = correction[field]
        references = raw_value if isinstance(raw_value, list) else [raw_value]
        for reference in references:
            target = _find_target(graph, target_type, reference)
            if _entity_identity(target, owner_user_id) != persona_identity:
                raise KnowledgeGovernanceValidationError(
                    f"action.correction.{field} crosses persona boundary"
                )


def _stable_replacement_id(
    *,
    owner_user_id: str,
    entity_type: str,
    entity_id: str,
    operation_id: str,
) -> str:
    seed = "|".join((owner_user_id, entity_type, entity_id, operation_id, "replacement"))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{ENTITY_ID_PREFIX[entity_type]}_{digest}"


def _normalize_source_ref(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise KnowledgeGovernanceValidationError("action.sourceRef must be an object")
    return {
        "kind": _required_text(value.get("kind"), "action.sourceRef.kind"),
        "id": _required_text(value.get("id"), "action.sourceRef.id"),
    }


def _entity_has_source_ref(entity: Dict[str, Any], source_ref: Dict[str, str]) -> bool:
    metadata = entity.get("privacyMetadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("sourceRefs"), list):
        return False
    return any(_source_refs_match(item, source_ref) for item in metadata["sourceRefs"])


def _source_refs_match(value: Any, expected: Dict[str, str]) -> bool:
    return (
        isinstance(value, dict)
        and value.get("kind") == expected["kind"]
        and value.get("id") == expected["id"]
    )


def _upserts_for(
    entity_type: str,
    entities: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        current_type: deepcopy(entities) if current_type == entity_type else []
        for current_type in KNOWLEDGE_ENTITY_TYPES
    }


def _entity_link(entity_type: str, entity_id: str) -> Dict[str, str]:
    return {"entityType": entity_type, "entityId": entity_id}


def _is_entity_link(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("entityType") in KNOWLEDGE_ENTITY_TYPES
        and isinstance(value.get("entityId"), str)
        and bool(value["entityId"].strip())
    )


def _unique_links(values: Any) -> List[Dict[str, str]]:
    result = []
    seen = set()
    for value in values:
        if not _is_entity_link(value):
            continue
        key = (value["entityType"], value["entityId"])
        if key in seen:
            continue
        seen.add(key)
        result.append(deepcopy(value))
    return result


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeGovernanceValidationError(f"{field} is required")
    return value.strip()


def _validate_base_revision(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KnowledgeGovernanceValidationError(
            "baseRevision is required and must be a non-negative integer"
        )


def _timezone_iso(value: Any) -> str:
    normalized = _required_text(value, "action.decidedAt")
    iso_value = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise KnowledgeGovernanceValidationError(
            "action.decidedAt must be a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KnowledgeGovernanceValidationError("action.decidedAt must include a timezone")
    return normalized

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.services.privacy import SYNCABLE_SCOPES, filter_syncable_graph


KNOWLEDGE_ENTITY_TYPES = ("people", "places", "events", "facts")


class KnowledgeMutationValidationError(ValueError):
    pass


@dataclass
class KnowledgeRevisionConflict(Exception):
    current_revision: int
    expected_revision: int

    def __str__(self) -> str:
        return (
            "knowledge revision conflict: "
            f"expected {self.expected_revision}, current {self.current_revision}"
        )


def normalize_kb_mutation_v2(upserts: Any, tombstones: Any) -> Dict[str, Any]:
    if not isinstance(upserts, dict):
        raise KnowledgeMutationValidationError("upserts must be an object")
    unknown_types = sorted(set(upserts) - set(KNOWLEDGE_ENTITY_TYPES))
    if unknown_types:
        raise KnowledgeMutationValidationError(
            f"unsupported knowledge entity type: {unknown_types[0]}"
        )

    normalized_upserts: Dict[str, List[Dict[str, Any]]] = {}
    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        entities = upserts.get(entity_type, [])
        if not isinstance(entities, list):
            raise KnowledgeMutationValidationError(f"upserts.{entity_type} must be an array")
        normalized_entities = []
        for entity in entities:
            if not isinstance(entity, dict):
                raise KnowledgeMutationValidationError(
                    f"upserts.{entity_type} entries must be objects"
                )
            entity_id = entity.get("id")
            if not isinstance(entity_id, str) or not entity_id.strip():
                raise KnowledgeMutationValidationError(
                    f"upserts.{entity_type} entries require a non-empty id"
                )
            metadata = entity.get("privacyMetadata")
            if not isinstance(metadata, dict):
                raise KnowledgeMutationValidationError(
                    f"upserts.{entity_type}[{entity_id}] requires privacyMetadata"
                )
            scope = metadata.get("scope")
            if not isinstance(scope, str) or scope not in SYNCABLE_SCOPES:
                raise KnowledgeMutationValidationError(
                    f"upserts.{entity_type}[{entity_id}] is not syncable"
                )
            normalized_entities.append(deepcopy(entity))
        normalized_upserts[entity_type] = normalized_entities

    if not isinstance(tombstones, list):
        raise KnowledgeMutationValidationError("tombstones must be an array")
    normalized_tombstones = []
    for tombstone in tombstones:
        if not isinstance(tombstone, dict):
            raise KnowledgeMutationValidationError("tombstone entries must be objects")
        entity_type = tombstone.get("entityType")
        if entity_type not in KNOWLEDGE_ENTITY_TYPES:
            raise KnowledgeMutationValidationError(
                f"unsupported knowledge entity type: {entity_type}"
            )
        entity_id = tombstone.get("entityId")
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise KnowledgeMutationValidationError("tombstones require a non-empty entityId")
        deleted_at = tombstone.get("deletedAt")
        if not isinstance(deleted_at, str) or not deleted_at.strip():
            raise KnowledgeMutationValidationError("tombstones require a non-empty deletedAt")
        iso_value = deleted_at[:-1] + "+00:00" if deleted_at.endswith("Z") else deleted_at
        try:
            parsed_deleted_at = datetime.fromisoformat(iso_value)
        except ValueError as exc:
            raise KnowledgeMutationValidationError(
                "tombstones.deletedAt must be a valid ISO-8601 timestamp"
            ) from exc
        if parsed_deleted_at.tzinfo is None or parsed_deleted_at.utcoffset() is None:
            raise KnowledgeMutationValidationError(
                "tombstones.deletedAt must include a timezone"
            )
        normalized_tombstones.append(deepcopy(tombstone))

    if not normalized_tombstones and not any(normalized_upserts.values()):
        raise KnowledgeMutationValidationError("mutation must contain an upsert or tombstone")

    return {
        "upserts": normalized_upserts,
        "tombstones": normalized_tombstones,
    }


def apply_kb_mutation_v2(
    current_graph: Dict[str, Any],
    mutation: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(current_graph, dict):
        raise KnowledgeMutationValidationError("current knowledge graph must be an object")
    if not isinstance(mutation, dict):
        raise KnowledgeMutationValidationError("mutation must be an object")

    normalized = normalize_kb_mutation_v2(
        mutation.get("upserts", {}),
        mutation.get("tombstones", []),
    )
    merged = deepcopy(current_graph)
    tombstone_ids = {entity_type: set() for entity_type in KNOWLEDGE_ENTITY_TYPES}
    for tombstone in normalized["tombstones"]:
        tombstone_ids[tombstone["entityType"]].add(tombstone["entityId"])

    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        current_entities = merged.get(entity_type, [])
        if not isinstance(current_entities, list):
            raise KnowledgeMutationValidationError(
                f"current knowledge graph {entity_type} must be an array"
            )
        remaining = [
            deepcopy(entity)
            for entity in current_entities
            if not isinstance(entity, dict)
            or entity.get("id") not in tombstone_ids[entity_type]
        ]
        for upsert in normalized["upserts"][entity_type]:
            upsert_id = upsert["id"]
            remaining = [
                entity
                for entity in remaining
                if not isinstance(entity, dict) or entity.get("id") != upsert_id
            ]
            remaining.append(deepcopy(upsert))
        merged[entity_type] = remaining

    filtered = filter_syncable_graph(merged)
    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        filtered_ids = {
            entity.get("id")
            for entity in filtered[entity_type]
            if isinstance(entity, dict)
        }
        for upsert in normalized["upserts"][entity_type]:
            if upsert["id"] not in filtered_ids:
                raise KnowledgeMutationValidationError(
                    f"upserts.{entity_type}[{upsert['id']}] did not survive privacy filtering"
                )

    return filtered, normalized

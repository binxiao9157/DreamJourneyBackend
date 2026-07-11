from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.services.privacy import (
    SYNCABLE_SCOPES,
    canonicalize_source_ref_titles,
    filter_syncable_graph,
)


KNOWLEDGE_ENTITY_TYPES = ("people", "places", "events", "facts")
KB_OPERATION_SYNC = "kb.sync"
KB_OPERATION_MUTATION = "kb.mutation"
KB_OPERATION_GOVERNANCE = "kb.governance"
KB_OPERATION_ARCHIVE_DELETE = "archive.delete"
KB_RECEIPT_ENVELOPE_VERSION = 1


class KnowledgeMutationValidationError(ValueError):
    pass


class KnowledgeOperationPayloadConflict(Exception):
    """An operation ID was already bound to different semantic input."""


@dataclass
class KnowledgeRevisionConflict(Exception):
    current_revision: int
    expected_revision: int

    def __str__(self) -> str:
        return (
            "knowledge revision conflict: "
            f"expected {self.expected_revision}, current {self.current_revision}"
        )


def knowledge_operation_payload_fingerprint(
    operation_kind: str,
    schema_version: int,
    semantic_payload: Any,
) -> str:
    canonical = json.dumps(
        {
            "operationKind": operation_kind,
            "schemaVersion": schema_version,
            "payload": semantic_payload,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_knowledge_operation_receipt(
    receipt: Dict[str, Any],
    *,
    operation_kind: str,
    payload_hash: str,
) -> None:
    if (
        receipt.get("operationKind") != operation_kind
        or receipt.get("payloadHash") != payload_hash
    ):
        raise KnowledgeOperationPayloadConflict()


def empty_kb_mutation_v2() -> Dict[str, Any]:
    return {
        "upserts": {entity_type: [] for entity_type in KNOWLEDGE_ENTITY_TYPES},
        "tombstones": [],
    }


def compact_knowledge_governance_summary(summary: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(summary, dict):
        return None

    compact: Dict[str, Any] = {}
    for key in ("action", "decidedAt", "affectedEntityCount"):
        value = summary.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            compact[key] = value

    for key in ("target", "replacement"):
        link = _compact_entity_link(summary.get(key))
        if link is not None:
            compact[key] = link

    source_ref = _compact_source_ref(summary.get("sourceRef"))
    if source_ref is not None:
        compact["sourceRef"] = source_ref

    targets = summary.get("targets")
    if isinstance(targets, list):
        compact_targets = [
            link
            for item in targets
            if (link := _compact_entity_link(item)) is not None
        ]
        if compact_targets:
            compact["targets"] = compact_targets
    return compact or None


def governance_summary_from_kb_mutation(
    mutation: Any,
    *,
    operation_id: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(mutation, dict) or not isinstance(mutation.get("upserts"), dict):
        return None

    metadata_items = []
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
    summary: Dict[str, Any] = {
        "action": first.get("action"),
        "decidedAt": first.get("decidedAt"),
        "affectedEntityCount": len(metadata_items),
    }
    if first.get("action") == "deleteSource":
        summary["sourceRef"] = first.get("sourceRef")
        summary["targets"] = [item.get("target") for item in metadata_items]
    else:
        summary["target"] = first.get("target")
        if first.get("replacement") is not None:
            summary["replacement"] = first.get("replacement")
    return compact_knowledge_governance_summary(summary)


def compact_knowledge_operation_receipt_result(
    result: Dict[str, Any],
    *,
    operation_id: str,
    operation_kind: str,
    governance_summary: Any = None,
) -> Dict[str, Any]:
    summary = compact_knowledge_governance_summary(governance_summary)
    if summary is None and operation_kind in (
        KB_OPERATION_GOVERNANCE,
        KB_OPERATION_ARCHIVE_DELETE,
    ):
        summary = governance_summary_from_kb_mutation(
            result.get("mutation"),
            operation_id=operation_id,
        )

    envelope: Dict[str, Any] = {
        "receiptEnvelopeVersion": KB_RECEIPT_ENVELOPE_VERSION,
        "revision": int(result.get("revision") or 0),
        "mutationSchemaVersion": result.get("mutationSchemaVersion"),
    }
    updated_at = result.get("updatedAt")
    if isinstance(updated_at, str) and updated_at:
        envelope["updatedAt"] = updated_at
    if bool(result.get("compatibilityNoOp")):
        envelope["compatibilityNoOp"] = True
    if summary is not None:
        envelope["governanceSummary"] = summary
    return envelope


def is_compact_knowledge_operation_receipt_result(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("receiptEnvelopeVersion") == KB_RECEIPT_ENVELOPE_VERSION
    )


def rebuild_compact_knowledge_operation_result(
    envelope: Dict[str, Any],
    *,
    user_id: str,
    operation_id: str,
    change: Optional[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    summary = compact_knowledge_governance_summary(envelope.get("governanceSummary"))
    if change is not None:
        mutation = deepcopy(change.get("mutation"))
        result = {
            "userId": user_id,
            "graph": deepcopy(change.get("graph") or {}),
            "revision": int(change.get("revision") or 0),
            "updatedAt": (
                change.get("updatedAt")
                or change.get("createdAt")
                or envelope.get("updatedAt")
            ),
            "operationId": operation_id,
            "mutationSchemaVersion": change.get(
                "mutationSchemaVersion",
                2 if mutation is not None else 1,
            ),
            "mutation": mutation,
        }
    else:
        current = snapshot or {}
        result = {
            "userId": user_id,
            "graph": deepcopy(current.get("graph") or {}),
            "revision": int(current.get("revision") or 0),
            "updatedAt": current.get("updatedAt") or envelope.get("updatedAt"),
            "operationId": operation_id,
            "mutationSchemaVersion": 2,
            "mutation": empty_kb_mutation_v2(),
        }
    result["receiptCompacted"] = True
    result["originalRevision"] = int(envelope.get("revision") or 0)
    if bool(envelope.get("compatibilityNoOp")):
        result["compatibilityNoOp"] = True
    if summary is not None:
        result["governanceSummary"] = summary
    return result


def _compact_entity_link(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    entity_type = value.get("entityType")
    entity_id = value.get("entityId")
    if entity_type not in KNOWLEDGE_ENTITY_TYPES or not isinstance(entity_id, str):
        return None
    entity_id = entity_id.strip()
    return None if not entity_id else {"entityType": entity_type, "entityId": entity_id}


def _compact_source_ref(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    source_id = value.get("id")
    if not isinstance(kind, str) or not isinstance(source_id, str):
        return None
    kind = kind.strip()
    source_id = source_id.strip()
    return None if not kind or not source_id else {"kind": kind, "id": source_id}


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
            normalized_entities.append(canonicalize_source_ref_titles(entity))
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

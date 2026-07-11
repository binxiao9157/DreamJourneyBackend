from typing import Any, Dict

from app.services.knowledge_source_refs import source_ref_classification
from app.services.knowledge_store import KNOWLEDGE_ENTITY_TYPES


SOURCE_REF_AUDIT_SCHEMA_VERSION = 1


def audit_knowledge_source_refs(snapshot: Any) -> Dict[str, Any]:
    snapshot_record = snapshot if isinstance(snapshot, dict) else {}
    revision = snapshot_record.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        revision = 0
    graph = snapshot_record.get("graph")
    if not isinstance(graph, dict):
        graph = {}

    entity_counts = {
        "total": 0,
        "withSourceRefs": 0,
        "withCanonicalRefs": 0,
        "withLegacyRefs": 0,
        "withUnknownRefs": 0,
    }
    ref_counts = {
        "total": 0,
        "canonical": 0,
        "legacy": 0,
        "unknown": 0,
    }

    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        entities = graph.get(entity_type)
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            entity_counts["total"] += 1
            metadata = entity.get("privacyMetadata")
            source_refs = metadata.get("sourceRefs") if isinstance(metadata, dict) else None
            if not isinstance(source_refs, list) or not source_refs:
                continue

            classifications = set()
            for source_ref in source_refs:
                kind = ""
                if isinstance(source_ref, dict) and isinstance(source_ref.get("kind"), str):
                    kind = source_ref["kind"].strip()
                classification = source_ref_classification(kind)
                classifications.add(classification)
                ref_counts["total"] += 1
                ref_counts[classification] += 1

            entity_counts["withSourceRefs"] += 1
            for classification in classifications:
                entity_counts[f"with{classification.title()}Refs"] += 1

    if ref_counts["unknown"]:
        recommended_action = "reviewUnknownSourceRefs"
    elif ref_counts["legacy"]:
        recommended_action = "planLegacySourceRefMigration"
    else:
        recommended_action = "none"

    return {
        "schemaVersion": SOURCE_REF_AUDIT_SCHEMA_VERSION,
        "revision": revision,
        "counts": {
            "entities": entity_counts,
            "sourceRefs": ref_counts,
        },
        "recommendedAction": recommended_action,
    }

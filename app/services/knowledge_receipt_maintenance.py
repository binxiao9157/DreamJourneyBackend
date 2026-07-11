from typing import Any, Dict

from app.services.knowledge_store import (
    KB_OPERATION_ARCHIVE_DELETE,
    KB_OPERATION_GOVERNANCE,
    KB_OPERATION_MUTATION,
    KB_OPERATION_SYNC,
    compact_knowledge_governance_summary,
    compact_knowledge_operation_receipt_result,
    is_compact_knowledge_operation_receipt_result,
)


SUPPORTED_RECEIPT_OPERATION_KINDS = frozenset(
    {
        KB_OPERATION_SYNC,
        KB_OPERATION_MUTATION,
        KB_OPERATION_GOVERNANCE,
        KB_OPERATION_ARCHIVE_DELETE,
    }
)


class KnowledgeReceiptMaintenanceError(ValueError):
    """A persisted knowledge operation receipt cannot be compacted safely."""


def compact_persisted_knowledge_receipt_result(
    result: Any,
    *,
    operation_id: str,
    operation_kind: str,
) -> Dict[str, Any]:
    if operation_kind not in SUPPORTED_RECEIPT_OPERATION_KINDS:
        raise KnowledgeReceiptMaintenanceError("unsupported receipt operation kind")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise KnowledgeReceiptMaintenanceError("receipt operation id is required")
    if not isinstance(result, dict):
        raise KnowledgeReceiptMaintenanceError("receipt result must be an object")

    revision = result.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise KnowledgeReceiptMaintenanceError(
            "receipt result must include a non-negative integer revision"
        )
    mutation_schema_version = result.get("mutationSchemaVersion")
    if (
        mutation_schema_version is not None
        and mutation_schema_version not in (1, 2)
    ):
        raise KnowledgeReceiptMaintenanceError(
            "receipt mutation schema version must be 1, 2, or null"
        )

    compact = compact_knowledge_operation_receipt_result(
        result,
        operation_id=operation_id.strip(),
        operation_kind=operation_kind,
        governance_summary=result.get("governanceSummary"),
    )
    mutation = result.get("mutation")
    if operation_kind == KB_OPERATION_GOVERNANCE and not isinstance(
        compact.get("governanceSummary"),
        dict,
    ):
        raise KnowledgeReceiptMaintenanceError(
            "governance receipt must include a safe summary"
        )
    if (
        operation_kind == KB_OPERATION_ARCHIVE_DELETE
        and mutation is not None
        and not isinstance(compact.get("governanceSummary"), dict)
    ):
        raise KnowledgeReceiptMaintenanceError(
            "archive delete receipt mutation must include a safe cascade summary"
        )
    return compact


def canonicalize_compact_knowledge_receipt_result(result: Any) -> Dict[str, Any]:
    if not is_compact_knowledge_operation_receipt_result(result):
        raise KnowledgeReceiptMaintenanceError(
            "receipt result is not a supported compact envelope"
        )
    if not isinstance(result, dict):
        raise KnowledgeReceiptMaintenanceError("receipt result must be an object")

    revision = result.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise KnowledgeReceiptMaintenanceError(
            "compact receipt must include a non-negative integer revision"
        )
    mutation_schema_version = result.get("mutationSchemaVersion")
    if (
        mutation_schema_version is not None
        and mutation_schema_version not in (1, 2)
    ):
        raise KnowledgeReceiptMaintenanceError(
            "compact receipt mutation schema version must be 1, 2, or null"
        )
    if "graph" in result or "mutation" in result:
        raise KnowledgeReceiptMaintenanceError(
            "compact receipt must not include graph or mutation content"
        )

    canonical: Dict[str, Any] = {
        "receiptEnvelopeVersion": result["receiptEnvelopeVersion"],
        "revision": revision,
        "mutationSchemaVersion": mutation_schema_version,
    }
    updated_at = result.get("updatedAt")
    if isinstance(updated_at, str) and updated_at:
        canonical["updatedAt"] = updated_at
    if bool(result.get("compatibilityNoOp")):
        canonical["compatibilityNoOp"] = True
    governance_summary = result.get("governanceSummary")
    if governance_summary is not None:
        compact_summary = compact_knowledge_governance_summary(governance_summary)
        if compact_summary is None:
            raise KnowledgeReceiptMaintenanceError(
                "compact receipt governance summary is invalid"
            )
        canonical["governanceSummary"] = compact_summary
    return canonical

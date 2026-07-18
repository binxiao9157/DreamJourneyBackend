"""Adapters from legacy account lifecycle routes to the data-rights contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from app.services.data_rights_contract import (
    DataRightsExecution,
    DataRightsRequest,
    DataRightsRequestAuthority,
)


ACCOUNT_DELETE_SCOPE = (
    "account",
    "archive",
    "voice",
    "knowledge",
    "family",
    "care",
    "echo",
    "notifications",
    "sessions",
)
ACCOUNT_DELETE_MODULE_ID = "account"
ACCOUNT_DELETE_RESOURCE_TYPE = "account"


def make_account_delete_request(
    *,
    command_id: str,
    subject_id: str,
    phone: str,
    lifecycle_marker: str,
    scope: Any = None,
    now: Optional[Any] = None,
) -> DataRightsRequest:
    """Build a redacted request; raw phone and payload never reach the store."""

    selected_scope = list(ACCOUNT_DELETE_SCOPE) if scope is None else scope
    authority = DataRightsRequestAuthority()
    return authority.create_request(
        command_id=command_id,
        subject_id=subject_id,
        identity_proof={"kind": "phoneReconfirmed", "value": phone},
        payload={
            "action": "account.delete",
            "scope": selected_scope,
            "lifecycleMarker": lifecycle_marker,
        },
        now=now,
    ).request


def completed_account_delete_execution(
    request: DataRightsRequest,
    *,
    updated_at: str,
) -> Dict[str, Any]:
    """Return store-safe execution and append-only receipt fields."""

    execution = DataRightsExecution(
        module=ACCOUNT_DELETE_MODULE_ID,
        execution_id_hash=hashlib.sha256(
            f"{request.request_id}:{ACCOUNT_DELETE_MODULE_ID}".encode("utf-8")
        ).hexdigest(),
        outcome="completed",
        evidence_id_hash=None,
        updated_at=updated_at,
    )
    receipt_id = f"rrc_{request.request_id}_{ACCOUNT_DELETE_MODULE_ID}"
    receipt_hash = hashlib.sha256(
        json.dumps(
            {
                "requestId": request.request_id,
                "executionIdHash": execution.execution_id_hash,
                "moduleId": ACCOUNT_DELETE_MODULE_ID,
                "resourceScopeHash": request.scope_hash,
                "outcome": execution.outcome,
                "updatedAt": updated_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "moduleId": ACCOUNT_DELETE_MODULE_ID,
        "resourceType": ACCOUNT_DELETE_RESOURCE_TYPE,
        "executionIdHash": execution.execution_id_hash,
        "outcome": execution.outcome,
        "evidenceIdHash": execution.evidence_id_hash,
        "updatedAt": updated_at,
        "receiptId": receipt_id,
        "resourceScopeHash": request.scope_hash,
        "receiptHash": receipt_hash,
    }


__all__ = [
    "ACCOUNT_DELETE_MODULE_ID",
    "ACCOUNT_DELETE_RESOURCE_TYPE",
    "ACCOUNT_DELETE_SCOPE",
    "completed_account_delete_execution",
    "make_account_delete_request",
]

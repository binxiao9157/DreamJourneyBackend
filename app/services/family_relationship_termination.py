from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import uuid5, NAMESPACE_URL


FAMILY_RELATIONSHIP_TERMINATION_SCHEMA_VERSION = "family-relationship-termination-v1"


class FamilyRelationshipTerminationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class FamilyRelationshipTerminationCommand:
    command_id: str
    relationship_id: str
    actor_subject_id: str
    expected_epoch: int
    second_confirmation: bool
    publication_grant_action: str = "preserve"

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _required(self.command_id, "command_id"))
        object.__setattr__(
            self,
            "relationship_id",
            _required(self.relationship_id, "relationship_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            _required(self.actor_subject_id, "actor_subject_id"),
        )
        if type(self.expected_epoch) is not int or self.expected_epoch < 1:
            raise ValueError("expected_epoch must be at least 1")
        if self.second_confirmation is not True:
            raise ValueError("second_confirmation must be true")
        if self.publication_grant_action != "preserve":
            raise ValueError("publication_grant_action must be preserve")

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "schemaVersion": FAMILY_RELATIONSHIP_TERMINATION_SCHEMA_VERSION,
                    "relationshipId": self.relationship_id,
                    "actorSubjectId": self.actor_subject_id,
                    "expectedEpoch": self.expected_epoch,
                    "secondConfirmation": self.second_confirmation,
                    "publicationGrantAction": self.publication_grant_action,
                }
            )
        )

    @property
    def receipt_id(self) -> str:
        return "frtr_" + uuid5(
            NAMESPACE_URL,
            f"dreamjourney:{self.relationship_id}:{self.command_id_hash}",
        ).hex


@dataclass(frozen=True)
class FamilyRelationshipTerminationResult:
    outcome: str
    receipt: Mapping[str, Any]

    def public_contract(self) -> dict[str, Any]:
        receipt = dict(self.receipt)
        receipt.pop("commandIdHash", None)
        receipt.pop("payloadHash", None)
        return {
            "schemaVersion": FAMILY_RELATIONSHIP_TERMINATION_SCHEMA_VERSION,
            "status": self.outcome,
            "receipt": receipt,
        }


class FamilyRelationshipTerminationService:
    """Atomically ends family authority without deleting either account."""

    def __init__(
        self,
        store: Any,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def terminate(
        self,
        *,
        command: FamilyRelationshipTerminationCommand,
    ) -> FamilyRelationshipTerminationResult:
        relationship = self._store.get_family_relationship_for_participant(
            command.relationship_id,
            command.actor_subject_id,
        )
        if relationship is None:
            raise FamilyRelationshipTerminationError("relationshipParticipantRequired")
        owner_subject_id = str(relationship.get("ownerSubjectId") or "").strip()
        if not owner_subject_id:
            raise FamilyRelationshipTerminationError("relationshipAuthorityInvalid")
        relationship_scope = getattr(
            self._store,
            "delegated_access_relationship_scope",
            None,
        )
        scope = (
            relationship_scope(
                owner_subject_id=owner_subject_id,
                relationship_id=command.relationship_id,
            )
            if callable(relationship_scope)
            else nullcontext()
        )
        with scope:
            with self._store.request_unit_of_work(
                correlation_id=f"family-relationship-termination-{command.relationship_id}",
                command_id=command.command_id_hash,
            ):
                receipt = self._store.terminate_family_relationship(
                    relationship_id=command.relationship_id,
                    actor_subject_id=command.actor_subject_id,
                    expected_epoch=command.expected_epoch,
                    command_id_hash=command.command_id_hash,
                    payload_hash=command.payload_hash,
                    receipt_id=command.receipt_id,
                    terminated_at_iso=self._now_provider().astimezone(timezone.utc).isoformat(),
                )
        error_code = str(receipt.get("errorCode") or "")
        if error_code:
            raise FamilyRelationshipTerminationError(error_code)
        outcome = "deduplicated" if bool(receipt.get("deduplicated")) else str(
            receipt.get("outcome") or "terminated"
        )
        return FamilyRelationshipTerminationResult(outcome=outcome, receipt=receipt)

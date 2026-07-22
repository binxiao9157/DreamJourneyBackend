"""CreateSource contracts for the additive Owner Truth shadow lane.

The command is intentionally narrow: V1 accepts owner-authenticated text
sources only.  It produces an immutable source and append-only receipt without
promoting either legacy Archive or KBLite to Owner Truth authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid5

from .contracts import OwnerTruthContractError, SourceKind, require_nonblank, require_uuid
from .ontology import OWNER_TRUTH_SCHEMA_VERSION


OWNER_TRUTH_CREATE_SOURCE_SCHEMA_VERSION = "owner-truth-create-source-v1"
OWNER_TRUTH_COMMAND_AUTHORIZATION_CAPTURE_SCHEMA_VERSION = (
    "owner-truth-command-authorization-capture-v1"
)
_RECEIPT_NAMESPACE = UUID("c9ebd77d-1e48-4a21-bb64-d5f6e98601d4")
_MAX_TEXT_CHARACTERS = 20_000
_ACCOUNT_GENERATION_HASH_PATTERN = re.compile(r"^[a-f0-9]{24,64}$")
_SHA256_HEX_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class OwnerTruthSourceCommandConflict(OwnerTruthContractError):
    """A stable command or source ID was reused with different meaning."""


class OwnerTruthSourceVersionConflict(OwnerTruthContractError):
    """A create command attempted to write over an existing source version."""

    def __init__(self, *, expected_version: int, current_version: int):
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__("owner truth source version does not match expectedVersion")


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthContractError("source metadata must be JSON serializable") from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _normalized_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthContractError("source metadata must be an object")
    normalized = json.loads(_canonical_json(dict(value)))
    if not isinstance(normalized, dict):  # defensive: json decoding preserves mapping here
        raise OwnerTruthContractError("source metadata must be an object")
    return normalized


@dataclass(frozen=True)
class OwnerTruthCommandAuthorizationCapture:
    """Value-minimized evidence for one server-authorized command effect.

    This deliberately records no bearer token, raw session ID, or raw client
    decision ID. It is an immutable audit attachment for write ledgers that
    need to prove the release-policy and account-generation boundary in force
    when a command was admitted.
    """

    feature: str
    policy_version: str
    policy_revision: int
    emergency_revision: int
    account_generation_hash: str
    decision_id_hash: str
    audience: str
    cohort: str
    client_build: int
    expires_at: str
    schema_version: str = OWNER_TRUTH_COMMAND_AUTHORIZATION_CAPTURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in (
            "feature",
            "policy_version",
            "account_generation_hash",
            "decision_id_hash",
            "audience",
            "cohort",
            "expires_at",
            "schema_version",
        ):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))
        if self.schema_version != OWNER_TRUTH_COMMAND_AUTHORIZATION_CAPTURE_SCHEMA_VERSION:
            raise OwnerTruthContractError("unsupported command authorization capture schema")
        for field in ("policy_revision", "emergency_revision", "client_build"):
            value = getattr(self, field)
            if not isinstance(value, int) or value < 0:
                raise OwnerTruthContractError(f"{field} must be a non-negative integer")
        if not _ACCOUNT_GENERATION_HASH_PATTERN.fullmatch(self.account_generation_hash):
            raise OwnerTruthContractError(
                "account_generation_hash must be a lowercase opaque hash"
            )
        if not _SHA256_HEX_PATTERN.fullmatch(self.decision_id_hash):
            raise OwnerTruthContractError(
                "decision_id_hash must be a lowercase SHA-256 digest"
            )

    def value_minimized_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "feature": self.feature,
            "policyVersion": self.policy_version,
            "policyRevision": self.policy_revision,
            "emergencyRevision": self.emergency_revision,
            "accountGenerationHash": self.account_generation_hash,
            "decisionIdHash": self.decision_id_hash,
            "audience": self.audience,
            "cohort": self.cohort,
            "clientBuild": self.client_build,
            "expiresAt": self.expires_at,
        }

    @classmethod
    def from_value_minimized_payload(
        cls,
        value: Mapping[str, Any],
    ) -> "OwnerTruthCommandAuthorizationCapture":
        if not isinstance(value, Mapping):
            raise OwnerTruthContractError("command authorization capture must be an object")
        return cls(
            feature=str(value.get("feature") or ""),
            policy_version=str(value.get("policyVersion") or ""),
            policy_revision=value.get("policyRevision"),
            emergency_revision=value.get("emergencyRevision"),
            account_generation_hash=str(value.get("accountGenerationHash") or ""),
            decision_id_hash=str(value.get("decisionIdHash") or ""),
            audience=str(value.get("audience") or ""),
            cohort=str(value.get("cohort") or ""),
            client_build=value.get("clientBuild"),
            expires_at=str(value.get("expiresAt") or ""),
            schema_version=str(value.get("schemaVersion") or ""),
        )


@dataclass(frozen=True)
class OwnerTruthCommandContext:
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str = OWNER_TRUTH_SCHEMA_VERSION
    authorization_capture: OwnerTruthCommandAuthorizationCapture | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            require_nonblank(self.actor_subject_id, field="actor_subject_id"),
        )
        object.__setattr__(
            self,
            "policy_version",
            require_nonblank(self.policy_version, field="policy_version"),
        )
        if self.authorization_capture is not None:
            if not isinstance(self.authorization_capture, OwnerTruthCommandAuthorizationCapture):
                raise OwnerTruthContractError("authorization_capture has an invalid type")
            # ``policy_version`` describes the Owner Truth write schema that
            # Candidate/receipt invariants use. The capture records a separate
            # release-policy decision which authorized this command. They may
            # intentionally evolve on different version lines.


@dataclass(frozen=True)
class CreateTextSourceCommand:
    command_id: str
    source_id: str
    expected_version: int
    text: str
    metadata: Mapping[str, Any]
    source_kind: SourceKind = SourceKind.TEXT

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "source_id", require_uuid(self.source_id, field="source_id"))
        if not isinstance(self.expected_version, int) or self.expected_version < 0:
            raise OwnerTruthContractError("expected_version must be a non-negative integer")
        normalized_text = require_nonblank(self.text, field="text")
        if len(normalized_text) > _MAX_TEXT_CHARACTERS:
            raise OwnerTruthContractError("text exceeds maximum source length")
        object.__setattr__(self, "text", normalized_text)
        object.__setattr__(self, "metadata", _normalized_metadata(self.metadata))
        try:
            source_kind = SourceKind(self.source_kind)
        except (TypeError, ValueError) as exc:
            raise OwnerTruthContractError("source_kind is not supported") from exc
        if source_kind not in {SourceKind.TEXT, SourceKind.CONVERSATION}:
            raise OwnerTruthContractError("CreateTextSourceCommand supports text or conversation sources")
        object.__setattr__(self, "source_kind", source_kind)

    def write_record(self, *, context: OwnerTruthCommandContext) -> "OwnerTruthSourceWriteRecord":
        payload = {
            "schemaVersion": OWNER_TRUTH_CREATE_SOURCE_SCHEMA_VERSION,
            "sourceId": self.source_id,
            "expectedVersion": self.expected_version,
            "text": self.text,
            "metadata": self.metadata,
        }
        # Keep legacy text-source command fingerprints stable. Conversation
        # sources are a new, explicit admission path and therefore include
        # their distinct kind in the immutable command/source hashes.
        if self.source_kind is not SourceKind.TEXT:
            payload["sourceKind"] = self.source_kind.value
        payload_hash = _sha256(_canonical_json(payload))
        command_id_hash = _sha256(self.command_id)
        receipt_id = str(
            uuid5(
                _RECEIPT_NAMESPACE,
                f"{context.vault_id}:{command_id_hash}",
            )
        )
        source_payload = {
            "schemaVersion": OWNER_TRUTH_CREATE_SOURCE_SCHEMA_VERSION,
            "text": self.text,
        }
        if self.source_kind is not SourceKind.TEXT:
            source_payload["sourceKind"] = self.source_kind.value
        return OwnerTruthSourceWriteRecord(
            receipt_id=receipt_id,
            command_id_hash=command_id_hash,
            payload_hash=payload_hash,
            source_id=self.source_id,
            expected_version=self.expected_version,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
            content_hash=_sha256(_canonical_json(source_payload)),
            content_payload=source_payload,
            metadata=self.metadata,
            source_kind=self.source_kind,
        )


@dataclass(frozen=True)
class OwnerTruthSourceWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    source_id: str
    expected_version: int
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str
    content_hash: str
    content_payload: Mapping[str, Any]
    metadata: Mapping[str, Any]
    source_kind: SourceKind = SourceKind.TEXT

    def __post_init__(self) -> None:
        try:
            source_kind = SourceKind(self.source_kind)
        except (TypeError, ValueError) as exc:
            raise OwnerTruthContractError("source_kind is not supported") from exc
        if source_kind not in {SourceKind.TEXT, SourceKind.CONVERSATION}:
            raise OwnerTruthContractError("OwnerTruthSourceWriteRecord supports text or conversation sources")
        object.__setattr__(self, "source_kind", source_kind)


@dataclass(frozen=True)
class OwnerTruthSourceCommandResult:
    outcome: str
    receipt_id: str
    source_id: str
    source_version: int
    authority_epoch: int
    content_hash: str

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_CREATE_SOURCE_SCHEMA_VERSION,
            "status": self.outcome,
            "receiptId": self.receipt_id,
            "sourceId": self.source_id,
            "sourceVersion": self.source_version,
            "authorityEpoch": self.authority_epoch,
        }


__all__ = [
    "CreateTextSourceCommand",
    "OWNER_TRUTH_COMMAND_AUTHORIZATION_CAPTURE_SCHEMA_VERSION",
    "OWNER_TRUTH_CREATE_SOURCE_SCHEMA_VERSION",
    "OwnerTruthCommandAuthorizationCapture",
    "OwnerTruthCommandContext",
    "OwnerTruthSourceCommandConflict",
    "OwnerTruthSourceCommandResult",
    "OwnerTruthSourceVersionConflict",
    "OwnerTruthSourceWriteRecord",
]

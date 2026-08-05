"""P2-S4C publication withdrawal cleanup effect boundary.

Local access denial remains the authoritative first step.  This module only
creates durable, value-minimized asynchronous cleanup intents *after* that
boundary has been recorded.  It does not call an index, cache, Digital Human,
voice, or object-storage provider and therefore cannot claim cleanup is done.

The generic async-effect and provider-effect stores own durable operation and
provider coordinates.  This module adds the publication-lifecycle association
and a redacted state projection so a later worker can update one domain without
weakening a completed local denial.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effects import (
    ProviderEffectIntent,
    ProviderEffectReceipt,
    ProviderEffectState,
)


PUBLICATION_EXTERNAL_CLEANUP_SCHEMA_VERSION = "publication-external-cleanup-v1"
PUBLICATION_EXTERNAL_CLEANUP_OPERATION_TYPE = "publication.lifecycle.externalCleanup"
PUBLICATION_EXTERNAL_CLEANUP_EVENT_TYPE = "publication.lifecycle.externalCleanupRequested"
PUBLICATION_EXTERNAL_CLEANUP_JOB_TYPE = "publication.lifecycle.externalCleanup"
PUBLICATION_EXTERNAL_CLEANUP_MAX_ATTEMPTS = 3
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_UUID_NAMESPACE = UUID("6d118fe8-1325-4e87-9b81-3686457b9f64")


class PublicationExternalCleanupError(ValueError):
    """A lifecycle cleanup effect would violate its deny-first boundary."""


class PublicationExternalCleanupDomain(str, Enum):
    PUBLIC_INDEX = "publicIndex"
    CACHE = "cache"
    DIGITAL_HUMAN_SESSION = "digitalHumanSession"
    PROVIDER_VOICE = "providerVoice"
    OBJECT_STORAGE = "objectStorage"


class PublicationExternalCleanupState(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    COMPLETED = "completed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PublicationExternalCleanupStatus:
    """One redacted lifecycle cleanup status safe for an internal QA receipt."""

    domain: PublicationExternalCleanupDomain
    state: PublicationExternalCleanupState
    reason_code: str
    provider_receipt_present: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", PublicationExternalCleanupDomain(self.domain))
        object.__setattr__(self, "state", PublicationExternalCleanupState(self.state))
        reason_code = str(self.reason_code or "").strip()
        if not _IDENTIFIER_PATTERN.fullmatch(reason_code):
            raise PublicationExternalCleanupError("reason_code must be an opaque identifier")
        object.__setattr__(self, "reason_code", reason_code)
        if not isinstance(self.provider_receipt_present, bool):
            raise PublicationExternalCleanupError("provider_receipt_present must be boolean")
        if self.state is PublicationExternalCleanupState.COMPLETED and not self.provider_receipt_present:
            raise PublicationExternalCleanupError(
                "completed cleanup requires an independent provider receipt"
            )

    def public_contract(self) -> dict[str, object]:
        """Return no effect id, provider id, publication id, or subject value."""

        return {
            "domain": self.domain.value,
            "providerReceiptPresent": self.provider_receipt_present,
            "reasonCode": self.reason_code,
            "state": self.state.value,
        }


@dataclass(frozen=True)
class PublicationExternalCleanupEffect:
    """Private coordinates linking one lifecycle receipt to a generic effect."""

    lifecycle_receipt_id: str
    vault_id: str
    publication_id: str
    publication_version_id: str
    domain: PublicationExternalCleanupDomain
    intent: AsyncEffectIntent
    provider_intent: ProviderEffectIntent

    def __post_init__(self) -> None:
        for field_name in (
            "lifecycle_receipt_id",
            "publication_id",
            "publication_version_id",
        ):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field_name))
        vault_id = str(self.vault_id or "").strip()
        if not _IDENTIFIER_PATTERN.fullmatch(vault_id):
            raise PublicationExternalCleanupError("vault_id must be an opaque identifier")
        object.__setattr__(self, "vault_id", vault_id)
        object.__setattr__(self, "domain", PublicationExternalCleanupDomain(self.domain))
        if not isinstance(self.intent, AsyncEffectIntent) or not isinstance(
            self.provider_intent, ProviderEffectIntent
        ):
            raise PublicationExternalCleanupError("async and provider intents are required")


@dataclass(frozen=True)
class PublicationExternalCleanupMaterializationTarget:
    """One already-denied lifecycle receipt that still needs effect coordinates.

    This is intentionally private worker input.  It has no rendered/public
    summary because it contains owner and publication coordinates needed only
    to create stable async-effect identities.
    """

    lifecycle_receipt_id: str
    vault_id: str
    owner_subject_id: str
    publication_id: str
    publication_version_id: str
    authority_epoch: int
    action: str
    reason_code: str

    def __post_init__(self) -> None:
        for field_name in (
            "lifecycle_receipt_id",
            "publication_id",
            "publication_version_id",
        ):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field_name))
        for field_name in ("vault_id", "owner_subject_id", "reason_code"):
            normalized = str(getattr(self, field_name) or "").strip()
            if not _IDENTIFIER_PATTERN.fullmatch(normalized):
                raise PublicationExternalCleanupError(
                    f"{field_name} must be an opaque identifier"
                )
            object.__setattr__(self, field_name, normalized)
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, "authority_epoch"),
        )
        action = str(self.action or "").strip()
        if action not in {"withdraw", "suspend", "systemSuspend"}:
            raise PublicationExternalCleanupError(
                "action must be withdraw, suspend, or systemSuspend"
            )
        object.__setattr__(self, "action", action)


@dataclass(frozen=True)
class _CleanupSpec:
    domain: PublicationExternalCleanupDomain
    provider: str
    capability: str
    purpose: str


_CLEANUP_SPECS: tuple[_CleanupSpec, ...] = (
    _CleanupSpec(
        PublicationExternalCleanupDomain.PUBLIC_INDEX,
        "publicationPublicIndex",
        "publicationPublicIndexCleanup",
        "publicationPublicIndexCleanup",
    ),
    _CleanupSpec(
        PublicationExternalCleanupDomain.CACHE,
        "publicationCache",
        "publicationCacheCleanup",
        "publicationCacheCleanup",
    ),
    _CleanupSpec(
        PublicationExternalCleanupDomain.DIGITAL_HUMAN_SESSION,
        "tencentDigitalHuman",
        "publicationDigitalHumanSessionCleanup",
        "publicationDigitalHumanSessionCleanup",
    ),
    _CleanupSpec(
        PublicationExternalCleanupDomain.PROVIDER_VOICE,
        "volcengineVoice",
        "publicationVoiceCleanup",
        "publicationVoiceCleanup",
    ),
    _CleanupSpec(
        PublicationExternalCleanupDomain.OBJECT_STORAGE,
        "objectStorage",
        "publicationObjectStorageCleanup",
        "publicationObjectStorageCleanup",
    ),
)


def _uuid(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationExternalCleanupError(f"{field_name} must be a UUID") from exc


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationExternalCleanupError(f"{field_name} must be a non-negative integer")
    return value


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _provider_receipt_hash(value: object | None) -> str | None:
    """Normalize the value-free hash of an independently observed receipt."""

    if value is None:
        return None
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationExternalCleanupError(
            "provider_receipt_hash must be a lowercase SHA-256 digest"
        )
    return normalized


def _effect_id(*, lifecycle_receipt_id: str, domain: PublicationExternalCleanupDomain) -> str:
    return str(uuid5(_UUID_NAMESPACE, f"publication-external-cleanup:{lifecycle_receipt_id}:{domain.value}"))


def _receipt_id(
    *,
    lifecycle_receipt_id: str,
    domain: PublicationExternalCleanupDomain,
    state: PublicationExternalCleanupState,
    reason_code: str,
    provider_receipt_present: bool,
    provider_receipt_hash: str | None,
) -> tuple[str, str]:
    observation_hash = _hash(
        {
            "domain": domain.value,
            "lifecycleReceiptId": lifecycle_receipt_id,
            "providerReceiptPresent": provider_receipt_present,
            "providerReceiptHash": provider_receipt_hash,
            "reasonCode": reason_code,
            "schemaVersion": PUBLICATION_EXTERNAL_CLEANUP_SCHEMA_VERSION,
            "state": state.value,
        }
    )
    return str(uuid5(_UUID_NAMESPACE, f"publication-external-cleanup-receipt:{observation_hash}")), observation_hash


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_publication_external_cleanup_effects(
    *,
    lifecycle_receipt_id: str,
    vault_id: str,
    owner_subject_id: str,
    publication_id: str,
    publication_version_id: str,
    authority_epoch: int,
    action: str,
    reason_code: str,
) -> tuple[PublicationExternalCleanupEffect, ...]:
    """Create one stable, value-minimized effect intent per cleanup domain."""

    lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
    publication_id = _uuid(publication_id, "publication_id")
    publication_version_id = _uuid(publication_version_id, "publication_version_id")
    normalized_vault_id = str(vault_id or "").strip()
    normalized_owner = str(owner_subject_id or "").strip()
    normalized_action = str(action or "").strip()
    normalized_reason = str(reason_code or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized_vault_id):
        raise PublicationExternalCleanupError("vault_id must be an opaque identifier")
    if not _IDENTIFIER_PATTERN.fullmatch(normalized_owner):
        raise PublicationExternalCleanupError("owner_subject_id must be an opaque identifier")
    if normalized_action not in {"withdraw", "suspend", "systemSuspend"}:
        raise PublicationExternalCleanupError(
            "action must be withdraw, suspend, or systemSuspend"
        )
    if not _IDENTIFIER_PATTERN.fullmatch(normalized_reason):
        raise PublicationExternalCleanupError("reason_code must be an opaque identifier")
    authority_epoch = _nonnegative_int(authority_epoch, "authority_epoch")

    effects: list[PublicationExternalCleanupEffect] = []
    for spec in _CLEANUP_SPECS:
        intent = AsyncEffectIntent(
            operation_type=PUBLICATION_EXTERNAL_CLEANUP_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=normalized_owner,
                vault_id=normalized_vault_id,
                resource_type="publicationLifecycle",
                resource_id=publication_id,
                resource_version=authority_epoch,
                purpose=spec.purpose,
                authority_epoch=authority_epoch,
            ),
            payload_hash=_hash(
                {
                    "action": normalized_action,
                    "domain": spec.domain.value,
                    "lifecycleReceiptId": lifecycle_receipt_id,
                    "publicationVersionId": publication_version_id,
                    "reasonCode": normalized_reason,
                    "schemaVersion": PUBLICATION_EXTERNAL_CLEANUP_SCHEMA_VERSION,
                }
            ),
            event_type=PUBLICATION_EXTERNAL_CLEANUP_EVENT_TYPE,
            job_type=PUBLICATION_EXTERNAL_CLEANUP_JOB_TYPE,
            max_attempts=PUBLICATION_EXTERNAL_CLEANUP_MAX_ATTEMPTS,
        )
        provider_intent = ProviderEffectIntent(
            effect_intent=intent,
            provider=spec.provider,
            capability=spec.capability,
            request_hash=_hash(
                {
                    "action": normalized_action,
                    "domain": spec.domain.value,
                    "lifecycleReceiptId": lifecycle_receipt_id,
                    "operationStableKey": intent.stable_key,
                    "schemaVersion": PUBLICATION_EXTERNAL_CLEANUP_SCHEMA_VERSION,
                }
            ),
        )
        effects.append(
            PublicationExternalCleanupEffect(
                lifecycle_receipt_id=lifecycle_receipt_id,
                vault_id=normalized_vault_id,
                publication_id=publication_id,
                publication_version_id=publication_version_id,
                domain=spec.domain,
                intent=intent,
                provider_intent=provider_intent,
            )
        )
    return tuple(effects)


class PublicationExternalCleanupRepository(Protocol):
    def record_accepted(
        self,
        effect: PublicationExternalCleanupEffect,
        *,
        reason_code: str,
        observed_at: datetime,
    ) -> PublicationExternalCleanupStatus:
        ...

    def list_statuses(self, lifecycle_receipt_id: str) -> tuple[PublicationExternalCleanupStatus, ...]:
        ...

    def list_pending_materializations(
        self,
        *,
        limit: int,
    ) -> tuple[PublicationExternalCleanupMaterializationTarget, ...]:
        ...

    def materialization_target(
        self,
        lifecycle_receipt_id: str,
    ) -> PublicationExternalCleanupMaterializationTarget:
        ...


class InMemoryPublicationExternalCleanupRepository:
    """Semantic double retaining no public IDs in its status projection."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._effects: dict[tuple[str, PublicationExternalCleanupDomain], dict[str, Any]] = {}
        self._receipts: dict[str, dict[str, Any]] = {}

    def record_accepted(
        self,
        effect: PublicationExternalCleanupEffect,
        *,
        reason_code: str,
        observed_at: datetime,
    ) -> PublicationExternalCleanupStatus:
        key = (effect.lifecycle_receipt_id, effect.domain)
        with self._lock:
            existing = self._effects.get(key)
            if existing is not None:
                if existing["effect"] != effect:
                    raise PublicationExternalCleanupError("lifecycle cleanup effect cannot be rebound")
                # A replay after a later terminal receipt must never reset the
                # current cleanup state back to pending.
                return _status_from_record(existing)
        return self._record(
            effect=effect,
            state=PublicationExternalCleanupState.PENDING,
            reason_code=reason_code,
            provider_receipt_hash=None,
            observed_at=observed_at,
        )

    def record_outcome(
        self,
        *,
        lifecycle_receipt_id: str,
        domain: PublicationExternalCleanupDomain,
        state: PublicationExternalCleanupState,
        reason_code: str,
        provider_receipt_hash: str | None,
        observed_at: datetime | None = None,
    ) -> PublicationExternalCleanupStatus:
        lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
        domain = PublicationExternalCleanupDomain(domain)
        state = PublicationExternalCleanupState(state)
        provider_receipt_hash = _provider_receipt_hash(provider_receipt_hash)
        provider_receipt_present = provider_receipt_hash is not None
        with self._lock:
            effect = self._effects.get((lifecycle_receipt_id, domain))
            if effect is None:
                raise PublicationExternalCleanupError("lifecycle cleanup effect is unavailable")
            existing = _status_from_record(effect)
            candidate = PublicationExternalCleanupStatus(
                domain=domain,
                state=state,
                reason_code=reason_code,
                provider_receipt_present=provider_receipt_present,
            )
            if existing.state is not PublicationExternalCleanupState.PENDING and (
                existing != candidate
                or effect.get("providerReceiptHash") != provider_receipt_hash
            ):
                raise PublicationExternalCleanupError("terminal cleanup outcome cannot be replaced")
            return self._record(
                effect=effect["effect"],
                state=state,
                reason_code=reason_code,
                provider_receipt_hash=provider_receipt_hash,
                observed_at=observed_at or _utc_now(),
            )

    def list_statuses(self, lifecycle_receipt_id: str) -> tuple[PublicationExternalCleanupStatus, ...]:
        lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
        with self._lock:
            statuses = [
                _status_from_record(record)
                for (receipt_id, _domain), record in self._effects.items()
                if receipt_id == lifecycle_receipt_id
            ]
        return tuple(sorted(statuses, key=lambda item: item.domain.value))

    def effect_count(self, lifecycle_receipt_id: str) -> int:
        lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
        with self._lock:
            return sum(1 for receipt_id, _domain in self._effects if receipt_id == lifecycle_receipt_id)

    def list_pending_materializations(
        self,
        *,
        limit: int,
    ) -> tuple[PublicationExternalCleanupMaterializationTarget, ...]:
        # The in-memory lifecycle repository materializes immediately.  Keeping
        # this port empty documents the production worker contract without
        # inventing a second source of truth in the semantic double.
        del limit
        return ()

    def materialization_target(
        self,
        lifecycle_receipt_id: str,
    ) -> PublicationExternalCleanupMaterializationTarget:
        raise PublicationExternalCleanupError(
            "in-memory cleanup materialization is attached directly by its lifecycle test double"
        )

    def _record(
        self,
        *,
        effect: PublicationExternalCleanupEffect,
        state: PublicationExternalCleanupState,
        reason_code: str,
        provider_receipt_hash: str | None,
        observed_at: datetime,
    ) -> PublicationExternalCleanupStatus:
        provider_receipt_hash = _provider_receipt_hash(provider_receipt_hash)
        provider_receipt_present = provider_receipt_hash is not None
        status = PublicationExternalCleanupStatus(
            domain=effect.domain,
            state=state,
            reason_code=reason_code,
            provider_receipt_present=provider_receipt_present,
        )
        receipt_id, observation_hash = _receipt_id(
            lifecycle_receipt_id=effect.lifecycle_receipt_id,
            domain=effect.domain,
            state=state,
            reason_code=status.reason_code,
            provider_receipt_present=provider_receipt_present,
            provider_receipt_hash=provider_receipt_hash,
        )
        key = (effect.lifecycle_receipt_id, effect.domain)
        with self._lock:
            existing = self._effects.get(key)
            if existing is None:
                self._effects[key] = {
                    "effect": effect,
                    "status": status,
                    "providerReceiptHash": provider_receipt_hash,
                    "receiptIds": [receipt_id],
                }
            else:
                if existing["effect"] != effect:
                    raise PublicationExternalCleanupError("lifecycle cleanup effect cannot be rebound")
                existing["status"] = status
                existing["providerReceiptHash"] = provider_receipt_hash
                if receipt_id not in existing["receiptIds"]:
                    existing["receiptIds"].append(receipt_id)
            self._receipts.setdefault(
                receipt_id,
                {
                    "observationHash": observation_hash,
                    "observedAt": observed_at.astimezone(timezone.utc).isoformat(),
                    "status": status,
                },
            )
        return status


class PostgresPublicationExternalCleanupRepository:
    """Publication cleanup receipt writer bound to an active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record_accepted(
        self,
        effect: PublicationExternalCleanupEffect,
        *,
        reason_code: str,
        observed_at: datetime,
    ) -> PublicationExternalCleanupStatus:
        return self._record(
            effect=effect,
            state=PublicationExternalCleanupState.PENDING,
            reason_code=reason_code,
            provider_receipt_hash=None,
            observed_at=observed_at,
            preserve_existing_terminal=True,
        )

    def record_outcome(
        self,
        *,
        lifecycle_receipt_id: str,
        domain: PublicationExternalCleanupDomain,
        state: PublicationExternalCleanupState,
        reason_code: str,
        provider_receipt_hash: str | None,
        observed_at: datetime | None = None,
    ) -> PublicationExternalCleanupStatus:
        lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
        domain = PublicationExternalCleanupDomain(domain)
        state = PublicationExternalCleanupState(state)
        provider_receipt_hash = _provider_receipt_hash(provider_receipt_hash)
        provider_receipt_present = provider_receipt_hash is not None
        with self._cursor() as cursor:
            row = self._select_effect_row(
                cursor,
                lifecycle_receipt_id=lifecycle_receipt_id,
                domain=domain,
            )
            if row is None:
                raise PublicationExternalCleanupError("lifecycle cleanup effect is unavailable")
            effect = self._effect_from_row(row)
            current = _status_from_row(row)
            candidate = PublicationExternalCleanupStatus(
                domain=domain,
                state=state,
                reason_code=reason_code,
                provider_receipt_present=provider_receipt_present,
            )
            if current.state is not PublicationExternalCleanupState.PENDING and (
                current != candidate
                or row.get("provider_receipt_hash") != provider_receipt_hash
            ):
                raise PublicationExternalCleanupError("terminal cleanup outcome cannot be replaced")
            return self._record(
                effect=effect,
                state=state,
                reason_code=reason_code,
                provider_receipt_hash=provider_receipt_hash,
                observed_at=observed_at or _utc_now(),
                locked=True,
                link_effect_id=str(row["effect_id"]),
            )

    def list_statuses(self, lifecycle_receipt_id: str) -> tuple[PublicationExternalCleanupStatus, ...]:
        lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT domain, state, reason_code, provider_receipt_present
                FROM publication.lifecycle_external_cleanup_effects
                WHERE lifecycle_receipt_id = %s
                ORDER BY domain ASC
                """,
                (lifecycle_receipt_id,),
            )
            rows = cursor.fetchall()
        return tuple(_status_from_row(row) for row in rows)

    def list_pending_materializations(
        self,
        *,
        limit: int,
    ) -> tuple[PublicationExternalCleanupMaterializationTarget, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise PublicationExternalCleanupError("materialization limit must be between 1 and 100")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT receipt.id AS lifecycle_receipt_id,
                    receipt.vault_id,
                    receipt.owner_subject_id,
                    receipt.publication_id,
                    receipt.publication_version_id,
                    receipt.authority_epoch,
                    receipt.action,
                    receipt.reason_code
                FROM publication.publication_lifecycle_receipts AS receipt
                WHERE receipt.access_deny_state = 'completed'
                  AND receipt.publication_state IN ('withdrawn', 'suspended')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM publication.lifecycle_external_cleanup_effects AS link
                      WHERE link.lifecycle_receipt_id = receipt.id
                  )
                ORDER BY receipt.created_at ASC, receipt.id ASC
                FOR UPDATE OF receipt SKIP LOCKED
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return tuple(
            PublicationExternalCleanupMaterializationTarget(
                lifecycle_receipt_id=str(row["lifecycle_receipt_id"]),
                vault_id=str(row["vault_id"]),
                owner_subject_id=str(row["owner_subject_id"]),
                publication_id=str(row["publication_id"]),
                publication_version_id=str(row["publication_version_id"]),
                authority_epoch=int(row["authority_epoch"]),
                action=str(row["action"]),
                reason_code=str(row["reason_code"]),
            )
            for row in rows
        )

    def materialization_target(
        self,
        lifecycle_receipt_id: str,
    ) -> PublicationExternalCleanupMaterializationTarget:
        lifecycle_receipt_id = _uuid(lifecycle_receipt_id, "lifecycle_receipt_id")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id AS lifecycle_receipt_id,
                    vault_id,
                    owner_subject_id,
                    publication_id,
                    publication_version_id,
                    authority_epoch,
                    action,
                    reason_code
                FROM publication.publication_lifecycle_receipts
                WHERE id = %s
                  AND access_deny_state = 'completed'
                  AND publication_state IN ('withdrawn', 'suspended')
                FOR UPDATE
                """,
                (lifecycle_receipt_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise PublicationExternalCleanupError(
                "external cleanup requires an already-denied lifecycle receipt"
            )
        return PublicationExternalCleanupMaterializationTarget(
            lifecycle_receipt_id=str(row["lifecycle_receipt_id"]),
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            publication_id=str(row["publication_id"]),
            publication_version_id=str(row["publication_version_id"]),
            authority_epoch=int(row["authority_epoch"]),
            action=str(row["action"]),
            reason_code=str(row["reason_code"]),
        )

    def effect_for_job(self, operation_id: str) -> PublicationExternalCleanupEffect:
        """Load a private effect only for the worker's already-leased operation."""

        normalized_operation_id = _uuid(operation_id, "operation_id")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT link.effect_id, link.lifecycle_receipt_id, link.vault_id,
                    link.publication_id, link.publication_version_id, link.domain,
                    link.operation_id, link.provider_effect_id, link.effect_identity_hash,
                    link.state, link.reason_code, link.provider_receipt_present,
                    link.provider_receipt_hash,
                    operation.owner_subject_id AS operation_owner_subject_id,
                    operation.resource_version AS operation_resource_version,
                    operation.payload_hash AS operation_payload_hash,
                    provider.provider_name, provider.capability,
                    provider.request_hash AS provider_request_hash,
                    provider.contract_version AS provider_contract_version
                FROM publication.lifecycle_external_cleanup_effects AS link
                JOIN async_effects.operations AS operation
                  ON operation.operation_id = link.operation_id
                JOIN async_effects.provider_effects AS provider
                  ON provider.effect_id = link.provider_effect_id
                WHERE link.operation_id = %s
                FOR UPDATE OF link, operation, provider
                """,
                (normalized_operation_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise PublicationExternalCleanupError("lifecycle cleanup effect is unavailable")
        return self._effect_from_row(row)

    def _record(
        self,
        *,
        effect: PublicationExternalCleanupEffect,
        state: PublicationExternalCleanupState,
        reason_code: str,
        provider_receipt_hash: str | None,
        observed_at: datetime,
        locked: bool = False,
        preserve_existing_terminal: bool = False,
        link_effect_id: str | None = None,
    ) -> PublicationExternalCleanupStatus:
        provider_receipt_hash = _provider_receipt_hash(provider_receipt_hash)
        provider_receipt_present = provider_receipt_hash is not None
        status = PublicationExternalCleanupStatus(
            domain=effect.domain,
            state=state,
            reason_code=reason_code,
            provider_receipt_present=provider_receipt_present,
        )
        receipt_id, observation_hash = _receipt_id(
            lifecycle_receipt_id=effect.lifecycle_receipt_id,
            domain=effect.domain,
            state=status.state,
            reason_code=status.reason_code,
            provider_receipt_present=status.provider_receipt_present,
            provider_receipt_hash=provider_receipt_hash,
        )
        with self._cursor() as cursor:
            resolved_link_effect_id: str
            if not locked:
                cursor.execute(
                    """
                    SELECT access_deny_state, publication_state, projection_state
                    FROM publication.publication_lifecycle_receipts
                    WHERE id = %s
                      AND vault_id = %s
                      AND publication_id = %s
                      AND publication_version_id = %s
                    FOR UPDATE
                    """,
                    (
                        effect.lifecycle_receipt_id,
                        effect.vault_id,
                        effect.publication_id,
                        effect.publication_version_id,
                    ),
                )
                lifecycle = cursor.fetchone()
                if lifecycle is None or str(lifecycle["access_deny_state"]) != "completed":
                    raise PublicationExternalCleanupError(
                        "external cleanup requires completed local access denial"
                    )
                if str(lifecycle["publication_state"]) not in {"withdrawn", "suspended"} or str(
                    lifecycle["projection_state"]
                ) not in {"withdrawn", "suspended", "blocked"}:
                    raise PublicationExternalCleanupError(
                        "external cleanup requires an unavailable public projection"
                    )
                cursor.execute(
                    """
                    INSERT INTO publication.lifecycle_external_cleanup_effects (
                        effect_id, lifecycle_receipt_id, vault_id, publication_id,
                        publication_version_id, domain, operation_id, provider_effect_id,
                        effect_identity_hash, state, reason_code, provider_receipt_present,
                        provider_receipt_hash, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (lifecycle_receipt_id, domain) DO NOTHING
                    RETURNING effect_id, lifecycle_receipt_id, vault_id, publication_id,
                        publication_version_id, domain, operation_id, provider_effect_id,
                        effect_identity_hash, state, reason_code, provider_receipt_present,
                        provider_receipt_hash
                    """,
                    (
                        _effect_id(
                            lifecycle_receipt_id=effect.lifecycle_receipt_id,
                            domain=effect.domain,
                        ),
                        effect.lifecycle_receipt_id,
                        effect.vault_id,
                        effect.publication_id,
                        effect.publication_version_id,
                        effect.domain.value,
                        effect.intent.operation_id,
                        effect.provider_intent.provider_effect_id,
                        _hash(
                            {
                                "domain": effect.domain.value,
                                "lifecycleReceiptId": effect.lifecycle_receipt_id,
                                "operationStableKey": effect.intent.stable_key,
                            }
                        ),
                        status.state.value,
                        status.reason_code,
                        status.provider_receipt_present,
                        provider_receipt_hash,
                        observed_at,
                        observed_at,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    existing = self._select_effect_row(
                        cursor,
                        lifecycle_receipt_id=effect.lifecycle_receipt_id,
                        domain=effect.domain,
                    )
                    if existing is None or not self._same_effect_row(existing, effect):
                        raise PublicationExternalCleanupError("lifecycle cleanup effect cannot be rebound")
                    existing_status = _status_from_row(existing)
                    if existing_status != status:
                        if preserve_existing_terminal:
                            return existing_status
                        raise PublicationExternalCleanupError("terminal cleanup outcome cannot be replaced")
                    resolved_link_effect_id = str(existing["effect_id"])
                else:
                    resolved_link_effect_id = str(inserted["effect_id"])
            else:
                if link_effect_id is None:
                    raise PublicationExternalCleanupError(
                        "locked cleanup update requires its durable effect id"
                    )
                resolved_link_effect_id = _uuid(link_effect_id, "link_effect_id")
            cursor.execute(
                """
                INSERT INTO publication.lifecycle_external_cleanup_receipts (
                    id, effect_id, state, reason_code, provider_receipt_present,
                    provider_receipt_hash, observation_hash, observed_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observation_hash) DO NOTHING
                """,
                (
                    receipt_id,
                    resolved_link_effect_id,
                    status.state.value,
                    status.reason_code,
                    status.provider_receipt_present,
                    provider_receipt_hash,
                    observation_hash,
                    observed_at,
                    observed_at,
                ),
            )
            cursor.execute(
                """
                UPDATE publication.lifecycle_external_cleanup_effects
                SET state = %s,
                    reason_code = %s,
                    provider_receipt_present = %s,
                    provider_receipt_hash = %s,
                    updated_at = %s
                WHERE lifecycle_receipt_id = %s AND domain = %s
                """,
                (
                    status.state.value,
                    status.reason_code,
                    status.provider_receipt_present,
                    provider_receipt_hash,
                    observed_at,
                    effect.lifecycle_receipt_id,
                    effect.domain.value,
                ),
            )
        return status

    def _select_effect_row(
        self,
        cursor: Any,
        *,
        lifecycle_receipt_id: str,
        domain: PublicationExternalCleanupDomain,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT link.effect_id, link.lifecycle_receipt_id, link.vault_id,
                link.publication_id, link.publication_version_id, link.domain,
                link.operation_id, link.provider_effect_id, link.effect_identity_hash,
                link.state, link.reason_code, link.provider_receipt_present,
                link.provider_receipt_hash,
                operation.owner_subject_id AS operation_owner_subject_id,
                operation.resource_version AS operation_resource_version,
                operation.payload_hash AS operation_payload_hash,
                provider.provider_name, provider.capability,
                provider.request_hash AS provider_request_hash,
                provider.contract_version AS provider_contract_version
            FROM publication.lifecycle_external_cleanup_effects AS link
            JOIN async_effects.operations AS operation
              ON operation.operation_id = link.operation_id
            JOIN async_effects.provider_effects AS provider
              ON provider.effect_id = link.provider_effect_id
            WHERE link.lifecycle_receipt_id = %s AND link.domain = %s
            FOR UPDATE OF link, operation, provider
            """,
            (lifecycle_receipt_id, domain.value),
        )
        row = cursor.fetchone()
        return None if row is None else dict(row)

    @staticmethod
    def _same_effect_row(row: Mapping[str, Any], effect: PublicationExternalCleanupEffect) -> bool:
        return (
            str(row["lifecycle_receipt_id"]) == effect.lifecycle_receipt_id
            and str(row["vault_id"]) == effect.vault_id
            and str(row["publication_id"]) == effect.publication_id
            and str(row["publication_version_id"]) == effect.publication_version_id
            and str(row["domain"]) == effect.domain.value
            and str(row["operation_id"]) == effect.intent.operation_id
            and str(row["provider_effect_id"]) == effect.provider_intent.provider_effect_id
        )

    @staticmethod
    def _effect_from_row(row: Mapping[str, Any]) -> PublicationExternalCleanupEffect:
        domain = PublicationExternalCleanupDomain(str(row["domain"]))
        purpose = _purpose_for_domain(domain)
        intent = AsyncEffectIntent(
            operation_type=PUBLICATION_EXTERNAL_CLEANUP_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=str(row["operation_owner_subject_id"]),
                vault_id=str(row["vault_id"]),
                resource_type="publicationLifecycle",
                resource_id=str(row["publication_id"]),
                resource_version=int(row["operation_resource_version"]),
                purpose=purpose,
                authority_epoch=int(row["operation_resource_version"]),
            ),
            payload_hash=str(row["operation_payload_hash"]),
            event_type=PUBLICATION_EXTERNAL_CLEANUP_EVENT_TYPE,
            job_type=PUBLICATION_EXTERNAL_CLEANUP_JOB_TYPE,
            max_attempts=PUBLICATION_EXTERNAL_CLEANUP_MAX_ATTEMPTS,
        )
        if str(row["operation_id"]) != intent.operation_id:
            raise PublicationExternalCleanupError("cleanup operation identity is inconsistent")
        provider_intent = ProviderEffectIntent(
            effect_intent=intent,
            provider=str(row["provider_name"]),
            capability=str(row["capability"]),
            request_hash=str(row["provider_request_hash"]),
            contract_version=str(row["provider_contract_version"]),
        )
        if str(row["provider_effect_id"]) != provider_intent.provider_effect_id:
            raise PublicationExternalCleanupError("cleanup provider effect identity is inconsistent")
        return PublicationExternalCleanupEffect(
            lifecycle_receipt_id=str(row["lifecycle_receipt_id"]),
            vault_id=str(row["vault_id"]),
            publication_id=str(row["publication_id"]),
            publication_version_id=str(row["publication_version_id"]),
            domain=domain,
            intent=intent,
            provider_intent=provider_intent,
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class PublicationExternalCleanupCoordinator:
    """Append generic async intents only after a lifecycle receipt exists."""

    def __init__(
        self,
        *,
        effect_repository: Any,
        provider_effect_repository: Any,
        cleanup_repository: PublicationExternalCleanupRepository,
    ) -> None:
        self._effect_repository = effect_repository
        self._provider_effect_repository = provider_effect_repository
        self._cleanup_repository = cleanup_repository

    def enqueue_after_access_deny(
        self,
        *,
        lifecycle_receipt_id: str,
        vault_id: str,
        owner_subject_id: str,
        publication_id: str,
        publication_version_id: str,
        authority_epoch: int,
        action: str,
        reason_code: str,
        observed_at: datetime | None = None,
    ) -> tuple[PublicationExternalCleanupStatus, ...]:
        """Queue every known domain and return only redacted initial states."""

        timestamp = (observed_at or _utc_now()).astimezone(timezone.utc)
        effects = build_publication_external_cleanup_effects(
            lifecycle_receipt_id=lifecycle_receipt_id,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            publication_id=publication_id,
            publication_version_id=publication_version_id,
            authority_epoch=authority_epoch,
            action=action,
            reason_code=reason_code,
        )
        existing_statuses = {
            status.domain: status
            for status in self._cleanup_repository.list_statuses(lifecycle_receipt_id)
        }
        for effect in effects:
            # A completed/partial/unsupported local receipt already has a
            # durable link to its generic operation and Provider effect. Do
            # not replay the initial `unknown` provider observation, because
            # that would conflict with a later independent Provider receipt.
            if effect.domain in existing_statuses:
                self._cleanup_repository.record_accepted(
                    effect,
                    reason_code="publicationExternalCleanupQueued",
                    observed_at=timestamp,
                )
                continue
            self._effect_repository.accept(effect.intent)
            # Unknown means no provider call or upstream receipt has happened;
            # it is intentionally not converted to a completion claim.
            self._provider_effect_repository.record(
                ProviderEffectReceipt(
                    intent=effect.provider_intent,
                    state=ProviderEffectState.UNKNOWN,
                    reason_code="publicationExternalCleanupProviderNotAttempted",
                    observation_origin="localAcceptance",
                )
            )
            self._cleanup_repository.record_accepted(
                effect,
                reason_code="publicationExternalCleanupQueued",
                observed_at=timestamp,
            )
        return self._cleanup_repository.list_statuses(lifecycle_receipt_id)

    def materialize(
        self,
        target: PublicationExternalCleanupMaterializationTarget,
        *,
        observed_at: datetime | None = None,
    ) -> tuple[PublicationExternalCleanupStatus, ...]:
        """Bind a pre-existing deny receipt to its stable generic effects."""

        if not isinstance(target, PublicationExternalCleanupMaterializationTarget):
            raise PublicationExternalCleanupError("lifecycle materialization target is required")
        return self.enqueue_after_access_deny(
            lifecycle_receipt_id=target.lifecycle_receipt_id,
            vault_id=target.vault_id,
            owner_subject_id=target.owner_subject_id,
            publication_id=target.publication_id,
            publication_version_id=target.publication_version_id,
            authority_epoch=target.authority_epoch,
            action=target.action,
            reason_code=target.reason_code,
            observed_at=observed_at,
        )


def _purpose_for_domain(domain: PublicationExternalCleanupDomain) -> str:
    return next(spec.purpose for spec in _CLEANUP_SPECS if spec.domain is domain)


def _status_from_record(record: Mapping[str, Any]) -> PublicationExternalCleanupStatus:
    status = record.get("status")
    if not isinstance(status, PublicationExternalCleanupStatus):
        raise PublicationExternalCleanupError("stored lifecycle cleanup status is invalid")
    return status


def _status_from_row(row: Mapping[str, Any]) -> PublicationExternalCleanupStatus:
    return PublicationExternalCleanupStatus(
        domain=PublicationExternalCleanupDomain(str(row["domain"])),
        state=PublicationExternalCleanupState(str(row["state"])),
        reason_code=str(row["reason_code"]),
        provider_receipt_present=bool(row["provider_receipt_present"]),
    )


__all__ = [
    "InMemoryPublicationExternalCleanupRepository",
    "PostgresPublicationExternalCleanupRepository",
    "PublicationExternalCleanupCoordinator",
    "PublicationExternalCleanupDomain",
    "PublicationExternalCleanupEffect",
    "PublicationExternalCleanupError",
    "PublicationExternalCleanupMaterializationTarget",
    "PublicationExternalCleanupState",
    "PublicationExternalCleanupStatus",
    "PUBLICATION_EXTERNAL_CLEANUP_EVENT_TYPE",
    "PUBLICATION_EXTERNAL_CLEANUP_JOB_TYPE",
    "PUBLICATION_EXTERNAL_CLEANUP_MAX_ATTEMPTS",
    "PUBLICATION_EXTERNAL_CLEANUP_OPERATION_TYPE",
    "PUBLICATION_EXTERNAL_CLEANUP_SCHEMA_VERSION",
    "build_publication_external_cleanup_effects",
]

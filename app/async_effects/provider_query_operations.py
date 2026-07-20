"""Read-only Provider-query operations evidence.

Provider effects can reach ``unknown`` after a timeout or an ambiguous
upstream response.  Before an operations user can query a real Provider, the
system must know which capability owns the backlog and whether a query is even
contractually possible.  This module deliberately stops there: it does not
hold a credential, reconstruct an upstream request, invoke a Provider, replay
an effect, or mutate a reconciliation projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable, Optional

from app.async_effects.provider_effects import (
    ProviderEffectCatalogEntry,
    provider_effect_catalog,
)


PROVIDER_QUERY_OPERATIONS_SCHEMA_VERSION = "provider-query-operations-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProviderQueryOperationsError(ValueError):
    """A Provider-query operations observation is incomplete or unsafe."""


class ProviderQueryOperationsObservationState(str, Enum):
    OBSERVED = "observed"
    CLEAR = "clear"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
    EXPIRED = "expired"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ProviderQueryOperationsError(f"{field} must be an opaque identifier")
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderQueryOperationsError(f"{field} must be a non-negative integer")
    return value


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ProviderQueryOperationsError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProviderQueryOperationsError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _canonical_hash(payload: object) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProviderQueryOperationsError("operations evidence must be serializable") from exc
    return sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderQueryBacklogEntry:
    """Aggregate unknown-effect counts for one Provider capability.

    The entry intentionally has no effect, request, owner, resource, vault,
    receipt, or upstream Provider identifier.  It exists only to decide
    whether operations must perform manual review before a future query path
    can be enabled.
    """

    provider: str
    capability: str
    unknown_effect_count: int
    pending_reconciliation_count: int
    manual_review_count: int
    reconciliation_conflict_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _identifier(self.provider, field="provider"))
        object.__setattr__(self, "capability", _identifier(self.capability, field="capability"))
        for field in (
            "unknown_effect_count",
            "pending_reconciliation_count",
            "manual_review_count",
            "reconciliation_conflict_count",
        ):
            object.__setattr__(self, field, _non_negative_int(getattr(self, field), field=field))
        if (
            self.pending_reconciliation_count
            + self.manual_review_count
            + self.reconciliation_conflict_count
            != self.unknown_effect_count
        ):
            raise ProviderQueryOperationsError(
                "backlog state counts must equal unknown_effect_count"
            )

    @property
    def provider_capability_key(self) -> str:
        return f"{self.provider}:{self.capability}"

    def value_free_summary(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "capability": self.capability,
            "unknownEffectCount": self.unknown_effect_count,
            "pendingReconciliationCount": self.pending_reconciliation_count,
            "manualReviewCount": self.manual_review_count,
            "reconciliationConflictCount": self.reconciliation_conflict_count,
        }


@dataclass(frozen=True)
class ProviderQueryOperationsEvidence:
    """Ephemeral, value-free query readiness report.

    ``provider_query_execution_enabled`` and related execution flags are
    deliberately false in this slice.  The report makes an external G3 gap
    visible without allowing a worker to bridge it by itself.
    """

    observation_id: str
    observation_state: ProviderQueryOperationsObservationState
    reason: str
    observed_at: datetime
    expires_at: datetime
    external_provider_query_gate_open: bool
    catalog_entry_count: int
    query_reconcile_support_counts: tuple[tuple[str, int], ...]
    backlog_entries: tuple[ProviderQueryBacklogEntry, ...]
    uncatalogued_unknown_effect_count: int
    artifact_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _identifier(self.observation_id, field="observation_id"))
        if not isinstance(self.observation_state, ProviderQueryOperationsObservationState):
            raise ProviderQueryOperationsError("observation_state is invalid")
        object.__setattr__(self, "reason", _identifier(self.reason, field="reason"))
        observed = _utc(self.observed_at, field="observed_at")
        expires = _utc(self.expires_at, field="expires_at")
        if expires <= observed:
            raise ProviderQueryOperationsError("expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(
            self,
            "catalog_entry_count",
            _non_negative_int(self.catalog_entry_count, field="catalog_entry_count"),
        )
        support_counts: list[tuple[str, int]] = []
        for support, count in self.query_reconcile_support_counts:
            support_counts.append(
                (
                    _identifier(support, field="query reconcile support"),
                    _non_negative_int(count, field="query reconcile support count"),
                )
            )
        if tuple(sorted(support_counts)) != tuple(support_counts):
            raise ProviderQueryOperationsError("query_reconcile_support_counts must be sorted")
        if sum(count for _, count in support_counts) != self.catalog_entry_count:
            raise ProviderQueryOperationsError(
                "query_reconcile_support_counts must equal catalog_entry_count"
            )
        object.__setattr__(self, "query_reconcile_support_counts", tuple(support_counts))
        entries = tuple(self.backlog_entries)
        if any(not isinstance(entry, ProviderQueryBacklogEntry) for entry in entries):
            raise ProviderQueryOperationsError("backlog_entries must contain ProviderQueryBacklogEntry")
        sorted_entries = tuple(sorted(entries, key=lambda item: item.provider_capability_key))
        if sorted_entries != entries:
            raise ProviderQueryOperationsError("backlog_entries must be sorted")
        if len({entry.provider_capability_key for entry in entries}) != len(entries):
            raise ProviderQueryOperationsError("backlog_entries must be unique")
        object.__setattr__(self, "backlog_entries", entries)
        object.__setattr__(
            self,
            "uncatalogued_unknown_effect_count",
            _non_negative_int(
                self.uncatalogued_unknown_effect_count,
                field="uncatalogued_unknown_effect_count",
            ),
        )
        expected_artifact_hash = _canonical_hash(self._artifact_payload())
        supplied_artifact_hash = str(self.artifact_hash or "").strip().lower()
        if supplied_artifact_hash and not _SHA256_PATTERN.fullmatch(supplied_artifact_hash):
            raise ProviderQueryOperationsError("artifact_hash must be a lowercase SHA-256 digest")
        if supplied_artifact_hash and supplied_artifact_hash != expected_artifact_hash:
            raise ProviderQueryOperationsError("artifact_hash does not match immutable evidence")
        object.__setattr__(self, "artifact_hash", expected_artifact_hash)

    @property
    def unknown_effect_count(self) -> int:
        return sum(entry.unknown_effect_count for entry in self.backlog_entries)

    @property
    def pending_reconciliation_count(self) -> int:
        return sum(entry.pending_reconciliation_count for entry in self.backlog_entries)

    @property
    def manual_review_count(self) -> int:
        return sum(entry.manual_review_count for entry in self.backlog_entries)

    @property
    def reconciliation_conflict_count(self) -> int:
        return sum(entry.reconciliation_conflict_count for entry in self.backlog_entries)

    def effective_state(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> ProviderQueryOperationsObservationState:
        instant = _utc(now, field="now") if now is not None else datetime.now(timezone.utc)
        if instant >= self.expires_at:
            return ProviderQueryOperationsObservationState.EXPIRED
        return self.observation_state

    def requires_manual_review(self, *, now: Optional[datetime] = None) -> bool:
        state = self.effective_state(now=now)
        return state is not ProviderQueryOperationsObservationState.CLEAR or self.unknown_effect_count > 0

    def _artifact_payload(self) -> dict[str, object]:
        return {
            "backlog": [entry.value_free_summary() for entry in self.backlog_entries],
            "catalogEntryCount": self.catalog_entry_count,
            "externalProviderQueryGateOpen": self.external_provider_query_gate_open,
            "expiresAt": self.expires_at.isoformat(),
            "observationId": self.observation_id,
            "observationState": self.observation_state.value,
            "observedAt": self.observed_at.isoformat(),
            "queryReconcileSupportCounts": dict(self.query_reconcile_support_counts),
            "reason": self.reason,
            "schemaVersion": PROVIDER_QUERY_OPERATIONS_SCHEMA_VERSION,
            "uncataloguedUnknownEffectCount": self.uncatalogued_unknown_effect_count,
        }

    def value_free_summary(self, *, now: Optional[datetime] = None) -> dict[str, object]:
        state = self.effective_state(now=now)
        reason = (
            "providerQueryOperationsEvidenceExpired"
            if state is ProviderQueryOperationsObservationState.EXPIRED
            else self.reason
        )
        return {
            "schemaVersion": PROVIDER_QUERY_OPERATIONS_SCHEMA_VERSION,
            "observationId": self.observation_id,
            "observationState": state.value,
            "reason": reason,
            "observedAt": self.observed_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "providerQueryExecutionEnabled": False,
            "automaticReconciliationEnabled": False,
            "replayEnabled": False,
            "externalProviderQueryGateOpen": self.external_provider_query_gate_open,
            "catalogEntryCount": self.catalog_entry_count,
            "queryReconcileSupportCounts": dict(self.query_reconcile_support_counts),
            "unknownEffectCount": self.unknown_effect_count,
            "pendingReconciliationCount": self.pending_reconciliation_count,
            "manualReviewCount": self.manual_review_count,
            "reconciliationConflictCount": self.reconciliation_conflict_count,
            "uncataloguedUnknownEffectCount": self.uncatalogued_unknown_effect_count,
            "backlog": [entry.value_free_summary() for entry in self.backlog_entries],
            "artifactHash": self.artifact_hash,
            "requiresManualReview": self.requires_manual_review(now=now),
        }


def build_provider_query_operations_evidence(
    *,
    backlog_entries: Iterable[ProviderQueryBacklogEntry],
    observed_at: datetime,
    expires_at: datetime,
    catalog_entries: Iterable[ProviderEffectCatalogEntry] | None = None,
    store_supported: bool = True,
    collection_error_code: Optional[str] = None,
) -> ProviderQueryOperationsEvidence:
    """Build an immutable query-operations snapshot without querying a Provider."""

    observed = _utc(observed_at, field="observed_at")
    expires = _utc(expires_at, field="expires_at")
    catalog = tuple(provider_effect_catalog() if catalog_entries is None else catalog_entries)
    if any(not isinstance(entry, ProviderEffectCatalogEntry) for entry in catalog):
        raise ProviderQueryOperationsError("catalog_entries must contain ProviderEffectCatalogEntry")
    catalog_pairs = [(entry.provider, entry.capability) for entry in catalog]
    if len(set(catalog_pairs)) != len(catalog_pairs):
        raise ProviderQueryOperationsError("catalog provider/capability pairs must be unique")
    support_counts: dict[str, int] = {}
    for entry in catalog:
        support_counts[entry.query_reconcile_support] = (
            support_counts.get(entry.query_reconcile_support, 0) + 1
        )
    entries = tuple(sorted(tuple(backlog_entries), key=lambda item: item.provider_capability_key))
    if any(not isinstance(entry, ProviderQueryBacklogEntry) for entry in entries):
        raise ProviderQueryOperationsError("backlog_entries must contain ProviderQueryBacklogEntry")
    if len({entry.provider_capability_key for entry in entries}) != len(entries):
        raise ProviderQueryOperationsError("backlog_entries must be unique")
    catalog_pair_set = set(catalog_pairs)
    uncatalogued_count = sum(
        entry.unknown_effect_count
        for entry in entries
        if (entry.provider, entry.capability) not in catalog_pair_set
    )
    normalized_error = (
        _identifier(collection_error_code, field="collection_error_code")
        if collection_error_code is not None
        else None
    )
    unknown_count = sum(entry.unknown_effect_count for entry in entries)
    if normalized_error is not None:
        state = ProviderQueryOperationsObservationState.UNKNOWN
        reason = normalized_error
    elif not store_supported:
        state = ProviderQueryOperationsObservationState.SKIPPED
        reason = "providerQueryOperationsStoreUnsupported"
    elif unknown_count:
        state = ProviderQueryOperationsObservationState.OBSERVED
        reason = "providerQueryBacklogObserved"
    else:
        state = ProviderQueryOperationsObservationState.CLEAR
        reason = "providerQueryBacklogClear"
    external_gate_open = any(
        entry.query_reconcile_support == "providerQuery" for entry in catalog
    )
    observation_id = "providerQueryOps-" + _canonical_hash(
        {
            "backlog": [entry.value_free_summary() for entry in entries],
            "catalog": [entry.key for entry in catalog],
            "expiresAt": expires.isoformat(),
            "observedAt": observed.isoformat(),
            "reason": reason,
            "state": state.value,
        }
    )[:32]
    return ProviderQueryOperationsEvidence(
        observation_id=observation_id,
        observation_state=state,
        reason=reason,
        observed_at=observed,
        expires_at=expires,
        external_provider_query_gate_open=external_gate_open,
        catalog_entry_count=len(catalog),
        query_reconcile_support_counts=tuple(sorted(support_counts.items())),
        backlog_entries=entries,
        uncatalogued_unknown_effect_count=uncatalogued_count,
    )

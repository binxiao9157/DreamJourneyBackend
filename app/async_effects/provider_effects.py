"""Value-free provider-effect contracts for the async-effect migration.

The current backend has several synchronous provider adapters.  This module
does not route traffic or enable a worker.  It only defines the immutable
identity and receipt vocabulary that every future provider adapter must use
before it can be moved behind the async-effect kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectIntent


PROVIDER_EFFECT_SCHEMA_VERSION = "provider-effect-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_REQUEST_NAMESPACE = UUID("e1463b2a-6ab2-4b5d-bc39-4eebf756b3f8")


class ProviderEffectContractError(ValueError):
    """The provider-effect envelope or evidence is incomplete."""


class ProviderEffectConflict(ProviderEffectContractError):
    """A stable request identity was reused with different meaning."""


class ProviderEffectState(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ProviderEffectQueryOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    STILL_UNKNOWN = "stillUnknown"
    UNSUPPORTED = "unsupported"


def _require_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ProviderEffectContractError(f"{field} must be an opaque identifier")
    return normalized


def _require_hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ProviderEffectContractError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _canonical_hash(payload: object) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProviderEffectContractError("provider effect hash material must be serializable") from exc
    return sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderEffectIntent:
    """One provider request bound to an already-accepted async operation.

    ``request_hash`` represents the canonical provider input outside this
    module.  Raw prompts, media, credentials and provider request bodies never
    enter this contract, the catalog, or receipt summaries.
    """

    effect_intent: AsyncEffectIntent
    provider: str
    capability: str
    request_hash: str
    contract_version: str = PROVIDER_EFFECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.effect_intent, AsyncEffectIntent):
            raise ProviderEffectContractError("effect_intent must be an AsyncEffectIntent")
        object.__setattr__(self, "provider", _require_identifier(self.provider, field="provider"))
        object.__setattr__(self, "capability", _require_identifier(self.capability, field="capability"))
        object.__setattr__(self, "request_hash", _require_hash(self.request_hash, field="requestHash"))
        object.__setattr__(
            self,
            "contract_version",
            _require_identifier(self.contract_version, field="contractVersion"),
        )

    @property
    def provider_effect_key(self) -> str:
        return _canonical_hash(
            {
                "capability": self.capability,
                "contractVersion": self.contract_version,
                "operationStableKey": self.effect_intent.stable_key,
                "provider": self.provider,
                "purpose": self.effect_intent.target.purpose,
            }
        )

    @property
    def provider_request_id(self) -> str:
        return str(
            uuid5(
                _PROVIDER_REQUEST_NAMESPACE,
                f"provider-request:{self.provider_effect_key}:{self.request_hash}",
            )
        )

    @property
    def provider_request_id_hash(self) -> str:
        return _canonical_hash(
            {
                "provider": self.provider,
                "providerRequestId": self.provider_request_id,
                "schemaVersion": self.contract_version,
            }
        )

    @property
    def immutable_fingerprint(self) -> str:
        return _canonical_hash(
            {
                "providerEffectKey": self.provider_effect_key,
                "providerRequestId": self.provider_request_id,
                "requestHash": self.request_hash,
                "schemaVersion": self.contract_version,
            }
        )

    def value_free_summary(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "contractVersion": self.contract_version,
            "operationId": self.effect_intent.operation_id,
            "operationStableKey": self.effect_intent.stable_key,
            "provider": self.provider,
            "providerEffectKey": self.provider_effect_key,
            "providerRequestIdHash": self.provider_request_id_hash,
            "requestHash": self.request_hash,
            "schemaVersion": PROVIDER_EFFECT_SCHEMA_VERSION,
        }


def assert_same_provider_request(
    existing: ProviderEffectIntent,
    candidate: ProviderEffectIntent,
) -> None:
    """Reject a different request body under an existing stable effect key.

    The effect key deliberately excludes the request hash so a provider effect
    has one durable identity across an accepted operation.  Once that identity
    has been observed, a changed request hash is a semantic conflict rather
    than a retry.  Callers must create a new operation instead of reissuing it.
    """

    if not isinstance(existing, ProviderEffectIntent) or not isinstance(candidate, ProviderEffectIntent):
        raise ProviderEffectContractError("existing and candidate must be ProviderEffectIntent values")
    if existing.provider_effect_key != candidate.provider_effect_key:
        raise ProviderEffectContractError("provider effect keys must match before request binding is compared")
    if existing.request_hash != candidate.request_hash:
        raise ProviderEffectConflict(
            "a stable provider effect key cannot be rebound to a different requestHash"
        )


@dataclass(frozen=True)
class ProviderEffectReceipt:
    """Append-only, value-free observation of a provider effect.

    ``provider_receipt_hash`` is optional because a timeout may leave no
    upstream receipt.  ``observation_hash`` is always available and may later
    be persisted as the local receipt identity without pretending that an
    upstream provider receipt exists.
    """

    intent: ProviderEffectIntent
    state: ProviderEffectState
    reason_code: str
    attempt: int = 1
    provider_receipt_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ProviderEffectIntent):
            raise ProviderEffectContractError("intent must be a ProviderEffectIntent")
        if not isinstance(self.state, ProviderEffectState):
            raise ProviderEffectContractError("state must be a ProviderEffectState")
        object.__setattr__(self, "reason_code", _require_identifier(self.reason_code, field="reasonCode"))
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ProviderEffectContractError("attempt must be a positive integer")
        if self.provider_receipt_hash is not None:
            object.__setattr__(
                self,
                "provider_receipt_hash",
                _require_hash(self.provider_receipt_hash, field="providerReceiptHash"),
            )

    @property
    def observation_hash(self) -> str:
        return _canonical_hash(
            {
                "attempt": self.attempt,
                "providerEffectKey": self.intent.provider_effect_key,
                "providerReceiptHash": self.provider_receipt_hash,
                "reasonCode": self.reason_code,
                "schemaVersion": self.intent.contract_version,
                "state": self.state.value,
            }
        )

    def value_free_summary(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "observationHash": self.observation_hash,
            "providerEffectKey": self.intent.provider_effect_key,
            "providerReceiptPresent": self.provider_receipt_hash is not None,
            "reasonCode": self.reason_code,
            "schemaVersion": self.intent.contract_version,
            "state": self.state.value,
        }


@dataclass(frozen=True)
class ProviderEffectReconciliation:
    """Result of querying an effect after an uncertain provider outcome."""

    prior_unknown: ProviderEffectReceipt
    outcome: ProviderEffectQueryOutcome
    query_receipt_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.prior_unknown, ProviderEffectReceipt):
            raise ProviderEffectContractError("prior_unknown must be a ProviderEffectReceipt")
        if self.prior_unknown.state is not ProviderEffectState.UNKNOWN:
            raise ProviderEffectContractError("only an unknown provider effect may be reconciled")
        if not isinstance(self.outcome, ProviderEffectQueryOutcome):
            raise ProviderEffectContractError("outcome must be a ProviderEffectQueryOutcome")
        object.__setattr__(self, "query_receipt_hash", _require_hash(self.query_receipt_hash, field="queryReceiptHash"))

    @property
    def result_state(self) -> ProviderEffectState:
        if self.outcome is ProviderEffectQueryOutcome.COMPLETED:
            return ProviderEffectState.COMPLETED
        if self.outcome is ProviderEffectQueryOutcome.FAILED:
            return ProviderEffectState.FAILED
        return ProviderEffectState.UNKNOWN

    @property
    def reason_code(self) -> str:
        return {
            ProviderEffectQueryOutcome.COMPLETED: "providerQueryCompleted",
            ProviderEffectQueryOutcome.FAILED: "providerQueryFailed",
            ProviderEffectQueryOutcome.STILL_UNKNOWN: "providerQueryStillUnknown",
            ProviderEffectQueryOutcome.UNSUPPORTED: "providerQueryUnsupported",
        }[self.outcome]

    @property
    def requires_manual_review(self) -> bool:
        return self.outcome in {
            ProviderEffectQueryOutcome.STILL_UNKNOWN,
            ProviderEffectQueryOutcome.UNSUPPORTED,
        }

    @property
    def reissue_allowed(self) -> bool:
        """Unknown outcomes must query/reconcile; they never silently resend."""

        return False

    def terminal_receipt(self) -> ProviderEffectReceipt:
        return ProviderEffectReceipt(
            intent=self.prior_unknown.intent,
            state=self.result_state,
            reason_code=self.reason_code,
            attempt=self.prior_unknown.attempt,
            provider_receipt_hash=self.query_receipt_hash,
        )


@dataclass(frozen=True)
class ProviderEffectCatalogEntry:
    """Migration classification for one current or explicitly absent provider path."""

    key: str
    provider: str
    capability: str
    source_paths: tuple[str, ...]
    current_execution: str
    request_id_strategy: str
    query_reconcile_support: str
    migration_disposition: str
    default_exposure: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_identifier(self.key, field="key"))
        object.__setattr__(self, "provider", _require_identifier(self.provider, field="provider"))
        object.__setattr__(self, "capability", _require_identifier(self.capability, field="capability"))
        if not self.source_paths or any(":" not in path for path in self.source_paths):
            raise ProviderEffectContractError("source_paths must contain source references")
        for field in (
            "current_execution",
            "request_id_strategy",
            "query_reconcile_support",
            "migration_disposition",
            "default_exposure",
        ):
            object.__setattr__(self, field, _require_identifier(getattr(self, field), field=field))

    @property
    def requires_stable_provider_effect(self) -> bool:
        return self.migration_disposition in {
            "stableRequestBeforeEnable",
            "providerSelectionBeforeEnable",
            "brokerBeforeEnable",
        }

    def value_free_summary(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "currentExecution": self.current_execution,
            "defaultExposure": self.default_exposure,
            "key": self.key,
            "migrationDisposition": self.migration_disposition,
            "provider": self.provider,
            "queryReconcileSupport": self.query_reconcile_support,
            "requestIdStrategy": self.request_id_strategy,
            "sourcePaths": list(self.source_paths),
        }


PROVIDER_EFFECT_CATALOG: tuple[ProviderEffectCatalogEntry, ...] = (
    ProviderEffectCatalogEntry(
        key="amap.districtLookup",
        provider="amap",
        capability="districtLookup",
        source_paths=("app/main.py:/maps/district", "app/services/amap.py:AMapDistrictProxy.request_district"),
        current_execution="syncReadOnly",
        request_id_strategy="none",
        query_reconcile_support="notApplicable",
        migration_disposition="keepReadOnlySync",
        default_exposure="existingPublicShell",
    ),
    ProviderEffectCatalogEntry(
        key="apns.delivery",
        provider="apns",
        capability="notificationDelivery",
        source_paths=("app/main.py:inAppMessageProjection", "app/services:APNsAdapterAbsent"),
        current_execution="notImplemented",
        request_id_strategy="none",
        query_reconcile_support="providerReceiptRequired",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="defaultOff",
    ),
    ProviderEffectCatalogEntry(
        key="deepseek.archiveImageAnalysis",
        provider="deepseekTextOnly",
        capability="archiveImageAnalysis",
        source_paths=("app/main.py:/archive/image-analysis", "app/services/deepseek.py:DeepSeekTextOnlyImageAnalysisAdapter"),
        current_execution="providerUnsupported",
        request_id_strategy="none",
        query_reconcile_support="unsupported",
        migration_disposition="providerSelectionBeforeEnable",
        default_exposure="defaultOff",
    ),
    ProviderEffectCatalogEntry(
        key="deepseek.kbExtract",
        provider="deepseek",
        capability="kbExtract",
        source_paths=("app/main.py:/kb/extract", "app/services/deepseek.py:DeepSeekKnowledgeExtractionProxy.request_extraction"),
        current_execution="syncDirect",
        request_id_strategy="none",
        query_reconcile_support="unsupported",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="existingPublicShell",
    ),
    ProviderEffectCatalogEntry(
        key="echo.modelGeneration",
        provider="model",
        capability="echoReplyGeneration",
        source_paths=("app/main.py:/context/build", "app/services:serverModelAdapterAbsent"),
        current_execution="notImplemented",
        request_id_strategy="none",
        query_reconcile_support="providerSelectionRequired",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="defaultOff",
    ),
    ProviderEffectCatalogEntry(
        key="objectStorage.mediaUpload",
        provider="objectStorage",
        capability="mediaUpload",
        source_paths=("app/main.py:/archive/media/upload-intent", "app/services/runtime_config.py:mockObjectStorage"),
        current_execution="mockOnly",
        request_id_strategy="mockOnly",
        query_reconcile_support="notApplicable",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="defaultOff",
    ),
    ProviderEffectCatalogEntry(
        key="tencent.digitalHumanSession",
        provider="tencent",
        capability="digitalHumanSession",
        source_paths=("app/main.py:/digital-human/sessions", "app/services/digital_human_access.py:DigitalHumanAccessPolicy"),
        current_execution="credentialBrokerBlocked",
        request_id_strategy="none",
        query_reconcile_support="providerContractRequired",
        migration_disposition="brokerBeforeEnable",
        default_exposure="defaultOff",
    ),
    ProviderEffectCatalogEntry(
        key="volcengine.legacyTts",
        provider="volcengine",
        capability="legacyTts",
        source_paths=("app/main.py:/tts", "app/services/tts.py:VolcTTSProxy.request_tts"),
        current_execution="syncDirect",
        request_id_strategy="providerReqid",
        query_reconcile_support="unsupported",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="existingPublicShell",
    ),
    ProviderEffectCatalogEntry(
        key="volcengineVoiceClone.synthesis",
        provider="volcengineVoiceClone",
        capability="voiceCloneSynthesis",
        source_paths=("app/main.py:/voice/synthesis", "app/services/tts.py:VolcVoiceCloneTTSProxy.synthesize"),
        current_execution="syncDirect",
        request_id_strategy="providerRequestId",
        query_reconcile_support="logReferenceOnly",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="defaultOff",
    ),
    ProviderEffectCatalogEntry(
        key="volcengineVoiceClone.training",
        provider="volcengineVoiceClone",
        capability="voiceCloneTraining",
        source_paths=("app/services/voice_clone.py:VolcEngineVoiceCloneV3Provider.submit_training", "app/services/voice_clone.py:VolcEngineVoiceCloneV3Provider.query_training"),
        current_execution="adapterOnly",
        request_id_strategy="providerRequestId",
        query_reconcile_support="providerQuery",
        migration_disposition="stableRequestBeforeEnable",
        default_exposure="defaultOff",
    ),
)


def provider_effect_catalog() -> tuple[ProviderEffectCatalogEntry, ...]:
    """Return the deterministic catalog without inspecting runtime secrets."""

    return PROVIDER_EFFECT_CATALOG


def provider_effect_catalog_summary(
    entries: Iterable[ProviderEffectCatalogEntry] = PROVIDER_EFFECT_CATALOG,
) -> dict[str, object]:
    normalized = tuple(entries)
    keys = [entry.key for entry in normalized]
    if len(keys) != len(set(keys)):
        raise ProviderEffectContractError("provider effect catalog keys must be unique")
    if keys != sorted(keys):
        raise ProviderEffectContractError("provider effect catalog must be sorted by key")
    execution_counts: dict[str, int] = {}
    stable_request_required_count = 0
    for entry in normalized:
        execution_counts[entry.current_execution] = execution_counts.get(entry.current_execution, 0) + 1
        stable_request_required_count += int(entry.requires_stable_provider_effect)
    return {
        "catalogVersion": PROVIDER_EFFECT_SCHEMA_VERSION,
        "entryCount": len(normalized),
        "executionCounts": dict(sorted(execution_counts.items())),
        "stableRequestRequiredCount": stable_request_required_count,
        "providerCallsEnabledByCatalog": False,
        "entries": [entry.value_free_summary() for entry in normalized],
    }

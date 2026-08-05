"""Value-minimized external-effect view for a data-rights request.

Data-rights execution records, Provider-effect receipts and access revocation
events have different lifecycles.  This module deliberately does not merge
their writers or invent a fourth state machine.  It makes their relationship
readable through one strict, read-only projection:

* access revocation is reported separately from physical/provider completion;
* a missing upstream receipt can never become ``completed``;
* unconfigured external domains remain ``unsupported`` or ``pending``;
* observations whose request/owner hashes do not match are discarded.

The output contains no provider identifier, object key, media URL, raw subject
identifier, or provider log identifier.  It is suitable for the internal
data-rights evidence route and for a future redacted export manifest.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence


DATA_RIGHTS_EXTERNAL_EFFECT_PROJECTION_SCHEMA_VERSION = 1

_DOMAIN_DEFINITIONS = (
    {
        "domain": "objectStorage",
        "defaultStatus": "unsupported",
        "defaultReasonCode": "objectStorageDeletionAdapterNotConfigured",
    },
    {
        "domain": "providerVoice",
        "defaultStatus": "pending",
        "defaultReasonCode": "providerVoiceDeletionReceiptPending",
    },
    {
        "domain": "providerDigitalHuman",
        "defaultStatus": "pending",
        "defaultReasonCode": "providerDigitalHumanDeletionReceiptPending",
    },
    {
        "domain": "notificationDelivery",
        "defaultStatus": "unsupported",
        "defaultReasonCode": "notificationProviderReceiptNotConfigured",
    },
    {
        "domain": "backupRetention",
        "defaultStatus": "pending",
        "defaultReasonCode": "backupRetentionExternalBoundary",
    },
)
DATA_RIGHTS_EXTERNAL_EFFECT_DOMAINS = frozenset(
    item["domain"] for item in _DOMAIN_DEFINITIONS
)
_SAFE_REASON_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)


class DataRightsExternalEffectProjectionError(ValueError):
    """Raised when a redacted external-effect projection cannot be built."""


class DataRightsExternalEffectObservation(dict):
    """A value-minimized receipt observation with a non-serializable owner bind.

    Repositories create this object only after enforcing the request-owner
    fence.  The owner hash remains available to this in-process projection so
    it can reject foreign evidence, while the public mapping deliberately
    contains no owner hash, effect identity, evidence hash, or Provider value.
    ``json.dumps`` treats this as its dictionary payload and therefore cannot
    accidentally serialize the private owner binding.
    """

    def __init__(
        self,
        *,
        request_id: str,
        owner_subject_hash: str,
        domain: str,
        effect_identity_hash: str,
        state: str,
        provider_receipt_present: bool,
        reason_codes: Sequence[str],
        observed_at: str,
    ) -> None:
        super().__init__(
            {
                "requestId": request_id,
                "domain": domain,
                "state": state,
                "providerReceiptPresent": provider_receipt_present,
                "reasonCodes": list(reason_codes),
            }
        )
        self._owner_subject_hash = owner_subject_hash
        self._effect_identity_hash = effect_identity_hash
        self._observed_at = observed_at

    @property
    def owner_subject_hash(self) -> str:
        """Return the owner binding for in-process projection validation only."""

        return self._owner_subject_hash

    @property
    def effect_identity_hash(self) -> str:
        """Return the private effect key used to collapse state history."""

        return self._effect_identity_hash

    @property
    def observed_at(self) -> str:
        """Return the private observation ordering value for this process only."""

        return self._observed_at


def build_data_rights_external_effect_projection(
    summary: Mapping[str, Any],
    *,
    resource_evidence: Sequence[Mapping[str, Any]],
    access_revocation: Mapping[str, Any],
    linked_effect_observations: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """Project external cleanup state for one already-persisted rights request.

    ``resource_evidence`` is the value-minimized output already produced by
    :mod:`data_rights_evidence_projection`.  ``linked_effect_observations`` is
    intentionally optional until each Provider path writes a durable relation
    to the request.  Such observations must carry only request/owner hashes and
    opaque state; foreign observations are rejected rather than co-mingled.
    """

    request = _mapping(summary.get("request"), "rights summary request")
    request_id = _text(request.get("id"), "request id")
    # Older redacted rights summaries predate the owner-hash projection.  They
    # remain readable, but cannot safely bind a newly linked external receipt.
    # Treating that as an empty match would allow foreign evidence to enter the
    # projection, so the linked receipt is rejected below instead.
    subject_hash = str(request.get("subjectHash") or "").strip()
    access_state = (
        "revoked"
        if str(access_revocation.get("status") or "") == "revoked"
        else "notConfirmed"
    )

    observations_by_domain: Dict[str, List[Dict[str, Any]]] = {
        domain: [] for domain in DATA_RIGHTS_EXTERNAL_EFFECT_DOMAINS
    }
    for resource in resource_evidence:
        if not isinstance(resource, Mapping):
            continue
        domain = _domain_for_resource(resource)
        if domain is not None:
            observations_by_domain[domain].append(_resource_observation(resource))

    rejected_evidence_count = 0
    for sequence, observation in enumerate(linked_effect_observations):
        if not isinstance(observation, Mapping):
            rejected_evidence_count += 1
            continue
        domain = str(observation.get("domain") or "").strip()
        if domain not in DATA_RIGHTS_EXTERNAL_EFFECT_DOMAINS:
            rejected_evidence_count += 1
            continue
        if (
            not subject_hash
            or str(observation.get("requestId") or "") != request_id
            or _linked_observation_owner_hash(observation) != subject_hash
        ):
            rejected_evidence_count += 1
            continue
        observations_by_domain[domain].append(
            _linked_effect_observation(observation, sequence=sequence)
        )

    domains = []
    for definition in _DOMAIN_DEFINITIONS:
        domain = str(definition["domain"])
        domains.append(
            _domain_projection(
                definition,
                observations_by_domain[domain],
                access_state=access_state,
            )
        )

    statuses = [str(item["status"]) for item in domains]
    return {
        "schemaVersion": DATA_RIGHTS_EXTERNAL_EFFECT_PROJECTION_SCHEMA_VERSION,
        "accessState": access_state,
        "accessFirstRequired": True,
        "status": _aggregate_statuses(statuses),
        "uncompletedDomainCount": sum(1 for status in statuses if status != "completed"),
        "rejectedEvidenceCount": rejected_evidence_count,
        "domains": domains,
    }


def _domain_for_resource(resource: Mapping[str, Any]) -> str | None:
    module = str(resource.get("moduleId") or "").strip().lower()
    resource_type = str(resource.get("resourceType") or "").strip().lower()
    if module == "objectstorage" or "objectstorage" in module:
        return "objectStorage"
    if module == "providervoice" or (module.startswith("provider") and "voice" in module):
        return "providerVoice"
    if module == "providerdigitalhuman" or (
        module.startswith("provider") and ("digitalhuman" in module or "digital_human" in module)
    ):
        return "providerDigitalHuman"
    if module == "backupretention" or "backup" in resource_type:
        return "backupRetention"
    if module in {"notificationdelivery", "providernotification"} or (
        module.startswith("provider") and "notification" in module
    ):
        return "notificationDelivery"
    return None


def _resource_observation(resource: Mapping[str, Any]) -> Dict[str, Any]:
    status = _normalized_status(resource.get("status"))
    return {
        "status": status,
        "receiptPresent": bool(resource.get("receiptPresent")),
        "ageSeconds": _nonnegative_int_or_none(resource.get("ageSeconds")),
        "reasonCodes": _safe_reason_codes(resource.get("reasonCodes")),
    }


def _linked_effect_observation(
    observation: Mapping[str, Any],
    *,
    sequence: int,
) -> Dict[str, Any]:
    state = str(observation.get("state") or "").strip().lower()
    provider_receipt_present = bool(observation.get("providerReceiptPresent"))
    if state == "completed" and provider_receipt_present:
        status = "completed"
    elif state == "completed":
        status = "pending"
    elif state in {"failed", "partial"}:
        status = "partial"
    elif state == "unsupported":
        status = "unsupported"
    else:
        # pending, accepted and unknown all require an external follow-up.
        status = "pending"
    reason_codes = _safe_reason_codes(observation.get("reasonCodes"))
    if state == "completed" and not provider_receipt_present:
        reason_codes.append("providerCompletionMissingUpstreamReceipt")
    elif state == "unknown":
        reason_codes.append("providerEffectUnknown")
    elif state == "failed":
        reason_codes.append("providerEffectFailed")
    return {
        "status": status,
        "receiptPresent": provider_receipt_present,
        "ageSeconds": _nonnegative_int_or_none(observation.get("ageSeconds")),
        "reasonCodes": _deduplicate(reason_codes),
        "_source": "linkedEffectReceipt",
        "_effectKey": _linked_observation_effect_key(observation, sequence=sequence),
        "_observedAt": _linked_observation_observed_at(observation),
        "_sequence": sequence,
    }


def _linked_observation_owner_hash(observation: Mapping[str, Any]) -> str:
    """Read a trusted private owner bind or a test-only raw mapping value.

    The raw mapping fallback preserves validation of malformed/foreign inputs
    from callers that do not originate in a repository.  Repository results
    use :class:`DataRightsExternalEffectObservation`, whose owner binding is
    intentionally excluded from the dictionary payload.
    """

    if isinstance(observation, DataRightsExternalEffectObservation):
        return observation.owner_subject_hash
    return str(observation.get("ownerSubjectHash") or "")


def _linked_observation_effect_key(
    observation: Mapping[str, Any],
    *,
    sequence: int,
) -> str:
    """Return an in-process key for grouping one effect's state history."""

    if isinstance(observation, DataRightsExternalEffectObservation):
        return observation.effect_identity_hash
    raw_key = str(observation.get("effectIdentityHash") or "").strip()
    return raw_key or f"unbound-linked-effect-{sequence}"


def _linked_observation_observed_at(observation: Mapping[str, Any]) -> str:
    """Return a non-public ordering value for linked state observations."""

    if isinstance(observation, DataRightsExternalEffectObservation):
        return observation.observed_at
    return str(observation.get("observedAt") or "")


def _domain_projection(
    definition: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    *,
    access_state: str,
) -> Dict[str, Any]:
    if not observations:
        status = str(definition["defaultStatus"])
        reason_codes = [str(definition["defaultReasonCode"])]
        receipt_state = "notObserved"
        latest_age_seconds = None
    else:
        statuses = [str(item["status"]) for item in _current_effect_observations(observations)]
        status = _aggregate_statuses(statuses)
        receipt_count = sum(1 for item in observations if item["receiptPresent"])
        if receipt_count == len(observations):
            receipt_state = "recorded"
        elif receipt_count:
            receipt_state = "partial"
        else:
            receipt_state = "missing"
        reason_codes = _deduplicate(
            code
            for item in observations
            for code in item["reasonCodes"]
        )
        latest_age_seconds = _latest_age_seconds(observations)

    if access_state != "revoked":
        reason_codes = _deduplicate([*reason_codes, "accessRevocationNotConfirmed"])
    return {
        "domain": str(definition["domain"]),
        "accessState": access_state,
        "status": status,
        "receiptState": receipt_state,
        "observedEffectCount": len(observations),
        "latestEvidenceAgeSeconds": latest_age_seconds,
        "requiresFollowUp": status in {"pending", "partial"},
        "reasonCodes": reason_codes,
    }


def _current_effect_observations(
    observations: Sequence[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """Collapse append-only receipt history to its current state per effect.

    Receipt history stays append-only for auditability.  A previous accepted
    observation must not keep a later provider-confirmed completion in the
    pending state.  Resource evidence has no external-effect identity and is
    therefore retained as individual current facts; linked receipts are
    grouped by their private effect key and only the latest state per key is
    used for the domain's current status.
    """

    current: List[Mapping[str, Any]] = []
    latest_linked: Dict[str, tuple[str, int, Mapping[str, Any]]] = {}
    for index, observation in enumerate(observations):
        if str(observation.get("_source") or "") != "linkedEffectReceipt":
            current.append(observation)
            continue
        effect_key = str(observation.get("_effectKey") or f"unbound-{index}")
        ordering = (
            str(observation.get("_observedAt") or ""),
            int(observation.get("_sequence") or index),
        )
        existing = latest_linked.get(effect_key)
        if existing is None or ordering >= (existing[0], existing[1]):
            latest_linked[effect_key] = (ordering[0], ordering[1], observation)
    current.extend(item[2] for item in latest_linked.values())
    return current


def _aggregate_statuses(statuses: Sequence[str]) -> str:
    if not statuses:
        return "pending"
    values = set(statuses)
    if "pending" in values:
        return "pending"
    if values == {"completed"}:
        return "completed"
    if values == {"unsupported"}:
        return "unsupported"
    return "partial"


def _normalized_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "completed":
        return "completed"
    if normalized == "unsupported":
        return "unsupported"
    if normalized in {"partial", "failed"}:
        return "partial"
    return "pending"


def _latest_age_seconds(observations: Sequence[Mapping[str, Any]]) -> int | None:
    ages = [item["ageSeconds"] for item in observations if item["ageSeconds"] is not None]
    return min(ages) if ages else None


def _safe_reason_codes(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return _deduplicate(
        str(item).strip()
        for item in value
        if isinstance(item, str)
        and item.strip()
        and set(item.strip()) <= _SAFE_REASON_CHARACTERS
        and len(item.strip()) <= 128
    )


def _deduplicate(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


def _nonnegative_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataRightsExternalEffectProjectionError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DataRightsExternalEffectProjectionError(f"{field} is required")
    return normalized


__all__ = [
    "DATA_RIGHTS_EXTERNAL_EFFECT_PROJECTION_SCHEMA_VERSION",
    "DATA_RIGHTS_EXTERNAL_EFFECT_DOMAINS",
    "DataRightsExternalEffectObservation",
    "DataRightsExternalEffectProjectionError",
    "build_data_rights_external_effect_projection",
]

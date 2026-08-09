"""Dry-run first closed-pilot admission and rollback planning.

This module never mutates environment variables or production traffic. It
validates a proposed staged rollout and emits a value-minimized audit plan for
operators to apply through the existing deployment mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping

from app.services.release_policy import ReleasePolicyService


CLOSED_PILOT_ADMISSION_SCHEMA_VERSION = 1
CLOSED_PILOT_FEATURE_ORDER = (
    "ownerTextCaptureV1",
    "ownerTruthCandidateReview",
    "ownerMediaCaptureV1",
    "ownerMediaProcessingV1",
    "ownerTruthFamilyContribution",
    "accountDataExport",
)


class ClosedPilotAdmissionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ClosedPilotReadiness:
    ready: bool
    reason: str


def build_closed_pilot_admission_plan(
    *,
    owner_ids: Iterable[str],
    requested_features: Iterable[str],
    readiness: Mapping[str, ClosedPilotReadiness],
    current_features: Iterable[str] = (),
    kill_switch_features: Iterable[str] = (),
    synthetic_only: bool = True,
) -> dict[str, Any]:
    owners = tuple(sorted({_required(item, "owner") for item in owner_ids}))
    requested = tuple(_ordered_feature_set(requested_features))
    current = tuple(_ordered_feature_set(current_features))
    kills = frozenset(_required(item, "killSwitchFeature") for item in kill_switch_features)
    if not owners:
        raise ClosedPilotAdmissionError("closedPilotAllowlistEmpty")
    if synthetic_only and any(not item.startswith("synthetic-") for item in owners):
        raise ClosedPilotAdmissionError("closedPilotSyntheticAccountRequired")
    unknown = set(requested).difference(ReleasePolicyService._FEATURE_GATES)
    if unknown:
        raise ClosedPilotAdmissionError("closedPilotUnknownFeature")
    if kills.intersection(requested):
        raise ClosedPilotAdmissionError("closedPilotKillSwitchActive")
    if not set(current).issubset(requested):
        raise ClosedPilotAdmissionError("closedPilotRollbackMustUseExplicitPlan")

    added = [item for item in requested if item not in current]
    if len(added) > 1:
        raise ClosedPilotAdmissionError("closedPilotSingleStepRequired")
    if added:
        next_feature = added[0]
        next_index = CLOSED_PILOT_FEATURE_ORDER.index(next_feature)
        required_predecessors = set(CLOSED_PILOT_FEATURE_ORDER[:next_index])
        if not required_predecessors.issubset(current):
            raise ClosedPilotAdmissionError("closedPilotFeatureOrderViolation")
    else:
        next_feature = None

    checks = []
    for feature in requested:
        state = readiness.get(feature)
        ready = bool(state and state.ready)
        reason = state.reason if state is not None else "readinessMissing"
        checks.append({"feature": feature, "ready": ready, "reason": reason})
    blocked = [item for item in checks if not item["ready"]]
    status = "ready" if not blocked else "blocked"
    rollback_features = list(current)
    return {
        "schemaVersion": CLOSED_PILOT_ADMISSION_SCHEMA_VERSION,
        "status": status,
        "syntheticOnly": synthetic_only,
        "ownerCount": len(owners),
        "ownerDigests": [_digest(item) for item in owners],
        "currentFeatures": list(current),
        "requestedFeatures": list(requested),
        "nextFeature": next_feature,
        "readiness": checks,
        "blockedReasons": sorted({item["reason"] for item in blocked}),
        "killSwitchFeatures": sorted(kills),
        "rollback": {
            "requestedFeatures": rollback_features,
            "emergencyDisable": [next_feature] if next_feature else [],
            "requiresNewPolicyRevision": bool(next_feature),
        },
        "applyAuthorized": status == "ready",
        "auditDigest": _digest(
            ":".join(
                (
                    ",".join(owners),
                    ",".join(requested),
                    status,
                )
            )
        ),
    }


def _ordered_feature_set(values: Iterable[str]) -> list[str]:
    normalized = {_required(value, "feature") for value in values}
    unsupported = normalized.difference(CLOSED_PILOT_FEATURE_ORDER)
    if unsupported:
        raise ClosedPilotAdmissionError("closedPilotFeatureNotStaged")
    return [item for item in CLOSED_PILOT_FEATURE_ORDER if item in normalized]


def _required(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 128:
        raise ClosedPilotAdmissionError(f"closedPilot{field}Invalid")
    return normalized


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "CLOSED_PILOT_ADMISSION_SCHEMA_VERSION",
    "CLOSED_PILOT_FEATURE_ORDER",
    "ClosedPilotAdmissionError",
    "ClosedPilotReadiness",
    "build_closed_pilot_admission_plan",
]

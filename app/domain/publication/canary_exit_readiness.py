"""Default-blocked Publication M2 canary, incident and exit-readiness contract.

This G0 module captures only the decision boundary for a future adult
Publication canary. It accepts opaque identifiers, hashes, enum stages and
boolean evidence observations. It never enrolls a person, opens a public
gateway, creates a Visitor session, dispatches an incident, removes data,
contacts a provider or asserts external/product approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID

from .schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)


PUBLICATION_CANARY_EXIT_READINESS_G0_SCHEMA_VERSION = "publication-canary-exit-readiness-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PublicationCanaryExitReadinessError(ValueError):
    """Raised when a future Publication canary decision is malformed."""


class PublicationCanaryStage(str, Enum):
    SYNTHETIC = "synthetic"
    INTERNAL = "internal"
    ADULT_COHORT = "adultCohort"


class PublicationCanaryDecision(str, Enum):
    NO_GO = "noGo"
    PAUSE = "pause"


class PublicationCanaryExitDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    OWNER_SCOPE_DENIED = "owner_scope_denied"
    PAUSE_REQUIRED = "pause_required"
    LEGACY_PATH_RETIREMENT_REQUIRED = "legacy_path_retirement_required"
    INTERNAL_EVIDENCE_REQUIRED = "internal_evidence_required"
    EXTERNAL_GATES_REQUIRED = "external_gates_required"
    POLICY_DISABLED = "policy_disabled"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationCanaryExitReadinessError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationCanaryExitReadinessError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationCanaryExitReadinessError(f"{field} must be a SHA-256 digest")
    return normalized


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationCanaryExitReadinessError(f"{field} must be a non-negative integer")
    return value


def _strict_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise PublicationCanaryExitReadinessError(f"{field} must be a boolean")
    return value


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationCanaryEvidence:
    """Value-minimized evidence observations for one candidate decision."""

    synthetic_negative_corpus_passed: bool
    internal_release_guard_passed: bool
    withdrawal_receipt_candidate_present: bool
    rights_exit_candidate_present: bool
    incident_response_candidate_present: bool
    private_leak_observed: bool = False
    revoke_gap_observed: bool = False
    unknown_required_effect_observed: bool = False
    open_incident_observed: bool = False
    legacy_guest_path_hit_count: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "synthetic_negative_corpus_passed",
            "internal_release_guard_passed",
            "withdrawal_receipt_candidate_present",
            "rights_exit_candidate_present",
            "incident_response_candidate_present",
            "private_leak_observed",
            "revoke_gap_observed",
            "unknown_required_effect_observed",
            "open_incident_observed",
        ):
            object.__setattr__(
                self,
                field_name,
                _strict_bool(getattr(self, field_name), field=field_name),
            )
        object.__setattr__(
            self,
            "legacy_guest_path_hit_count",
            _nonnegative_int(
                self.legacy_guest_path_hit_count,
                field="legacy_guest_path_hit_count",
            ),
        )

    @property
    def pause_required(self) -> bool:
        return any(
            (
                self.private_leak_observed,
                self.revoke_gap_observed,
                self.unknown_required_effect_observed,
                self.open_incident_observed,
            )
        )

    @property
    def internal_evidence_complete(self) -> bool:
        return all(
            (
                self.synthetic_negative_corpus_passed,
                self.internal_release_guard_passed,
                self.withdrawal_receipt_candidate_present,
                self.rights_exit_candidate_present,
                self.incident_response_candidate_present,
            )
        )


@dataclass(frozen=True)
class PublicationCanaryExitRequest:
    """Hash-only decision envelope for a future synthetic/internal/adult canary."""

    decision_id: str
    publication_id: str
    publication_version_id: str
    vault_id: str
    owner_subject_hash: str
    authority_epoch: int
    stage: PublicationCanaryStage
    policy_hash: str
    build_hash: str
    schema_hash: str
    evidence_hashes: tuple[str, ...]
    evidence: PublicationCanaryEvidence
    external_g2_evidence_present: bool = False
    external_g3_evidence_present: bool = False
    external_g4_approval_present: bool = False

    def __post_init__(self) -> None:
        for field_name in ("decision_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_hash",
            _digest(self.owner_subject_hash, field="owner_subject_hash"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(self, "stage", PublicationCanaryStage(self.stage))
        for field_name in ("policy_hash", "build_hash", "schema_hash"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field=field_name))
        try:
            evidence_hashes = tuple(_digest(value, field="evidence_hash") for value in self.evidence_hashes)
        except TypeError as exc:
            raise PublicationCanaryExitReadinessError("evidence_hashes must be iterable") from exc
        if not evidence_hashes or len(evidence_hashes) != len(set(evidence_hashes)):
            raise PublicationCanaryExitReadinessError(
                "evidence_hashes must be non-empty and unique"
            )
        object.__setattr__(self, "evidence_hashes", tuple(sorted(evidence_hashes)))
        if not isinstance(self.evidence, PublicationCanaryEvidence):
            raise PublicationCanaryExitReadinessError("evidence must be PublicationCanaryEvidence")
        for field_name in (
            "external_g2_evidence_present",
            "external_g3_evidence_present",
            "external_g4_approval_present",
        ):
            object.__setattr__(
                self,
                field_name,
                _strict_bool(getattr(self, field_name), field=field_name),
            )

    @property
    def external_gates_present(self) -> bool:
        return all(
            (
                self.external_g2_evidence_present,
                self.external_g3_evidence_present,
                self.external_g4_approval_present,
            )
        )


@dataclass(frozen=True)
class PublicationCanaryExitResult:
    disposition: PublicationCanaryExitDisposition
    decision: PublicationCanaryDecision
    reason_codes: tuple[str, ...]
    stage: PublicationCanaryStage | None = None
    scope_hash: str | None = None
    required_gates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            PublicationCanaryExitDisposition(self.disposition),
        )
        object.__setattr__(self, "decision", PublicationCanaryDecision(self.decision))
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationCanaryExitReadinessError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.stage is not None:
            object.__setattr__(self, "stage", PublicationCanaryStage(self.stage))
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))
        allowed_gates = {"G0", "G1", "G2", "G3", "G4"}
        gates = tuple(sorted({_identifier(value, field="required_gate") for value in self.required_gates}))
        if not set(gates).issubset(allowed_gates):
            raise PublicationCanaryExitReadinessError("required_gates must be known gates")
        object.__setattr__(self, "required_gates", gates)

    @property
    def cohort_enrolled(self) -> bool:
        return False

    @property
    def public_access_enabled(self) -> bool:
        return False

    @property
    def incident_dispatched(self) -> bool:
        return False

    @property
    def rights_exit_executed(self) -> bool:
        return False

    @property
    def regulatory_exit_approved(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "cohortEnrolled": self.cohort_enrolled,
            "decision": self.decision.value,
            "incidentDispatched": self.incident_dispatched,
            "publicAccessEnabled": self.public_access_enabled,
            "reasonCodes": list(self.reason_codes),
            "regulatoryExitApproved": self.regulatory_exit_approved,
            "releaseVisible": False,
            "requiredGates": list(self.required_gates),
            "rightsExitExecuted": self.rights_exit_executed,
            "schemaVersion": PUBLICATION_CANARY_EXIT_READINESS_G0_SCHEMA_VERSION,
            "stage": self.stage.value if self.stage is not None else None,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        return summary


def _scope_hash(
    *,
    context: PublicationAuthorizationContext,
    principal: PublicationAuthorizationPrincipal,
    request: PublicationCanaryExitRequest,
) -> str:
    return _hash(
        {
            "authorityEpoch": request.authority_epoch,
            "buildHash": request.build_hash,
            "evidenceHashes": request.evidence_hashes,
            "ownerSubjectHash": request.owner_subject_hash,
            "policyHash": request.policy_hash,
            "principalKind": principal.kind.value,
            "principalSubjectHash": principal.subject_hash,
            "publicationId": request.publication_id,
            "publicationVersionId": request.publication_version_id,
            "schemaHash": request.schema_hash,
            "stage": request.stage.value,
            "vaultId": request.vault_id,
        }
    )


def _result(
    disposition: PublicationCanaryExitDisposition,
    decision: PublicationCanaryDecision,
    reason: str,
    *,
    request: PublicationCanaryExitRequest | None = None,
    scope_hash: str | None = None,
    required_gates: tuple[str, ...] = ("G0", "G1", "G2", "G3", "G4"),
) -> PublicationCanaryExitResult:
    return PublicationCanaryExitResult(
        disposition=disposition,
        decision=decision,
        reason_codes=(reason,),
        stage=request.stage if request is not None else None,
        scope_hash=scope_hash,
        required_gates=required_gates,
    )


def evaluate_publication_canary_exit_readiness(
    *,
    context: PublicationAuthorizationContext | object,
    principal: PublicationAuthorizationPrincipal | object,
    request: PublicationCanaryExitRequest | object,
    enabled: bool = False,
) -> PublicationCanaryExitResult:
    """Produce only a no-go/pause candidate; it cannot promote a real canary."""

    if enabled is not True:
        return _result(
            PublicationCanaryExitDisposition.SHADOW_DISABLED,
            PublicationCanaryDecision.NO_GO,
            "publicationCanaryExitReadinessShadowDisabled",
        )
    if not all(
        (
            isinstance(context, PublicationAuthorizationContext),
            isinstance(principal, PublicationAuthorizationPrincipal),
            isinstance(request, PublicationCanaryExitRequest),
        )
    ):
        return _result(
            PublicationCanaryExitDisposition.INVALID_CONTEXT,
            PublicationCanaryDecision.NO_GO,
            "invalidPublicationCanaryExitReadinessContext",
        )
    if (
        principal.kind is not PublicationPrincipalKind.OWNER
        or principal.vault_id != context.vault_id
        or principal.subject_hash != context.owner_subject_hash
        or request.vault_id != context.vault_id
        or request.owner_subject_hash != context.owner_subject_hash
        or request.authority_epoch != context.authority_epoch
    ):
        return _result(
            PublicationCanaryExitDisposition.OWNER_SCOPE_DENIED,
            PublicationCanaryDecision.NO_GO,
            "publicationCanaryOwnerScopeMismatch",
            request=request,
        )

    scope_hash = _scope_hash(context=context, principal=principal, request=request)
    if request.evidence.pause_required:
        return _result(
            PublicationCanaryExitDisposition.PAUSE_REQUIRED,
            PublicationCanaryDecision.PAUSE,
            "publicationCanaryStopTheLineConditionObserved",
            request=request,
            scope_hash=scope_hash,
        )
    if request.evidence.legacy_guest_path_hit_count > 0:
        return _result(
            PublicationCanaryExitDisposition.LEGACY_PATH_RETIREMENT_REQUIRED,
            PublicationCanaryDecision.NO_GO,
            "legacyGuestPathMustReachZeroBeforePublicationCanary",
            request=request,
            scope_hash=scope_hash,
        )
    if not request.evidence.internal_evidence_complete:
        return _result(
            PublicationCanaryExitDisposition.INTERNAL_EVIDENCE_REQUIRED,
            PublicationCanaryDecision.NO_GO,
            "publicationCanaryInternalEvidenceIncomplete",
            request=request,
            scope_hash=scope_hash,
        )
    if request.stage is PublicationCanaryStage.ADULT_COHORT and not request.external_gates_present:
        return _result(
            PublicationCanaryExitDisposition.EXTERNAL_GATES_REQUIRED,
            PublicationCanaryDecision.NO_GO,
            "adultPublicationCanaryRequiresG2G3G4EvidenceAndApproval",
            request=request,
            scope_hash=scope_hash,
        )
    return _result(
        PublicationCanaryExitDisposition.POLICY_DISABLED,
        PublicationCanaryDecision.NO_GO,
        "publicationCanaryRemainsManualAndDisabledAtG0",
        request=request,
        scope_hash=scope_hash,
    )


__all__ = [
    "PUBLICATION_CANARY_EXIT_READINESS_G0_SCHEMA_VERSION",
    "PublicationCanaryDecision",
    "PublicationCanaryEvidence",
    "PublicationCanaryExitDisposition",
    "PublicationCanaryExitReadinessError",
    "PublicationCanaryExitRequest",
    "PublicationCanaryExitResult",
    "PublicationCanaryStage",
    "evaluate_publication_canary_exit_readiness",
]

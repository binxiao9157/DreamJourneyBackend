"""Default-off G0 boundary for a future Memorial FamilyContributionGrant.

Family relationship is not authority. A future scoped contribution grant may
allow only material contribution and the contributor's own withdrawal. This
module has no route or persistence and cannot create a grant, Source, Candidate
or any broader Memorial authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID


OWNER_TRUTH_MEMORIAL_FAMILY_CONTRIBUTION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-memorial-family-contribution-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class MemorialFamilyContributionContractError(ValueError):
    """Raised when a server-resolved Family Contributor envelope is invalid."""


class MemorialFamilyContributionOperation(str, Enum):
    SUBMIT_SOURCE = "submit_source"
    SUBMIT_CANDIDATE = "submit_candidate"
    WITHDRAW_OWN_CONTRIBUTION = "withdraw_own_contribution"
    CONFIRM_MEMORY = "confirm_memory"
    PRIVATE_QUERY = "private_query"
    GRANT_QUERY_ACCESS = "grant_query_access"
    PUBLICATION = "publication"
    VOICE_TRAINING = "voice_training"
    VOICE_SYNTHESIS_PRIVATE = "voice_synthesis_private"
    PORTRAIT_RENDERING = "portrait_rendering"
    DIGITAL_HUMAN_PRIVATE = "digital_human_private"
    CONTROLLER_APPOINTMENT = "controller_appointment"


class MemorialFamilyContributionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN = "represented_login_principal_forbidden"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    CONTRIBUTION_GRANT_REQUIRED = "contribution_grant_required"
    EXTERNAL_G2_G4_REQUIRED = "external_g2_g4_required"


_PERMITTED_OPERATIONS = frozenset(
    {
        MemorialFamilyContributionOperation.SUBMIT_SOURCE,
        MemorialFamilyContributionOperation.SUBMIT_CANDIDATE,
        MemorialFamilyContributionOperation.WITHDRAW_OWN_CONTRIBUTION,
    }
)


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MemorialFamilyContributionContractError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise MemorialFamilyContributionContractError(f"{field} must be a UUID") from exc


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemorialFamilyContributionContractError(f"{field} must be a non-negative integer")
    return value


def _digest(value: object) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MemorialFamilyContributionContractError(
            "family contribution material must be JSON serializable"
        ) from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemorialFamilyContributionContext:
    """Read-only, server-resolved context for one contributor operation."""

    vault_id: str
    represented_persona_id: str
    contributor_subject_id: str
    operation: MemorialFamilyContributionOperation
    contribution_grant_version: int
    represented_login_subject_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "represented_persona_id",
            _uuid(self.represented_persona_id, field="represented_persona_id"),
        )
        object.__setattr__(
            self,
            "contributor_subject_id",
            _identifier(self.contributor_subject_id, field="contributor_subject_id"),
        )
        object.__setattr__(
            self,
            "contribution_grant_version",
            _non_negative(self.contribution_grant_version, field="contribution_grant_version"),
        )
        if self.represented_login_subject_id is not None:
            object.__setattr__(
                self,
                "represented_login_subject_id",
                _identifier(
                    self.represented_login_subject_id,
                    field="represented_login_subject_id",
                ),
            )
        try:
            operation = MemorialFamilyContributionOperation(self.operation)
        except ValueError as exc:
            raise MemorialFamilyContributionContractError("operation is unsupported") from exc
        object.__setattr__(self, "operation", operation)

    def scope_hash(self) -> str:
        return _digest(
            {
                "operation": self.operation.value,
                "personaId": self.represented_persona_id,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class MemorialFamilyContributionClaims:
    """Synthetic flags that do not replace a persisted ContributionGrant."""

    active_contribution_grant: bool = False
    contributor_identity_verified: bool = False
    operation_is_own_contribution: bool = False

    def __post_init__(self) -> None:
        for field in (
            "active_contribution_grant",
            "contributor_identity_verified",
            "operation_is_own_contribution",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MemorialFamilyContributionContractError(f"{field} must be a boolean")

    def missing_reason_codes(
        self,
        operation: MemorialFamilyContributionOperation,
    ) -> tuple[str, ...]:
        reasons = []
        if not self.active_contribution_grant:
            reasons.append("activeScopedFamilyContributionGrantRequired")
        if not self.contributor_identity_verified:
            reasons.append("verifiedContributorIdentityRequired")
        if (
            operation is MemorialFamilyContributionOperation.WITHDRAW_OWN_CONTRIBUTION
            and not self.operation_is_own_contribution
        ):
            reasons.append("contributorMayWithdrawOnlyOwnContribution")
        return tuple(reasons)

    def prerequisites_asserted(self, operation: MemorialFamilyContributionOperation) -> bool:
        return not self.missing_reason_codes(operation)


@dataclass(frozen=True)
class MemorialFamilyContributionShadow:
    """Value-free non-admission result for future scoped contribution writes."""

    enabled: bool
    disposition: MemorialFamilyContributionDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    captured_grant_version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MemorialFamilyContributionContractError("enabled must be a boolean")
        if not isinstance(self.disposition, MemorialFamilyContributionDisposition):
            raise MemorialFamilyContributionContractError("disposition is required")
        reasons = tuple(sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes}))
        if not reasons:
            raise MemorialFamilyContributionContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", self.scope_hash):
            raise MemorialFamilyContributionContractError("scope_hash must be a SHA-256 digest")
        if self.captured_grant_version is not None:
            object.__setattr__(
                self,
                "captured_grant_version",
                _non_negative(self.captured_grant_version, field="captured_grant_version"),
            )

    @property
    def contribution_admitted(self) -> bool:
        return False

    @property
    def contribution_grant_written(self) -> bool:
        return False

    @property
    def source_or_candidate_written(self) -> bool:
        return False

    @property
    def confirmed_memory_written(self) -> bool:
        return False

    @property
    def private_query_allowed(self) -> bool:
        return False

    @property
    def publication_or_high_risk_capability_allowed(self) -> bool:
        return False

    @property
    def controller_authority_granted(self) -> bool:
        return False

    @property
    def records_written(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "confirmedMemoryWritten": self.confirmed_memory_written,
            "contributionAdmitted": self.contribution_admitted,
            "contributionGrantWritten": self.contribution_grant_written,
            "controllerAuthorityGranted": self.controller_authority_granted,
            "enabled": self.enabled,
            "privateQueryAllowed": self.private_query_allowed,
            "publicationOrHighRiskCapabilityAllowed": (
                self.publication_or_high_risk_capability_allowed
            ),
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "representedPersonaLoginPrincipal": False,
            "requiredExternalGates": ["G2", "G4"],
            "schemaVersion": OWNER_TRUTH_MEMORIAL_FAMILY_CONTRIBUTION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "sourceOrCandidateWritten": self.source_or_candidate_written,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.captured_grant_version is not None:
            summary["capturedGrantVersion"] = self.captured_grant_version
        return summary


def observe_memorial_family_contribution(
    context: MemorialFamilyContributionContext | object,
    *,
    claims: MemorialFamilyContributionClaims | object | None = None,
    enabled: bool = False,
) -> MemorialFamilyContributionShadow:
    """Fail closed until a real scoped ContributionGrant and write model exist."""

    if not enabled:
        return MemorialFamilyContributionShadow(
            enabled=False,
            disposition=MemorialFamilyContributionDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, MemorialFamilyContributionContext):
        return MemorialFamilyContributionShadow(
            enabled=True,
            disposition=MemorialFamilyContributionDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialFamilyContributionContext",),
        )
    if context.represented_login_subject_id is not None:
        return MemorialFamilyContributionShadow(
            enabled=True,
            disposition=(
                MemorialFamilyContributionDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN
            ),
            reason_codes=("representedPersonaCannotBeLoginPrincipal",),
        )
    if context.operation not in _PERMITTED_OPERATIONS:
        return MemorialFamilyContributionShadow(
            enabled=True,
            disposition=MemorialFamilyContributionDisposition.OPERATION_NOT_ALLOWED,
            reason_codes=("familyContributorMayOnlySubmitSourceCandidateOrWithdrawOwnContribution",),
            scope_hash=context.scope_hash(),
            captured_grant_version=context.contribution_grant_version,
        )
    if claims is None:
        claims = MemorialFamilyContributionClaims()
    if not isinstance(claims, MemorialFamilyContributionClaims):
        return MemorialFamilyContributionShadow(
            enabled=True,
            disposition=MemorialFamilyContributionDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialFamilyContributionClaims",),
        )
    if not claims.prerequisites_asserted(context.operation):
        return MemorialFamilyContributionShadow(
            enabled=True,
            disposition=MemorialFamilyContributionDisposition.CONTRIBUTION_GRANT_REQUIRED,
            reason_codes=claims.missing_reason_codes(context.operation),
            scope_hash=context.scope_hash(),
            captured_grant_version=context.contribution_grant_version,
        )
    return MemorialFamilyContributionShadow(
        enabled=True,
        disposition=MemorialFamilyContributionDisposition.EXTERNAL_G2_G4_REQUIRED,
        reason_codes=(
            "futureContributionWriterMustUseScopedGrantVersionCas",
            "familyRelationshipCannotAuthorizePrivateQueryOrHighRiskCapability",
            "syntheticClaimsCannotAuthorizeMemorialContribution",
        ),
        scope_hash=context.scope_hash(),
        captured_grant_version=context.contribution_grant_version,
    )


__all__ = [
    "OWNER_TRUTH_MEMORIAL_FAMILY_CONTRIBUTION_SHADOW_SCHEMA_VERSION",
    "MemorialFamilyContributionClaims",
    "MemorialFamilyContributionContext",
    "MemorialFamilyContributionContractError",
    "MemorialFamilyContributionDisposition",
    "MemorialFamilyContributionOperation",
    "MemorialFamilyContributionShadow",
    "observe_memorial_family_contribution",
]

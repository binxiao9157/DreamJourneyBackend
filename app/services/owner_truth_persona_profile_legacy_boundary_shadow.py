"""Default-off profile inventory for a future Persona Authority migration.

Legacy ``profiles`` are a migration input, not a Persona authority.  This G0
classifier keeps the V4 B15 allowlist deliberately small and quarantines
identity, relationship, voice, provider, digital-human and unknown fields.
It never writes a Persona, changes a profile, calls a provider or exposes a
route.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Mapping


OWNER_TRUTH_PERSONA_PROFILE_LEGACY_BOUNDARY_SCHEMA_VERSION = (
    "owner-truth-persona-profile-legacy-boundary-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PERSONA_ALLOWLIST = {
    "birthday": "birthDate",
    "birthDate": "birthDate",
    "displayName": "displayName",
    "gender": "gender",
    "nickname": "displayName",
}
_IDENTITY_ONLY_FIELDS = frozenset(
    {
        "accountId",
        "email",
        "id",
        "ownerId",
        "ownerSubjectId",
        "ownerUserId",
        "phone",
        "subjectId",
        "userId",
    }
)
_EMBODIMENT_OR_PROVIDER_FIELDS = frozenset(
    {
        "avatarName",
        "avatarURL",
        "digitalHumanId",
        "digitalHumanMode",
        "familyPersonaContractVersion",
        "providerAssetId",
        "providerLogId",
        "providerMode",
        "providerRequestId",
        "providerSpeakerId",
        "sessionId",
        "voiceEnabled",
        "voiceProfileId",
        "voiceSampleStatus",
    }
)
_FAMILY_OR_RELATIONSHIP_FIELDS = frozenset(
    {
        "accessGrants",
        "accessStatus",
        "familyMemberId",
        "grantEpoch",
        "invitationCode",
        "invitationError",
        "invitationStatus",
        "invitationURL",
        "memberSubjectId",
        "personaScope",
        "relation",
        "relationshipAuthoritySource",
        "relationshipEpoch",
        "relationshipId",
        "relationshipOwnerUserId",
        "relationshipStatus",
    }
)


class OwnerTruthPersonaProfileLegacyBoundaryContractError(ValueError):
    """Raised when a caller supplies an invalid synthetic profile context."""


class OwnerTruthPersonaProfileLegacyBoundaryDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    OWNER_MISMATCH = "owner_mismatch"
    CLASSIFIED = "classified"


class OwnerTruthPersonaProfileLegacyFieldClass(str, Enum):
    ALLOWLISTED_PERSONA_CANDIDATE = "allowlisted_persona_candidate"
    IDENTITY_BINDING_ONLY = "identity_binding_only"
    EMBODIMENT_OR_PROVIDER_EXCLUDED = "embodiment_or_provider_excluded"
    FAMILY_OR_RELATIONSHIP_EXCLUDED = "family_or_relationship_excluded"
    UNKNOWN_QUARANTINE = "unknown_quarantine"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthPersonaProfileLegacyBoundaryContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _scope_hash(*, vault_id: str, owner_subject_id: str) -> str:
    return sha256(
        json.dumps(
            {"ownerSubjectId": owner_subject_id, "vaultId": vault_id},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _classify_field(key: str) -> OwnerTruthPersonaProfileLegacyFieldClass:
    if key in _PERSONA_ALLOWLIST:
        return OwnerTruthPersonaProfileLegacyFieldClass.ALLOWLISTED_PERSONA_CANDIDATE
    if key in _IDENTITY_ONLY_FIELDS:
        return OwnerTruthPersonaProfileLegacyFieldClass.IDENTITY_BINDING_ONLY
    if key in _EMBODIMENT_OR_PROVIDER_FIELDS:
        return OwnerTruthPersonaProfileLegacyFieldClass.EMBODIMENT_OR_PROVIDER_EXCLUDED
    if key in _FAMILY_OR_RELATIONSHIP_FIELDS:
        return OwnerTruthPersonaProfileLegacyFieldClass.FAMILY_OR_RELATIONSHIP_EXCLUDED
    return OwnerTruthPersonaProfileLegacyFieldClass.UNKNOWN_QUARANTINE


def _profile_fingerprint(
    profile: Mapping[str, object],
    *,
    field_classes: Mapping[str, OwnerTruthPersonaProfileLegacyFieldClass],
) -> str:
    """Hash field names/classes only; profile values must not enter evidence."""

    material = {
        "fieldClasses": {
            key: field_classes[key].value
            for key in sorted(field_classes)
        },
        "fieldCount": len(profile),
    }
    return sha256(
        json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class OwnerTruthPersonaProfileLegacyBoundaryContext:
    vault_id: str
    owner_subject_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )


@dataclass(frozen=True)
class OwnerTruthPersonaProfileLegacyBoundaryShadow:
    enabled: bool
    disposition: OwnerTruthPersonaProfileLegacyBoundaryDisposition
    field_class_counts: Mapping[str, int]
    canonical_candidate_fields: tuple[str, ...]
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    profile_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthPersonaProfileLegacyBoundaryContractError(
                "shadow enabled must be a boolean"
            )
        if not isinstance(self.disposition, OwnerTruthPersonaProfileLegacyBoundaryDisposition):
            raise OwnerTruthPersonaProfileLegacyBoundaryContractError("shadow disposition is required")
        normalized_counts: dict[str, int] = {}
        for key, value in self.field_class_counts.items():
            normalized_key = OwnerTruthPersonaProfileLegacyFieldClass(key).value
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise OwnerTruthPersonaProfileLegacyBoundaryContractError(
                    "field class count must be a non-negative integer"
                )
            normalized_counts[normalized_key] = value
        object.__setattr__(self, "field_class_counts", dict(sorted(normalized_counts.items())))
        normalized_candidates = tuple(sorted(set(self.canonical_candidate_fields)))
        if any(field not in set(_PERSONA_ALLOWLIST.values()) for field in normalized_candidates):
            raise OwnerTruthPersonaProfileLegacyBoundaryContractError(
                "candidate field is outside the Persona allowlist"
            )
        object.__setattr__(self, "canonical_candidate_fields", normalized_candidates)
        normalized_reasons = tuple(sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes}))
        if not normalized_reasons:
            raise OwnerTruthPersonaProfileLegacyBoundaryContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        for field in ("scope_hash", "profile_fingerprint"):
            value = getattr(self, field)
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise OwnerTruthPersonaProfileLegacyBoundaryContractError(
                    f"{field} must be a SHA-256 digest"
                )

    @property
    def legacy_profile_migrated(self) -> bool:
        return False

    @property
    def persona_created(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidatePersonaFields": list(self.canonical_candidate_fields),
            "enabled": self.enabled,
            "fieldClassCounts": dict(self.field_class_counts),
            "legacyProfileMigrated": self.legacy_profile_migrated,
            "personaCreated": self.persona_created,
            "reasonCodes": list(self.reason_codes),
            "schemaVersion": OWNER_TRUTH_PERSONA_PROFILE_LEGACY_BOUNDARY_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.profile_fingerprint is not None:
            summary["profileFingerprint"] = self.profile_fingerprint
        return summary


def inventory_legacy_profile_persona_boundary(
    profile: Mapping[str, object] | object,
    *,
    context: OwnerTruthPersonaProfileLegacyBoundaryContext | object,
    enabled: bool = False,
) -> OwnerTruthPersonaProfileLegacyBoundaryShadow:
    """Classify a legacy profile without creating or updating Persona authority."""

    if not enabled:
        return OwnerTruthPersonaProfileLegacyBoundaryShadow(
            enabled=False,
            disposition=OwnerTruthPersonaProfileLegacyBoundaryDisposition.SHADOW_DISABLED,
            field_class_counts={},
            canonical_candidate_fields=(),
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(profile, Mapping) or not isinstance(
        context, OwnerTruthPersonaProfileLegacyBoundaryContext
    ):
        return OwnerTruthPersonaProfileLegacyBoundaryShadow(
            enabled=True,
            disposition=OwnerTruthPersonaProfileLegacyBoundaryDisposition.INVALID_ENVELOPE,
            field_class_counts={},
            canonical_candidate_fields=(),
            reason_codes=("invalidLegacyProfileEnvelope",),
        )

    profile_owner = str(profile.get("userId") or "").strip()
    if not profile_owner:
        return OwnerTruthPersonaProfileLegacyBoundaryShadow(
            enabled=True,
            disposition=OwnerTruthPersonaProfileLegacyBoundaryDisposition.INVALID_ENVELOPE,
            field_class_counts={},
            canonical_candidate_fields=(),
            reason_codes=("legacyProfileOwnerMissing",),
        )
    scope_hash = _scope_hash(
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
    )
    if profile_owner != context.owner_subject_id:
        return OwnerTruthPersonaProfileLegacyBoundaryShadow(
            enabled=True,
            disposition=OwnerTruthPersonaProfileLegacyBoundaryDisposition.OWNER_MISMATCH,
            field_class_counts={},
            canonical_candidate_fields=(),
            reason_codes=("legacyProfileOwnerMismatch",),
            scope_hash=scope_hash,
        )

    field_classes: dict[str, OwnerTruthPersonaProfileLegacyFieldClass] = {}
    canonical_candidates: set[str] = set()
    for raw_key in profile:
        key = str(raw_key)
        field_class = _classify_field(key)
        field_classes[key] = field_class
        if field_class is OwnerTruthPersonaProfileLegacyFieldClass.ALLOWLISTED_PERSONA_CANDIDATE:
            canonical_candidates.add(_PERSONA_ALLOWLIST[key])

    counts = {field_class.value: 0 for field_class in OwnerTruthPersonaProfileLegacyFieldClass}
    for field_class in field_classes.values():
        counts[field_class.value] += 1
    return OwnerTruthPersonaProfileLegacyBoundaryShadow(
        enabled=True,
        disposition=OwnerTruthPersonaProfileLegacyBoundaryDisposition.CLASSIFIED,
        field_class_counts=counts,
        canonical_candidate_fields=tuple(canonical_candidates),
        reason_codes=(
            "personaAllowlistCandidateOnly",
            "providerFamilyAndUnknownFieldsExcluded",
            "separatePersonaAuthorityCommandRequired",
        ),
        scope_hash=scope_hash,
        profile_fingerprint=_profile_fingerprint(profile, field_classes=field_classes),
    )


__all__ = [
    "OWNER_TRUTH_PERSONA_PROFILE_LEGACY_BOUNDARY_SCHEMA_VERSION",
    "OwnerTruthPersonaProfileLegacyBoundaryContext",
    "OwnerTruthPersonaProfileLegacyBoundaryContractError",
    "OwnerTruthPersonaProfileLegacyBoundaryDisposition",
    "OwnerTruthPersonaProfileLegacyBoundaryShadow",
    "inventory_legacy_profile_persona_boundary",
]

"""Default-off G0 resolver vocabulary for Echo role voice selection.

The legacy iOS role picker may currently observe local family metadata. This
module defines the server-authoritative vocabulary required before a real
``ResolveRoleVoice`` query is introduced. It deliberately stays an in-memory,
value-minimized observer: it does not trust a client profile ID, open a
provider session, synthesize audio, persist a receipt, or expose a
release-visible result.

Even when synthetic prerequisites for a self or independently published
living-adult profile are present, the result is only a future candidate. The
effective G0 fallback remains explicit, so an old role/profile cannot be
silently retained while a later resolver is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from threading import RLock

from app.services.voice_dh_authority import VoiceDHPurpose


VOICE_ROLE_SELECTION_SHADOW_SCHEMA_VERSION = "voice-role-selection-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_PURPOSES = frozenset(
    {
        VoiceDHPurpose.PRIVATE_SYNTHESIS,
        VoiceDHPurpose.DH_AUDIO_DRIVE,
    }
)


class VoiceRoleSelectionError(ValueError):
    """Raised when a value-minimized role selection envelope is malformed."""


class VoiceRoleSelectionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


class VoiceRoleKind(str, Enum):
    SELF = "self"
    LIVING_ADULT = "livingAdult"
    MINOR = "minor"
    MEMORIAL_OR_DECEASED = "memorialOrDeceased"
    FAMILY_RELATION_ONLY = "familyRelationOnly"


class VoiceProfileResolutionState(str, Enum):
    MISSING = "missing"
    READY = "ready"
    REVOKED = "revoked"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class VoiceRoleSelectionCandidateSource(str, Enum):
    DEFAULT_AI = "defaultAI"
    SELF_PROFILE = "selfProfile"
    PUBLISHED_LIVING_PROFILE = "publishedLivingProfile"
    NONE = "none"


class VoiceRoleSelectionFallbackSource(str, Enum):
    DEFAULT_AI = "defaultAI"
    NEUTRAL_DEFAULT_AI = "neutralDefaultAI"
    TEXT_ONLY = "textOnly"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise VoiceRoleSelectionError(f"{field} must be an opaque identifier")
    return normalized


def _optional_identifier(value: object, *, field: str) -> str | None:
    return None if value is None else _identifier(value, field=field)


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise VoiceRoleSelectionError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VoiceRoleSelectionError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise VoiceRoleSelectionError(f"{field} must be a positive integer")
    return value


def _canonical_json(value: dict[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise VoiceRoleSelectionError("selection material must be serializable") from error


@dataclass(frozen=True)
class VoiceRoleSelectionAuthorityContext:
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            _identifier(self.actor_subject_id, field="actor_subject_id"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )


@dataclass(frozen=True)
class VoiceRoleSelectionRequest:
    """Server-resolved role/profile facts for a future Echo voice query.

    The caller cannot send a provider speaker ID, raw consent text, audio,
    display name, or family metadata. Consent/grant/quality booleans stand in
    for future server-owned receipt lookups during G0 only.
    """

    request_id: str
    runtime_id: str
    runtime_generation: int
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int
    role_subject_id: str
    role_kind: VoiceRoleKind
    profile_id: str | None
    profile_version: int | None
    profile_subject_id: str | None
    profile_state: VoiceProfileResolutionState
    purpose: VoiceDHPurpose
    policy_version: str
    independent_consent_observed: bool
    published_purpose_grant_observed: bool
    quality_acceptance_observed: bool
    request_hash: str

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "runtime_id",
            "vault_id",
            "owner_subject_id",
            "actor_subject_id",
            "role_subject_id",
            "policy_version",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "runtime_generation",
            _nonnegative_int(self.runtime_generation, field="runtime_generation"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )
        if not isinstance(self.role_kind, VoiceRoleKind):
            raise VoiceRoleSelectionError("role_kind is required")
        if not isinstance(self.profile_state, VoiceProfileResolutionState):
            raise VoiceRoleSelectionError("profile_state is required")
        if not isinstance(self.purpose, VoiceDHPurpose) or self.purpose not in _ALLOWED_PURPOSES:
            raise VoiceRoleSelectionError("purpose is not allowed for role voice selection")
        object.__setattr__(self, "request_hash", _hash(self.request_hash, field="request_hash"))

        profile_id = _optional_identifier(self.profile_id, field="profile_id")
        profile_subject_id = _optional_identifier(self.profile_subject_id, field="profile_subject_id")
        if profile_id is not None and profile_id.startswith("S_"):
            raise VoiceRoleSelectionError("profile_id must not be a provider speaker ID")
        if profile_id is None:
            if self.profile_version is not None or profile_subject_id is not None:
                raise VoiceRoleSelectionError("profile fields require an internal profile_id")
            if self.profile_state is not VoiceProfileResolutionState.MISSING:
                raise VoiceRoleSelectionError("missing profile must use missing profile_state")
        else:
            if self.profile_version is None or profile_subject_id is None:
                raise VoiceRoleSelectionError("profile_id requires version and subject binding")
            object.__setattr__(
                self,
                "profile_version",
                _positive_int(self.profile_version, field="profile_version"),
            )
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "profile_subject_id", profile_subject_id)

        for field in (
            "independent_consent_observed",
            "published_purpose_grant_observed",
            "quality_acceptance_observed",
        ):
            if not isinstance(getattr(self, field), bool):
                raise VoiceRoleSelectionError(f"{field} must be a boolean")

    @property
    def selection_fingerprint(self) -> str:
        material = {
            "authorityEpoch": self.authority_epoch,
            "independentConsentObserved": self.independent_consent_observed,
            "ownerSubjectId": self.owner_subject_id,
            "policyVersion": self.policy_version,
            "profileId": self.profile_id,
            "profileState": self.profile_state.value,
            "profileSubjectId": self.profile_subject_id,
            "profileVersion": self.profile_version,
            "publishedPurposeGrantObserved": self.published_purpose_grant_observed,
            "purpose": self.purpose.value,
            "qualityAcceptanceObserved": self.quality_acceptance_observed,
            "requestHash": self.request_hash,
            "roleKind": self.role_kind.value,
            "roleSubjectId": self.role_subject_id,
            "runtimeGeneration": self.runtime_generation,
            "runtimeId": self.runtime_id,
            "vaultId": self.vault_id,
        }
        return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VoiceRoleSelectionResult:
    disposition: VoiceRoleSelectionDisposition
    candidate_source: VoiceRoleSelectionCandidateSource
    fallback_source: VoiceRoleSelectionFallbackSource
    profile_candidate_eligible: bool
    clear_previous_profile: bool
    runtime_generation_accepted: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, VoiceRoleSelectionDisposition):
            raise TypeError("role selection disposition is required")
        if not isinstance(self.candidate_source, VoiceRoleSelectionCandidateSource):
            raise TypeError("candidate_source is required")
        if not isinstance(self.fallback_source, VoiceRoleSelectionFallbackSource):
            raise TypeError("fallback_source is required")
        for field in (
            "profile_candidate_eligible",
            "clear_previous_profile",
            "runtime_generation_accepted",
        ):
            if not isinstance(getattr(self, field), bool):
                raise VoiceRoleSelectionError(f"{field} must be a boolean")
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise VoiceRoleSelectionError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "candidateSource": self.candidate_source.value,
            "clearPreviousProfile": self.clear_previous_profile,
            "fallbackSource": self.fallback_source.value,
            "profileCandidateEligible": self.profile_candidate_eligible,
            "providerEffectAllowed": False,
            "providerEffectPerformed": False,
            "releaseVisible": False,
            "roleVoiceReceiptPersisted": False,
            "runtimeGenerationAccepted": self.runtime_generation_accepted,
            "schemaVersion": VOICE_ROLE_SELECTION_SHADOW_SCHEMA_VERSION,
            "status": self.disposition.value,
            "reasonCodes": list(self.reason_codes),
        }


class VoiceRoleSelectionShadow:
    """In-memory, default-deny role resolution observer for WI-V0-01-07."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._request_hashes: dict[tuple[str, str], str] = {}
        self._runtime_generations: dict[tuple[str, str], tuple[int, str]] = {}

    def observe(
        self,
        *,
        context: VoiceRoleSelectionAuthorityContext | object,
        request: VoiceRoleSelectionRequest | object,
        enabled: object = False,
    ) -> VoiceRoleSelectionResult:
        if enabled is not True:
            return VoiceRoleSelectionResult(
                disposition=VoiceRoleSelectionDisposition.SHADOW_DISABLED,
                candidate_source=VoiceRoleSelectionCandidateSource.NONE,
                fallback_source=VoiceRoleSelectionFallbackSource.DEFAULT_AI,
                profile_candidate_eligible=False,
                clear_previous_profile=False,
                runtime_generation_accepted=False,
                reason_codes=("roleVoiceSelectionShadowDisabled",),
            )
        if not isinstance(context, VoiceRoleSelectionAuthorityContext) or not isinstance(
            request,
            VoiceRoleSelectionRequest,
        ):
            return VoiceRoleSelectionResult(
                disposition=VoiceRoleSelectionDisposition.INVALID_CONTEXT,
                candidate_source=VoiceRoleSelectionCandidateSource.NONE,
                fallback_source=VoiceRoleSelectionFallbackSource.TEXT_ONLY,
                profile_candidate_eligible=False,
                clear_previous_profile=True,
                runtime_generation_accepted=False,
                reason_codes=("invalidRoleVoiceSelectionContext",),
            )

        reasons: set[str] = {
            "g0NoServerRoleVoiceResolver",
            "g1ServerConsentGrantQualityLookupRequired",
            "g3ProviderProfileEvidenceRequired",
            "releasePolicyDefaultOff",
        }
        context_matches = (
            context.vault_id == request.vault_id
            and context.owner_subject_id == request.owner_subject_id
            and context.actor_subject_id == request.actor_subject_id
            and context.authority_epoch == request.authority_epoch
        )
        if context.actor_subject_id != context.owner_subject_id:
            reasons.add("contextActorOwnerMismatch")
        if not context_matches:
            reasons.add("ownerVaultAuthorityMismatch")

        generation_accepted = False
        if context_matches and context.actor_subject_id == context.owner_subject_id:
            generation_accepted, generation_reasons = self._observe_runtime_generation(request)
            reasons.update(generation_reasons)

        reasons.update(self._observe_request_replay(request))

        candidate_source = VoiceRoleSelectionCandidateSource.NONE
        fallback_source = VoiceRoleSelectionFallbackSource.DEFAULT_AI
        profile_candidate_eligible = False
        clear_previous_profile = True

        if not context_matches or context.actor_subject_id != context.owner_subject_id:
            fallback_source = VoiceRoleSelectionFallbackSource.TEXT_ONLY
        elif not generation_accepted:
            reasons.add("staleOrConflictingRuntimeRoleSelection")
        else:
            (
                candidate_source,
                fallback_source,
                profile_candidate_eligible,
                clear_previous_profile,
                resolution_reasons,
            ) = self._resolve_candidate(context=context, request=request)
            reasons.update(resolution_reasons)

        return VoiceRoleSelectionResult(
            disposition=VoiceRoleSelectionDisposition.BLOCKED,
            candidate_source=candidate_source,
            fallback_source=fallback_source,
            profile_candidate_eligible=profile_candidate_eligible,
            clear_previous_profile=clear_previous_profile,
            runtime_generation_accepted=generation_accepted,
            reason_codes=tuple(reasons),
        )

    def _observe_request_replay(self, request: VoiceRoleSelectionRequest) -> set[str]:
        key = (request.vault_id, request.request_id)
        with self._lock:
            existing = self._request_hashes.get(key)
            if existing is None:
                self._request_hashes[key] = request.request_hash
                return set()
            if existing == request.request_hash:
                return {"stableRoleVoiceRequestReplayObserved"}
            return {"stableRoleVoiceRequestHashConflict"}

    def _observe_runtime_generation(self, request: VoiceRoleSelectionRequest) -> tuple[bool, set[str]]:
        key = (request.vault_id, request.runtime_id)
        fingerprint = request.selection_fingerprint
        with self._lock:
            existing = self._runtime_generations.get(key)
            if existing is None or request.runtime_generation > existing[0]:
                self._runtime_generations[key] = (request.runtime_generation, fingerprint)
                return True, set()
            if request.runtime_generation < existing[0]:
                return False, {"staleRuntimeGeneration"}
            if fingerprint != existing[1]:
                return False, {"sameGenerationRoleSelectionConflict"}
            return True, {"stableRuntimeGenerationReplayObserved"}

    @staticmethod
    def _resolve_candidate(
        *,
        context: VoiceRoleSelectionAuthorityContext,
        request: VoiceRoleSelectionRequest,
    ) -> tuple[
        VoiceRoleSelectionCandidateSource,
        VoiceRoleSelectionFallbackSource,
        bool,
        bool,
        set[str],
    ]:
        if request.role_kind is VoiceRoleKind.MINOR:
            return (
                VoiceRoleSelectionCandidateSource.NONE,
                VoiceRoleSelectionFallbackSource.TEXT_ONLY,
                False,
                True,
                {"minorRoleVoiceForbidden"},
            )
        if request.role_kind is VoiceRoleKind.MEMORIAL_OR_DECEASED:
            return (
                VoiceRoleSelectionCandidateSource.NONE,
                VoiceRoleSelectionFallbackSource.TEXT_ONLY,
                False,
                True,
                {"memorialOrDeceasedRoleVoiceForbidden"},
            )
        if request.role_kind is VoiceRoleKind.FAMILY_RELATION_ONLY:
            return (
                VoiceRoleSelectionCandidateSource.NONE,
                VoiceRoleSelectionFallbackSource.NEUTRAL_DEFAULT_AI,
                False,
                True,
                {"familyRelationshipNotVoiceGrant"},
            )

        if request.role_kind is VoiceRoleKind.SELF:
            if request.role_subject_id != context.owner_subject_id:
                return (
                    VoiceRoleSelectionCandidateSource.NONE,
                    VoiceRoleSelectionFallbackSource.TEXT_ONLY,
                    False,
                    True,
                    {"selfRoleSubjectMismatch"},
                )
            if request.profile_id is None:
                return (
                    VoiceRoleSelectionCandidateSource.DEFAULT_AI,
                    VoiceRoleSelectionFallbackSource.DEFAULT_AI,
                    False,
                    False,
                    {"selfDefaultAIRequested"},
                )
            profile_reasons = VoiceRoleSelectionShadow._profile_reasons(
                request,
                expected_subject_id=context.owner_subject_id,
                require_published_grant=False,
            )
            if profile_reasons:
                return (
                    VoiceRoleSelectionCandidateSource.DEFAULT_AI,
                    VoiceRoleSelectionFallbackSource.DEFAULT_AI,
                    False,
                    True,
                    profile_reasons,
                )
            return (
                VoiceRoleSelectionCandidateSource.SELF_PROFILE,
                VoiceRoleSelectionFallbackSource.DEFAULT_AI,
                True,
                False,
                {"syntheticSelfProfileCandidateOnly"},
            )

        if request.role_kind is VoiceRoleKind.LIVING_ADULT:
            if request.role_subject_id == context.owner_subject_id:
                return (
                    VoiceRoleSelectionCandidateSource.NONE,
                    VoiceRoleSelectionFallbackSource.DEFAULT_AI,
                    False,
                    True,
                    {"livingAdultRoleMustNotAliasOwner"},
                )
            profile_reasons = VoiceRoleSelectionShadow._profile_reasons(
                request,
                expected_subject_id=request.role_subject_id,
                require_published_grant=True,
            )
            if profile_reasons:
                return (
                    VoiceRoleSelectionCandidateSource.NONE,
                    VoiceRoleSelectionFallbackSource.NEUTRAL_DEFAULT_AI,
                    False,
                    True,
                    profile_reasons,
                )
            return (
                VoiceRoleSelectionCandidateSource.PUBLISHED_LIVING_PROFILE,
                VoiceRoleSelectionFallbackSource.NEUTRAL_DEFAULT_AI,
                True,
                False,
                {"syntheticPublishedLivingProfileCandidateOnly"},
            )

        return (
            VoiceRoleSelectionCandidateSource.NONE,
            VoiceRoleSelectionFallbackSource.TEXT_ONLY,
            False,
            True,
            {"unsupportedRoleVoiceKind"},
        )

    @staticmethod
    def _profile_reasons(
        request: VoiceRoleSelectionRequest,
        *,
        expected_subject_id: str,
        require_published_grant: bool,
    ) -> set[str]:
        reasons: set[str] = set()
        if request.profile_id is None:
            reasons.add("voiceProfileMissing")
            return reasons
        if request.profile_subject_id != expected_subject_id:
            reasons.add("voiceProfileSubjectMismatch")
        if request.profile_state is VoiceProfileResolutionState.REVOKED:
            reasons.add("voiceProfileRevoked")
        elif request.profile_state is VoiceProfileResolutionState.DELETED:
            reasons.add("voiceProfileDeleted")
        elif request.profile_state is not VoiceProfileResolutionState.READY:
            reasons.add("voiceProfileNotReady")
        if not request.independent_consent_observed:
            reasons.add("independentConsentRequired")
        if require_published_grant and not request.published_purpose_grant_observed:
            reasons.add("publishedPurposeGrantRequired")
        if not request.quality_acceptance_observed:
            reasons.add("qualityAcceptanceRequired")
        return reasons


__all__ = [
    "VOICE_ROLE_SELECTION_SHADOW_SCHEMA_VERSION",
    "VoiceProfileResolutionState",
    "VoiceRoleKind",
    "VoiceRoleSelectionAuthorityContext",
    "VoiceRoleSelectionCandidateSource",
    "VoiceRoleSelectionDisposition",
    "VoiceRoleSelectionError",
    "VoiceRoleSelectionFallbackSource",
    "VoiceRoleSelectionRequest",
    "VoiceRoleSelectionResult",
    "VoiceRoleSelectionShadow",
]

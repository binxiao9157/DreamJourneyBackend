"""Fail-closed citation, perspective and uncertainty checks for book artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Mapping, Sequence

from app.domain.narrative.contracts import BookProjectType, NarrativeNarratorType


class FactGuardReason(str, Enum):
    EMPTY_ARTIFACT = "empty_artifact"
    EMPTY_CLAIM = "empty_claim"
    MISSING_MEMORY_REFERENCE = "missing_memory_reference"
    UNKNOWN_MEMORY_REFERENCE = "unknown_memory_reference"
    INACTIVE_MEMORY_REFERENCE = "inactive_memory_reference"
    UNSUPPORTED_DIRECT_QUOTE = "unsupported_direct_quote"
    UNCERTAINTY_UPGRADED = "uncertainty_upgraded"
    TA_FIRST_PERSON_FORBIDDEN = "ta_first_person_forbidden"
    TA_WITNESS_POSITION_MISSING = "ta_witness_position_missing"
    UNSUPPORTED_PSYCHOLOGY_OR_CAUSALITY = "unsupported_psychology_or_causality"


class FactGuardRejected(ValueError):
    def __init__(self, reasons: Sequence[FactGuardReason]) -> None:
        unique = tuple(dict.fromkeys(reasons))
        super().__init__(", ".join(item.value for item in unique))
        self.reasons = unique


@dataclass(frozen=True)
class FactLedgerEntry:
    memory_version_id: str
    content_hash: str
    text: str
    active: bool = True
    supports_direct_quote: bool = False
    supports_psychology_or_causality: bool = False
    uncertain: bool = False


@dataclass(frozen=True)
class NarrativeClaim:
    claim_id: str
    text: str
    memory_version_ids: tuple[str, ...]
    direct_quote: bool = False
    uncertain: bool = False
    psychology_or_causality: bool = False


@dataclass(frozen=True)
class FactGuardResult:
    claim_count: int
    reference_count: int
    coverage: float


_FIRST_PERSON = re.compile(r"(^|[，。！？；：\s])(我|我们|我的|我们的)")
_WITNESS_POSITION = re.compile(r"(在我记忆中|我记得|我亲眼|我看到|我听到|我和|对我来说)")
_DIRECT_QUOTE = re.compile(r"[“”\"‘’]")
_PSYCHOLOGY_OR_CAUSALITY = re.compile(
    r"(内心|心里|他想|她想|TA想|他觉得|她觉得|TA觉得|一定是因为|之所以.+是因为|从此不再|因此决定)"
)


def validate_claims(
    *,
    claims: Iterable[NarrativeClaim],
    ledger: Mapping[str, FactLedgerEntry],
    project_type: BookProjectType,
    narrator_type: NarrativeNarratorType,
) -> FactGuardResult:
    values = tuple(claims)
    reasons: list[FactGuardReason] = []
    references = 0
    if not values:
        reasons.append(FactGuardReason.EMPTY_ARTIFACT)
    for claim in values:
        if not claim.text.strip():
            reasons.append(FactGuardReason.EMPTY_CLAIM)
        if not claim.memory_version_ids:
            reasons.append(FactGuardReason.MISSING_MEMORY_REFERENCE)
        entries: list[FactLedgerEntry] = []
        for memory_version_id in claim.memory_version_ids:
            entry = ledger.get(memory_version_id)
            if entry is None:
                reasons.append(FactGuardReason.UNKNOWN_MEMORY_REFERENCE)
                continue
            if not entry.active:
                reasons.append(FactGuardReason.INACTIVE_MEMORY_REFERENCE)
            entries.append(entry)
            references += 1
        has_direct_quote = claim.direct_quote or bool(_DIRECT_QUOTE.search(claim.text))
        if has_direct_quote and not any(item.supports_direct_quote for item in entries):
            reasons.append(FactGuardReason.UNSUPPORTED_DIRECT_QUOTE)
        if not claim.uncertain and any(item.uncertain for item in entries):
            reasons.append(FactGuardReason.UNCERTAINTY_UPGRADED)
        has_psychology_or_causality = (
            claim.psychology_or_causality
            or bool(_PSYCHOLOGY_OR_CAUSALITY.search(claim.text))
        )
        if has_psychology_or_causality and not any(
            item.supports_psychology_or_causality for item in entries
        ):
            reasons.append(FactGuardReason.UNSUPPORTED_PSYCHOLOGY_OR_CAUSALITY)
        if (
            project_type is BookProjectType.TA_STORY
            and narrator_type is NarrativeNarratorType.THIRD_PERSON_BIOGRAPHY
            and _FIRST_PERSON.search(claim.text)
        ):
            reasons.append(FactGuardReason.TA_FIRST_PERSON_FORBIDDEN)
        if (
            project_type is BookProjectType.TA_STORY
            and narrator_type is NarrativeNarratorType.CONTROLLER_WITNESS
            and _FIRST_PERSON.search(claim.text)
            and not _WITNESS_POSITION.search(claim.text)
        ):
            reasons.append(FactGuardReason.TA_WITNESS_POSITION_MISSING)
    if reasons:
        raise FactGuardRejected(reasons)
    return FactGuardResult(
        claim_count=len(values),
        reference_count=references,
        coverage=1.0 if values else 0.0,
    )


__all__ = [
    "FactGuardReason",
    "FactGuardRejected",
    "FactGuardResult",
    "FactLedgerEntry",
    "NarrativeClaim",
    "validate_claims",
]
